# =============================================================================
# SoloChef Backend Dockerfile (FastAPI + Uvicorn)
# 基础镜像: python:3.12-slim
# =============================================================================
FROM python:3.12-slim

WORKDIR /app

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

# ── 先复制依赖清单，利用 Docker 层缓存 ──
COPY pyproject.toml ./
COPY requirements.txt ./
COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

# ── 安装 Python 依赖（包含 ai 可选组：LangChain/Milvus/Neo4j 等） ──
RUN pip install --no-cache-dir ".[ai]"

EXPOSE 8000

# ── 启动命令：先执行数据库迁移，再启动 Uvicorn ──
# 使用 sh -c 串联命令，确保迁移完成后再启动服务
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
