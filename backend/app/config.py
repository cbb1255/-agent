"""Personal knowledge base settings, anchored to the project directory."""
from functools import lru_cache
import math
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录（app/config.py 的上一级），保证任何工作目录下运行都能找到文件
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def normalize_host(value: str) -> str:
    """Parse a configured or requested host without trusting userinfo or paths."""
    if not isinstance(value, str) or not value or any(character.isspace() or ord(character) > 127 for character in value):
        raise ValueError("访问主机名无效")
    parsed = urlsplit("//" + value)
    if parsed.username is not None or parsed.password is not None or parsed.path or parsed.query or parsed.fragment:
        raise ValueError("访问主机名无效")
    if not parsed.hostname or any(character in value for character in (",", "\\", "*")):
        raise ValueError("访问主机名无效")
    parsed.port  # Reject malformed or out-of-range ports.
    return parsed.hostname.lower()


def normalize_origin(value: str) -> str:
    """Return a concrete HTTP origin for request and configuration comparisons."""
    if not isinstance(value, str) or not value or any(character.isspace() or ord(character) > 127 for character in value):
        raise ValueError("前端来源地址无效")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("前端来源须为包含协议和端口的 HTTP 地址")
    hostname = normalize_host(parsed.netloc)
    host = f"[{hostname}]" if ":" in hostname else hostname
    port = parsed.port
    if port is not None and port != (443 if parsed.scheme == "https" else 80):
        host += f":{port}"
    return f"{parsed.scheme}://{host}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_name: str = "个人知识库"
    access_token: str = Field(default="", repr=False, exclude=True)
    allowed_hosts: list[str] = ["127.0.0.1", "localhost", "[::1]"]
    allowed_origins: list[str] = []
    deepseek_api_key: str = Field(default="", repr=False, exclude=True)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    llm_timeout: float = 60.0
    llm_max_retries: int = 1
    enable_knowledge_fallback: bool = True
    web_search_mcp_config: str = ""
    web_search_mcp_server: str = "exa"
    web_search_timeout: float = 20.0
    web_search_max_results: int = 5

    # 嵌入 / 重排
    embed_model: str = "BAAI/bge-small-zh-v1.5"
    rerank_model: str = "BAAI/bge-reranker-base"
    enable_rerank: bool = False

    # RAG 参数
    chunk_size: int = 500
    chunk_overlap: int = 80
    chunk_strategy: Literal["window", "paragraph"] = "window"
    exclude_boilerplate: bool = False
    recall_top_k: int = 20
    rerank_top_n: int = 5
    min_score: float = 0.5  # 500份固定公开语料校准的向量下限
    rerank_min_score: float = 0.4  # 开了重排时卡重排分，与向量门槛分开校准
    rerank_score_margin: float | None = None
    retrieval_dedup_overlap: float = 0.0
    evidence_rerank_pool_size: int | None = None
    bm25_exempt_rank: int = 0  # 默认关闭绕过向量下限的 BM25 放行
    prewarm_models: bool = True  # 启动时预加载嵌入/重排模型，避免首个问题冷启动等待

    raw_dir: str = str(PROJECT_ROOT / "data" / "knowledge")
    index_dir: str = str(PROJECT_ROOT / "storage" / "indexes")
    collection_name: str = "knowledge"
    max_document_bytes: int = 500 * 1024 * 1024
    max_query_chars: int = 4000

    @field_validator("deepseek_api_key")
    @classmethod
    def normalize_deepseek_api_key(cls, value: str) -> str:
        value = value.strip()
        return "" if value == "your_deepseek_api_key" else value

    @field_validator("access_token")
    @classmethod
    def validate_access_token(cls, value: str) -> str:
        if value and (len(value) < 32 or any(not 33 <= ord(character) <= 126 for character in value)):
            raise ValueError("ACCESS_TOKEN 须至少包含32个可打印ASCII字符且不含空白")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, values: list[str]) -> list[str]:
        if not values:
            raise ValueError("ALLOWED_HOSTS 至少包含一个主机名")
        return list(dict.fromkeys(normalize_host(value) for value in values))

    @field_validator("allowed_origins")
    @classmethod
    def validate_allowed_origins(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(normalize_origin(value) for value in values))

    @field_validator("raw_dir", "index_dir")
    @classmethod
    def anchor_path(cls, value: str) -> str:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return str(path.resolve())

    @field_validator("web_search_mcp_config")
    @classmethod
    def anchor_search_config(cls, value: str) -> str:
        if not value.strip():
            return ""
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return str(path.resolve())

    @model_validator(mode="after")
    def validate_limits(self):
        if self.chunk_size <= 0 or not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("CHUNK_OVERLAP 必须大于等于 0 且小于 CHUNK_SIZE")
        if min(self.recall_top_k, self.rerank_top_n, self.max_document_bytes, self.max_query_chars) <= 0:
            raise ValueError("检索条数和请求大小上限必须为正数")
        if self.llm_timeout <= 0 or self.llm_max_retries < 0:
            raise ValueError("模型超时必须为正数，重试次数必须大于等于 0")
        if not math.isfinite(self.web_search_timeout) or self.web_search_timeout <= 0:
            raise ValueError("网页搜索超时必须为有效正数")
        if self.web_search_max_results <= 0 or not self.web_search_mcp_server.strip():
            raise ValueError("网页搜索条数必须为正数，MCP 服务名须非空")
        if not math.isfinite(self.min_score) or self.min_score <= 0:
            raise ValueError("向量相关度下限必须为正数")
        if not math.isfinite(self.rerank_min_score) or self.rerank_min_score <= 0:
            raise ValueError("重排相关度下限必须为正数")
        if self.bm25_exempt_rank < 0:
            raise ValueError("BM25 放行名次必须大于等于 0")
        if self.rerank_score_margin is not None and (not math.isfinite(self.rerank_score_margin) or self.rerank_score_margin < 0):
            raise ValueError("重排分数差须为有限的非负数")
        if not math.isfinite(self.retrieval_dedup_overlap) or not 0 <= self.retrieval_dedup_overlap <= 1:
            raise ValueError("来源重叠去重门槛须在0到1之间")
        if self.evidence_rerank_pool_size is not None and self.evidence_rerank_pool_size < self.rerank_top_n:
            raise ValueError("证据筛选候选池须大于等于最终返回条数")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
