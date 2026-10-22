"""独立的 Embedding 模型网关。

对话模型和向量模型具有不同的职责、参数与生命周期，因此不复用
``ModelProfile``。业务代码只依赖本模块提供的 ``embed_*`` 接口。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from dotenv import load_dotenv

from model_gateway import (
    APP_DIR,
    DEFAULT_ENV_PATH,
    ModelConfigurationError,
    ModelInvocationError,
    normalize_base_url,
    _write_env_value,
)


DEFAULT_EMBEDDING_PROFILES_PATH = APP_DIR / "embedding_profiles.local.json"
EMBEDDING_PROFILE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SUPPORTED_EMBEDDING_PROVIDERS = {"openai", "openai_compatible"}


@dataclass(frozen=True)
class EmbeddingProfile:
    profile_id: str
    display_name: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    dimensions: int | None = None
    batch_size: int = 32
    timeout_seconds: float = 120.0
    max_retries: int = 2

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EmbeddingProfile":
        dimensions = data.get("dimensions")
        return cls(
            profile_id=str(data["profile_id"]),
            display_name=str(data["display_name"]),
            provider=str(data["provider"]),
            model=str(data["model"]),
            base_url=str(data.get("base_url") or ""),
            api_key_env=str(data["api_key_env"]),
            dimensions=int(dimensions) if dimensions not in {None, ""} else None,
            batch_size=int(data.get("batch_size", 32)),
            timeout_seconds=float(data.get("timeout_seconds", 120.0)),
            max_retries=int(data.get("max_retries", 2)),
        )

    def validate(self) -> None:
        if not EMBEDDING_PROFILE_ID_PATTERN.fullmatch(self.profile_id):
            raise ModelConfigurationError(
                "Embedding Profile ID 必须以小写字母开头，且只能包含小写字母、数字、- 和 _"
            )
        if not self.display_name.strip():
            raise ModelConfigurationError("Embedding 模型显示名称不能为空")
        if self.provider not in SUPPORTED_EMBEDDING_PROVIDERS:
            raise ModelConfigurationError(f"不支持的 Embedding 提供商：{self.provider}")
        normalize_base_url(self.provider, self.base_url)
        if not self.model.strip():
            raise ModelConfigurationError("Embedding 模型名称不能为空")
        if not self.api_key_env.strip():
            raise ModelConfigurationError("Embedding API Key 环境变量名不能为空")
        if self.dimensions is not None and self.dimensions <= 0:
            raise ModelConfigurationError("Embedding 维度必须大于 0")
        if not 1 <= self.batch_size <= 2048:
            raise ModelConfigurationError("Embedding 批大小必须在 1 到 2048 之间")
        if self.timeout_seconds <= 0 or self.max_retries < 0:
            raise ModelConfigurationError("Embedding 超时和重试参数无效")


class EmbeddingProfileStore:
    def __init__(
        self,
        profiles_path: Path | str = DEFAULT_EMBEDDING_PROFILES_PATH,
        env_path: Path | str = DEFAULT_ENV_PATH,
    ) -> None:
        self.profiles_path = Path(profiles_path)
        self.env_path = Path(env_path)
        load_dotenv(self.env_path, override=True)

    def _load(self) -> dict[str, Any]:
        if not self.profiles_path.exists():
            return {"schema_version": 1, "default_profile_id": None, "profiles": []}
        try:
            data = json.loads(self.profiles_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelConfigurationError(f"无法读取 Embedding 配置：{exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("profiles"), list):
            raise ModelConfigurationError("Embedding 配置文件格式错误")
        return data

    def _save(self, data: Mapping[str, Any]) -> None:
        self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
        self.profiles_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def list_profiles(self) -> list[EmbeddingProfile]:
        profiles = [EmbeddingProfile.from_dict(item) for item in self._load()["profiles"]]
        for profile in profiles:
            profile.validate()
        return sorted(profiles, key=lambda item: item.display_name.casefold())

    def get(self, profile_id: str | None = None) -> EmbeddingProfile:
        data = self._load()
        selected = profile_id or data.get("default_profile_id")
        for item in data["profiles"]:
            if item.get("profile_id") == selected:
                profile = EmbeddingProfile.from_dict(item)
                profile.validate()
                return profile
        raise ModelConfigurationError("还没有可用的 Embedding 模型，请先在 UI 中添加")

    def default_profile_id(self) -> str | None:
        return self._load().get("default_profile_id")

    def upsert(
        self,
        profile: EmbeddingProfile,
        *,
        api_key: str | None = None,
        set_default: bool = False,
    ) -> None:
        profile.validate()
        data = self._load()
        items = [item for item in data["profiles"] if item.get("profile_id") != profile.profile_id]
        items.append(asdict(profile))
        data["profiles"] = sorted(items, key=lambda item: item["profile_id"])
        if set_default or not data.get("default_profile_id"):
            data["default_profile_id"] = profile.profile_id
        self._save(data)
        if api_key is not None and api_key.strip():
            _write_env_value(self.env_path, profile.api_key_env, api_key.strip())
            os.environ[profile.api_key_env] = api_key.strip()

    def delete(self, profile_id: str, *, delete_secret: bool = False) -> None:
        data = self._load()
        removed = [item for item in data["profiles"] if item.get("profile_id") == profile_id]
        if not removed:
            raise ModelConfigurationError(f"Embedding 档案不存在：{profile_id}")
        data["profiles"] = [item for item in data["profiles"] if item.get("profile_id") != profile_id]
        if data.get("default_profile_id") == profile_id:
            data["default_profile_id"] = (
                data["profiles"][0]["profile_id"] if data["profiles"] else None
            )
        self._save(data)
        if delete_secret:
            key_name = str(removed[0]["api_key_env"])
            if not any(item.get("api_key_env") == key_name for item in data["profiles"]):
                _write_env_value(self.env_path, key_name, None)
                os.environ.pop(key_name, None)

    def has_secret(self, profile: EmbeddingProfile) -> bool:
        load_dotenv(self.env_path, override=True)
        return bool(os.environ.get(profile.api_key_env, "").strip())


class EmbeddingGateway:
    """OpenAI 兼容 Embedding 网关，支持依赖注入以便离线测试。"""

    def __init__(
        self,
        store: EmbeddingProfileStore | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.store = store or EmbeddingProfileStore()
        self.client_factory = client_factory

    def _client(self, profile: EmbeddingProfile) -> Any:
        load_dotenv(self.store.env_path, override=True)
        secret = os.environ.get(profile.api_key_env, "").strip()
        if not secret:
            raise ModelConfigurationError(
                f"Embedding 档案 {profile.display_name} 缺少 {profile.api_key_env}"
            )
        factory = self.client_factory
        if factory is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ModelConfigurationError("缺少 openai 客户端") from exc
            factory = OpenAI
        return factory(
            api_key=secret,
            base_url=normalize_base_url(profile.provider, profile.base_url),
            timeout=profile.timeout_seconds,
            max_retries=profile.max_retries,
        )

    def embed_texts(
        self,
        texts: Sequence[str],
        *,
        profile_id: str | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ModelConfigurationError("Embedding 输入必须是非空字符串")
        profile = self.store.get(profile_id)
        client = self._client(profile)
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), profile.batch_size):
                batch = list(texts[start : start + profile.batch_size])
                request: dict[str, Any] = {"model": profile.model, "input": batch}
                if profile.dimensions is not None:
                    request["dimensions"] = profile.dimensions
                response = client.embeddings.create(**request)
                raw_data = response.get("data", []) if isinstance(response, Mapping) else response.data
                ordered = sorted(
                    raw_data,
                    key=lambda item: item.get("index", 0)
                    if isinstance(item, Mapping)
                    else getattr(item, "index", 0),
                )
                for item in ordered:
                    embedding = item.get("embedding") if isinstance(item, Mapping) else item.embedding
                    vectors.append([float(value) for value in embedding])
        except (ModelConfigurationError, ModelInvocationError):
            raise
        except Exception as exc:
            safe_message = str(exc)
            load_dotenv(self.store.env_path, override=True)
            secret = os.environ.get(profile.api_key_env, "").strip()
            if secret:
                safe_message = safe_message.replace(secret, "***")
            raise ModelInvocationError(
                f"调用 Embedding 模型 {profile.display_name} 失败：{safe_message}"
            ) from exc
        if len(vectors) != len(texts):
            raise ModelInvocationError(
                f"Embedding 返回数量异常：期望 {len(texts)}，实际 {len(vectors)}"
            )
        return vectors

    def embed_query(self, text: str, *, profile_id: str | None = None) -> list[float]:
        return self.embed_texts([text], profile_id=profile_id)[0]
