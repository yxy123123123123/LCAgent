import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent / ".env")


def _env(*names, default=None, required=False):
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    if required:
        joined_names = " or ".join(names)
        raise RuntimeError(f"Missing required environment variable: {joined_names}")
    return default


# Chat model API. Prefer the generic LLM_* names; legacy DEEPSEEK_* names still work.
LLM_API_KEY = _env("LLM_API_KEY", "DEEPSEEK_API_KEY", required=True)
LLM_BASE_URL = _env(
    "LLM_BASE_URL",
    "DEEPSEEK_BASE_URL",
    default="https://api.deepseek.com",
)
LLM_MODEL = _env("LLM_MODEL", "DEEPSEEK_MODEL", default="deepseek-chat")

# Embedding model API. Prefer EMBEDDING_* names; legacy DASHSCOPE_* names still work.
EMBEDDING_API_KEY = _env("EMBEDDING_API_KEY", "DASHSCOPE_API_KEY", required=True)
EMBEDDING_BASE_URL = _env(
    "EMBEDDING_BASE_URL",
    "DASHSCOPE_BASE_URL",
    default="https://dashscope.aliyuncs.com/compatible-mode/v1",
)
EMBEDDING_MODEL = _env(
    "EMBEDDING_MODEL",
    "DASHSCOPE_EMBED_MODEL",
    default="text-embedding-v4",
)

# Backward-compatible aliases for existing imports and older local scripts.
DEEPSEEK_API_KEY = LLM_API_KEY
DEEPSEEK_BASE_URL = LLM_BASE_URL
DEEPSEEK_MODEL = LLM_MODEL
DASHSCOPE_API_KEY = EMBEDDING_API_KEY
DASHSCOPE_BASE_URL = EMBEDDING_BASE_URL
DASHSCOPE_EMBED_MODEL = EMBEDDING_MODEL
