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
    database_url: str = "postgresql+asyncpg://solochef:solochef_password@localhost:5433/solochef"
    db_pool_size: int = 20
    db_max_overflow: int = 20
    db_pool_timeout: int = 30
    db_pool_recycle: int = 1800
    redis_url: str = "redis://localhost:6379/0"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "solochef_password"
    milvus_host: str = "localhost"
    milvus_port: int = 19530
    milvus_collection: str = "solochef_knowledge"
    milvus_user: str = ""
    milvus_password: str = ""
    rag_enabled: bool = True
    rag_top_k: int = 4
    chunk_size: int = 600
    chunk_overlap: int = 100
    # 应用启动时是否自动执行知识库 bootstrap（遍历 knowledge_docs/ 幂等入库）。
    # 依赖 Milvus/Neo4j 已启动；基础服务不可达时降级跳过，不阻断启动。
    auto_bootstrap_knowledge: bool = True

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

    # ── 稀疏向量检索（BGE-M3 lexical weights 可选增强）────────────
    # 开启后优先经 FlagEmbedding BGEM3FlagModel 同时产出稠密+稀疏向量，
    # Milvus 双路召回（COSINE 稠密 + IP 稀疏）RRF 融合；模型/依赖缺失时
    # 自动降级为纯稠密检索，链路不中断。
    sparse_enabled: bool = True
    # RRF 融合参数 k：score = Σ 1/(k + rank)，k 越大两路排名权重越平均
    sparse_rrf_k: int = 60
    llm_provider: str = "demo"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_timeout_seconds: float = 60.0
    plan_generation_timeout_seconds: float = 45.0
    ai_fallback_enabled: bool = True
    # 领域智能体默认使用确定性规则，避免与主规划器串联多次外部模型等待。
    domain_agents_llm_enabled: bool = False
    # Agent capabilities are opt-in; the planner graph itself is always enabled.
    workflow_supervisor_enabled: bool = False
    supervisor_max_rounds: int = 2
    supervisor_max_dispatches_per_round: int = 4
    supervisor_max_total_dispatches: int = 6
    domain_agent_max_iterations: int = 3
    domain_agent_max_tool_calls: int = 4
    domain_agent_tool_timeout_seconds: float = 8.0
    chat_agent_enabled: bool = False
    chat_agent_max_iterations: int = 6
    chat_agent_tool_timeout_seconds: float = 8.0
    tool_websearch_enabled: bool = False
    tool_websearch_provider: str = "tavily"
    tool_websearch_api_key: str = ""
    tool_websearch_timeout_seconds: float = 8.0
    checkpoint_backend: str = "memory"
    checkpoint_redis_url: str = ""
    checkpoint_postgres_url: str = ""
    checkpoint_ttl_seconds: int = 86_400
    checkpoint_retention_days: int = 7
    # 图谱实体抽取是增强功能。默认使用本地正则抽取，避免启动期因外部 LLM
    # 响应格式或网络问题阻塞 API；需要更丰富关系时可显式设为 true。
    entity_extraction_llm_enabled: bool = False

    # ── 多模态视觉模型（Qwen-VL 可选增强）─────────────────────────
    vlm_enabled: bool = False
    vlm_api_key: str = ""
    vlm_model: str = "qwen-vl-max"
    vlm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    vlm_timeout_seconds: float = 90.0
    vlm_max_image_size: int = 5  # 单张图片最大 MB
    vlm_max_image_dimension: int = 2048  # 长边像素上限
    vlm_rate_limit_per_minute: int = 10
    # P3 Planner 分段生成：meals → shopping → budget 三阶段独立 LLM 调用。
    # 默认关闭——当前 Verifier 兜底已够用；启用后每阶段聚焦小 prompt 提升质量，
    # 任一阶段失败自动回退到单次生成模式（向后兼容）。
    planner_segmented_enabled: bool = False
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
    def vlm_enabled_real(self) -> bool:
        return self.vlm_enabled and bool(self.vlm_api_key)

    @property
    def jwt_uses_development_secret(self) -> bool:
        return self.jwt_secret_key == "dev-only-change-this-secret-key-now"


@lru_cache
def get_settings() -> Settings:
    return Settings()
