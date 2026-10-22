"""LCAgent 统一模型网关。

业务代码只按 profile_id 调用模型，不直接依赖 DeepSeek、OpenAI
或其他厂商的 SDK。非敏感的模型档案保存在本地 JSON，API Key 只保存
在 Git 已忽略的 .env 中。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
DEFAULT_ENV_PATH = APP_DIR / ".env"
DEFAULT_PROFILES_PATH = APP_DIR / "model_profiles.local.json"
PROFILE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
SUPPORTED_PROVIDERS = {"deepseek", "openai", "openai_compatible"}
DEFAULT_PROVIDER_BASE_URLS = {
    "deepseek": "https://api.deepseek.com",
    "openai": "https://api.openai.com/v1",
}


class ModelGatewayError(RuntimeError):
    """模型网关错误基类。"""


class ModelConfigurationError(ModelGatewayError):
    """模型档案或密钥配置错误。"""


class ModelInvocationError(ModelGatewayError):
    """模型 API 调用失败。"""


def normalize_base_url(provider: str, base_url: str) -> str:
    """规范化并校验模型服务地址，避免 API Key 通过公网明文传输。"""

    normalized = base_url.strip().rstrip("/")
    if not normalized:
        normalized = DEFAULT_PROVIDER_BASE_URLS.get(provider, "")
    if not normalized:
        raise ModelConfigurationError("OpenAI 兼容接口必须填写 Base URL")

    parsed = urlparse(normalized)
    if not parsed.scheme or not parsed.hostname:
        raise ModelConfigurationError("Base URL 必须是完整的 http(s) 地址")
    if parsed.scheme not in {"http", "https"}:
        raise ModelConfigurationError("Base URL 只支持 http 或 https")
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and parsed.hostname not in local_hosts:
        raise ModelConfigurationError("非本机模型服务必须使用 HTTPS")
    return normalized


@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    display_name: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    temperature: float = 0.0
    timeout_seconds: float = 120.0
    max_retries: int = 2
    thinking: bool = False
    reasoning_effort: str = "high"
    context_window_tokens: int = 64000
    compression_trigger_ratio: float = 0.65
    keep_recent_turns: int = 6

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelProfile":
        return cls(
            profile_id=str(data["profile_id"]),
            display_name=str(data["display_name"]),
            provider=str(data["provider"]),
            model=str(data["model"]),
            base_url=str(data.get("base_url") or ""),
            api_key_env=str(data["api_key_env"]),
            temperature=float(data.get("temperature", 0.0)),
            timeout_seconds=float(data.get("timeout_seconds", 120.0)),
            max_retries=int(data.get("max_retries", 2)),
            thinking=bool(data.get("thinking", False)),
            reasoning_effort=str(data.get("reasoning_effort", "high")),
            context_window_tokens=int(data.get("context_window_tokens", 64000)),
            compression_trigger_ratio=float(data.get("compression_trigger_ratio", 0.65)),
            keep_recent_turns=int(data.get("keep_recent_turns", 6)),
        )

    def validate(self) -> None:
        if not PROFILE_ID_PATTERN.fullmatch(self.profile_id):
            raise ModelConfigurationError(
                "profile_id 必须以小写字母开头，且只能包含小写字母、数字、- 和 _"
            )
        if not self.display_name.strip():
            raise ModelConfigurationError("模型显示名称不能为空")
        if self.provider not in SUPPORTED_PROVIDERS:
            raise ModelConfigurationError(f"不支持的模型提供商：{self.provider}")
        normalize_base_url(self.provider, self.base_url)
        if not self.model.strip():
            raise ModelConfigurationError("模型名称不能为空")
        if not self.api_key_env.strip():
            raise ModelConfigurationError("API Key 环境变量名不能为空")
        if self.timeout_seconds <= 0:
            raise ModelConfigurationError("超时时间必须大于 0")
        if self.max_retries < 0:
            raise ModelConfigurationError("重试次数不能小于 0")
        if self.context_window_tokens < 4096:
            raise ModelConfigurationError("上下文窗口不能小于 4096 Token")
        if not 0.2 <= self.compression_trigger_ratio <= 0.9:
            raise ModelConfigurationError("自动压缩阈值必须在 20% 到 90% 之间")
        if not 1 <= self.keep_recent_turns <= 50:
            raise ModelConfigurationError("保留最近轮数必须在 1 到 50 之间")


@dataclass(frozen=True)
class ModelReply:
    content: str
    profile_id: str
    provider: str
    model: str
    usage: Mapping[str, Any]
    response_metadata: Mapping[str, Any]


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _write_env_value(path: Path, key: str, value: str | None) -> None:
    """只更新指定密钥，保留 .env 中其他模型和 Embedding 配置。"""

    existing_lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated_lines: list[str] = []
    replaced = False
    for line in existing_lines:
        if line.strip().startswith(f"{key}="):
            if value is not None:
                updated_lines.append(f"{key}={value}")
            replaced = True
        else:
            updated_lines.append(line)
    if not replaced and value is not None:
        if updated_lines and updated_lines[-1].strip():
            updated_lines.append("")
        updated_lines.append(f"{key}={value}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(updated_lines).rstrip() + "\n", encoding="utf-8")


class ModelProfileStore:
    def __init__(
        self,
        profiles_path: Path | str = DEFAULT_PROFILES_PATH,
        env_path: Path | str = DEFAULT_ENV_PATH,
    ) -> None:
        self.profiles_path = Path(profiles_path)
        self.env_path = Path(env_path)
        load_dotenv(self.env_path, override=True)

    def _load_document(self) -> dict[str, Any]:
        if not self.profiles_path.exists():
            return {"schema_version": 1, "default_profile_id": None, "profiles": []}
        try:
            document = json.loads(self.profiles_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ModelConfigurationError(f"无法读取模型配置：{exc}") from exc
        if not isinstance(document, dict) or not isinstance(document.get("profiles"), list):
            raise ModelConfigurationError("模型配置文件格式错误")
        return document

    def _save_document(self, document: Mapping[str, Any]) -> None:
        self.profiles_path.parent.mkdir(parents=True, exist_ok=True)
        self.profiles_path.write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def ensure_legacy_profile(self) -> None:
        """首次启动时将旧 LLM_* 配置映射成默认档案，不复制密钥。"""

        document = self._load_document()
        if document["profiles"]:
            return
        env_values = _read_env_file(self.env_path)
        api_key = env_values.get("LLM_API_KEY") or env_values.get("DEEPSEEK_API_KEY")
        if not api_key:
            return
        base_url = env_values.get("LLM_BASE_URL", "https://api.deepseek.com")
        provider = "deepseek" if "deepseek" in base_url.lower() else "openai_compatible"
        api_key_env = "LLM_API_KEY" if env_values.get("LLM_API_KEY") else "DEEPSEEK_API_KEY"
        profile = ModelProfile(
            profile_id="legacy-default",
            display_name="现有默认模型",
            provider=provider,
            model=env_values.get("LLM_MODEL") or env_values.get("DEEPSEEK_MODEL", "deepseek-flash"),
            base_url=base_url,
            api_key_env=api_key_env,
        )
        self.upsert(profile, set_default=True)

    def list_profiles(self) -> list[ModelProfile]:
        self.ensure_legacy_profile()
        profiles = [ModelProfile.from_dict(item) for item in self._load_document()["profiles"]]
        for profile in profiles:
            profile.validate()
        return sorted(profiles, key=lambda item: item.display_name.casefold())

    def get(self, profile_id: str | None = None) -> ModelProfile:
        document = self._load_document()
        selected_id = profile_id or document.get("default_profile_id")
        if not selected_id:
            self.ensure_legacy_profile()
            document = self._load_document()
            selected_id = document.get("default_profile_id")
        for item in document["profiles"]:
            if item.get("profile_id") == selected_id:
                profile = ModelProfile.from_dict(item)
                profile.validate()
                return profile
        raise ModelConfigurationError("还没有可用的模型档案，请先在 UI 中添加模型")

    def default_profile_id(self) -> str | None:
        self.ensure_legacy_profile()
        return self._load_document().get("default_profile_id")

    def upsert(
        self,
        profile: ModelProfile,
        *,
        api_key: str | None = None,
        set_default: bool = False,
    ) -> None:
        profile.validate()
        document = self._load_document()
        items = [item for item in document["profiles"] if item.get("profile_id") != profile.profile_id]
        items.append(asdict(profile))
        document["profiles"] = sorted(items, key=lambda item: item["profile_id"])
        if set_default or not document.get("default_profile_id"):
            document["default_profile_id"] = profile.profile_id
        self._save_document(document)
        if api_key is not None and api_key.strip():
            _write_env_value(self.env_path, profile.api_key_env, api_key.strip())
            os.environ[profile.api_key_env] = api_key.strip()

    def set_default(self, profile_id: str) -> None:
        self.get(profile_id)
        document = self._load_document()
        document["default_profile_id"] = profile_id
        self._save_document(document)

    def delete(self, profile_id: str, *, delete_secret: bool = False) -> None:
        document = self._load_document()
        removed = [item for item in document["profiles"] if item.get("profile_id") == profile_id]
        if not removed:
            raise ModelConfigurationError(f"模型档案不存在：{profile_id}")
        document["profiles"] = [
            item for item in document["profiles"] if item.get("profile_id") != profile_id
        ]
        if document.get("default_profile_id") == profile_id:
            document["default_profile_id"] = (
                document["profiles"][0]["profile_id"] if document["profiles"] else None
            )
        self._save_document(document)
        if delete_secret:
            api_key_env = str(removed[0]["api_key_env"])
            # 多个档案可以显式共用一个密钥变量，仍被引用时不能误删。
            secret_is_shared = any(
                str(item.get("api_key_env")) == api_key_env
                for item in document["profiles"]
            )
            if not secret_is_shared:
                _write_env_value(self.env_path, api_key_env, None)
                os.environ.pop(api_key_env, None)

    def has_secret(self, profile: ModelProfile) -> bool:
        load_dotenv(self.env_path, override=True)
        return bool(os.environ.get(profile.api_key_env))


class ModelGateway:
    def __init__(
        self,
        store: ModelProfileStore | None = None,
        model_factory: Callable[[ModelProfile, str], Any] | None = None,
    ) -> None:
        self.store = store or ModelProfileStore()
        self.model_factory = model_factory or self._build_langchain_model

    def _get_api_key(self, profile: ModelProfile) -> str:
        load_dotenv(self.store.env_path, override=True)
        api_key = os.environ.get(profile.api_key_env, "").strip()
        if not api_key:
            raise ModelConfigurationError(
                f"模型档案 {profile.display_name} 缺少 {profile.api_key_env}"
            )
        return api_key

    @staticmethod
    def discover_models(
        provider: str,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 30.0,
        client_factory: Callable[..., Any] | None = None,
    ) -> list[str]:
        """从 OpenAI 兼容的 ``/models`` 接口读取可用模型。

        LangChain 负责后续对话调用；模型发现属于厂商管理接口，因此直接
        使用 OpenAI 兼容客户端。部分厂商未实现该接口时，UI 会保留手工输入。
        """

        if provider not in SUPPORTED_PROVIDERS:
            raise ModelConfigurationError(f"不支持的模型提供商：{provider}")
        secret = api_key.strip()
        if not secret:
            raise ModelConfigurationError("获取模型列表前必须填写 API Key")
        if timeout_seconds <= 0:
            raise ModelConfigurationError("超时时间必须大于 0")
        normalized_url = normalize_base_url(provider, base_url)

        if client_factory is None:
            try:
                from openai import OpenAI
            except ImportError as exc:
                raise ModelConfigurationError(
                    "缺少 openai 客户端，请重新安装 requirements.txt"
                ) from exc
            client_factory = OpenAI

        try:
            client = client_factory(
                api_key=secret,
                base_url=normalized_url,
                timeout=timeout_seconds,
            )
            response = client.models.list()
            raw_models = (
                response.get("data", [])
                if isinstance(response, Mapping)
                else getattr(response, "data", [])
            )
            model_ids: set[str] = set()
            for item in raw_models:
                model_id = (
                    item.get("id")
                    if isinstance(item, Mapping)
                    else getattr(item, "id", None)
                )
                if model_id and str(model_id).strip():
                    model_ids.add(str(model_id).strip())
        except ModelGatewayError:
            raise
        except Exception as exc:
            # 极少数 SDK 异常可能回显请求信息，至少确保不会把完整密钥带到 UI。
            safe_message = str(exc).replace(secret, "***")
            raise ModelInvocationError(f"获取模型列表失败：{safe_message}") from exc

        if not model_ids:
            raise ModelInvocationError(
                "接口没有返回可选模型；该厂商可能不支持 /models，请手工填写模型名称"
            )
        return sorted(model_ids, key=str.casefold)

    @staticmethod
    def _build_langchain_model(profile: ModelProfile, api_key: str) -> Any:
        common: dict[str, Any] = {
            "model": profile.model,
            "api_key": api_key,
            "temperature": profile.temperature,
            "timeout": profile.timeout_seconds,
            "max_retries": profile.max_retries,
        }
        common["base_url"] = normalize_base_url(profile.provider, profile.base_url)

        if profile.provider == "deepseek":
            try:
                from langchain_deepseek import ChatDeepSeek
            except ImportError as exc:
                raise ModelConfigurationError(
                    "缺少 langchain-deepseek，请重新安装 requirements.txt"
                ) from exc
            # DeepSeek 的思考开关与普通 temperature 不是同一概念，在网关中统一封装。
            common["reasoning_effort"] = profile.reasoning_effort
            common["extra_body"] = {
                "thinking": {"type": "enabled" if profile.thinking else "disabled"}
            }
            return ChatDeepSeek(**common)

        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise ModelConfigurationError(
                "缺少 langchain-openai，请重新安装 requirements.txt"
            ) from exc
        return ChatOpenAI(**common)

    @staticmethod
    def _convert_messages(messages: Sequence[Mapping[str, Any]]) -> list[Any]:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

        converted: list[Any] = []
        for message in messages:
            role = str(message.get("role", "user"))
            content = message.get("content", "")
            if role == "system":
                converted.append(SystemMessage(content=content))
            elif role == "assistant":
                converted.append(AIMessage(content=content))
            else:
                converted.append(HumanMessage(content=content))
        return converted

    def chat(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        profile_id: str | None = None,
    ) -> ModelReply:
        profile = self.store.get(profile_id)
        api_key = self._get_api_key(profile)
        try:
            model = self.model_factory(profile, api_key)
            response = model.invoke(self._convert_messages(messages))
        except ModelGatewayError:
            raise
        except Exception as exc:
            raise ModelInvocationError(
                f"调用 {profile.display_name} ({profile.model}) 失败：{exc}"
            ) from exc

        content = response.content
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        return ModelReply(
            content=content,
            profile_id=profile.profile_id,
            provider=profile.provider,
            model=profile.model,
            usage=dict(getattr(response, "usage_metadata", None) or {}),
            response_metadata=dict(getattr(response, "response_metadata", None) or {}),
        )

    def test_connection(self, profile_id: str) -> ModelReply:
        return self.chat(
            [
                {"role": "system", "content": "You are a connection test. Keep the answer short."},
                {"role": "user", "content": "Reply with OK."},
            ],
            profile_id=profile_id,
        )


def mask_secret(value: str) -> str:
    if not value:
        return "未配置"
    if len(value) <= 8:
        return "已配置"
    return f"{value[:4]}...{value[-4:]}"
