# =============================================================================
# SoloChef Frontend Dockerfile (多阶段构建: Node 编译 -> Nginx 运行)
# 参考: https://blog.csdn.net/2301_80216352/article/details/162310443
# =============================================================================

# ── 阶段 1: Node 构建 ──
FROM node:22-alpine AS builder

WORKDIR /app

# 先复制依赖清单，利用 npm ci + Docker 层缓存加速构建
COPY package*.json ./
RUN npm ci

# 复制源码并构建
COPY . .
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
