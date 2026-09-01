# =============================================================================
# SoloChef Backend Dockerfile (FastAPI + Uvicorn)
# 基础镜像: python:3.12-slim
#
# 安装 ai + rerank 两个可选依赖组:
#   ai     → LangChain/LangGraph/Milvus/Neo4j/sentence-transformers
#   rerank → FlagEmbedding (BGE-M3 稀疏检索 + bge-reranker-v2-m3 精排)
# =============================================================================
FROM python:3.12-slim

WORKDIR /app

# 国内服务器(阿里云 ECS)构建加速，可用 --build-arg 覆盖：
#   docker compose build --build-arg PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
# EMBEDDING_DEVICE/RERANK_DEVICE 均为 cpu，安装 CPU 版 torch 避免拉入
# 数 GB 的 CUDA 依赖（有 GPU 的服务器可改为 https://download.pytorch.org/whl/cu124）
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

# ── 系统编译依赖 ──
# gcc: 编译 psycopg/bcrypt 等 C 扩展
# libpq-dev: PostgreSQL 客户端头文件 (psycopg/asyncpg)
# libffi-dev: cffi (cryptography 依赖)
# curl: 健康检查探针
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libffi-dev \
        curl \
    && rm -rf /var/lib/apt/lists/*

# ── 依赖层（利用 Docker 层缓存）──────────────────────────────────────
# 先单独装 CPU 版 torch（约 200MB），再装项目依赖；代码变更时不会重装。
RUN pip install --no-cache-dir torch --index-url ${TORCH_INDEX_URL}

# 用空壳 app 包占位让 pip 可构建项目元数据并安装全部依赖，
# 真实代码在下一层 COPY —— 改业务代码不再触发 torch 等大包重装。
COPY pyproject.toml requirements.docker.txt ./
RUN mkdir -p app && touch app/__init__.py \
    && pip install --no-cache-dir -i ${PIP_INDEX_URL} -r requirements.docker.txt \
    && rm -rf app

# ── 应用代码（变更只重建此层）────────────────────────────────────────
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

EXPOSE 8000

# ── 启动命令：先执行数据库迁移，再启动 Uvicorn ──
# 应用 lifespan 内另有 create_all 兜底，二者均幂等
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
