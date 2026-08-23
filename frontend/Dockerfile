# =============================================================================
# SoloChef Frontend Dockerfile (多阶段构建: Node 编译 -> Nginx 运行)
# 参考: https://blog.csdn.net/2301_80216352/article/details/162310443
# =============================================================================

# ── 阶段 1: Node 构建 ──
FROM node:22-alpine AS builder

# 国内服务器构建加速（可 --build-arg 覆盖为官方源）
ARG NPM_REGISTRY=https://registry.npmmirror.com

# 前端经 Nginx 反代同源访问后端，故默认相对路径；
# 直连后端(如 http://<服务器IP>:8000/api/v1)时用 --build-arg 覆盖
ARG VITE_API_BASE_URL=/api/v1

WORKDIR /app

# 先复制依赖清单，利用 npm ci + Docker 层缓存加速构建
COPY package*.json ./
RUN npm config set registry ${NPM_REGISTRY} && npm ci

# 复制源码并构建（Vite 在构建期读取 VITE_API_BASE_URL 固化进产物）
COPY . .
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# ── 阶段 2: Nginx 运行 ──
FROM nginx:1.27-alpine

# 移除 Nginx 默认配置，替换为自定义反向代理配置
RUN rm -f /etc/nginx/conf.d/default.conf
COPY nginx.conf /etc/nginx/conf.d/default.conf

# 拷贝构建产物到 Nginx 静态目录
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

# 显式启动 Nginx（前台运行，容器退出时 Nginx 随之退出）
CMD ["nginx", "-g", "daemon off;"]
