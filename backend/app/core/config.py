from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = PROJECT_ROOT / ".env"


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    app_name: str = "SoloChef API"
    app_env: str = "development"
    api_prefix: str = "/api/v1"
    database_url: str = "mysql+aiomysql://solochef:solochef_password@localhost:3306/solochef"
    redis_url: str = "redis://localhost:6379/0"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "solochef_password"
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection: str = "solochef_knowledge"
    chroma_ssl: bool = False
    rag_enabled: bool = True
    rag_top_k: int = 4
    chunk_size: int = 600
    chunk_overlap: int = 100

    # ── 语义向量模型（BGE-M3 可选增强）─────────────────────────────
    embedding_provider: str = "auto"  # auto | bge-m3 | default
    embedding_model: str = "BAAI/bge-m3"
    embedding_model_path: str = ""
    embedding_device: str = "cpu"

    # ── 检索重排（bge-reranker-v2-m3 可选增强）────────────────────
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_model_path: str = ""
    rerank_device: str = "cpu"
    # 二阶段精排候选倍数：首阶段召回 top_k * multiplier，再用 reranker 精排回 top_k
    rerank_candidate_multiplier: int = 3
    llm_provider: str = "demo"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_timeout_seconds: float = 60.0
    ai_fallback_enabled: bool = True
    jwt_secret_key: str = "dev-only-change-this-secret-key-now"
    jwt_algorithm: str = "HS256"
    jwt_issuer: str = "solochef-api"
    access_token_minutes: int = 30
    refresh_token_days: int = 7
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # ── 阿里云短信服务 ──────────────────────────────────────────────
    sms_access_key_id: str = ""
    sms_access_key_secret: str = ""
    sms_sign_name: str = "恒创联众"
    sms_template_login: str = "100001"
    sms_template_change_phone: str = "100002"
    sms_template_reset_password: str = "100003"
    sms_template_bind_phone: str = "100004"
    sms_template_verify_phone: str = "100005"
    sms_code_expire_minutes: int = 5
    sms_send_interval_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def real_llm_enabled(self) -> bool:
        return self.llm_provider != "demo" and bool(self.llm_api_key)

    @property
    def jwt_uses_development_secret(self) -> bool:
        return self.jwt_secret_key == "dev-only-change-this-secret-key-now"


@lru_cache
def get_settings() -> Settings:
    return Settings()
