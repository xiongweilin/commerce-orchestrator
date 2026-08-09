# syntax=docker/dockerfile:1
#
# commerce-orchestrator console（Next.js）镜像。
# 依赖 console/ 下的 package.json / next.config 等源码（由 console 专项负责），
# 要求 next.config 开启 output: 'standalone'，本镜像只保留 standalone 产物 + 静态资源。

# ------------------------- deps：安装锁定依赖 -------------------------
FROM node:24-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

# ------------------------- builder：生产构建 -------------------------
FROM node:24-alpine AS builder
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

# ------------------------- runner：仅 standalone 产物 -------------------------
FROM node:24-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0

# 非 root 运行用户（沿用 Next.js standalone 惯例的 uid/gid 1001）
RUN addgroup --system --gid 1001 nodejs \
    && adduser --system --uid 1001 nextjs

# public/ 静态资源（若 console 项目没有 public/ 目录，请删除本行）
COPY --from=builder --chown=nextjs:nodejs /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs

EXPOSE 3000

CMD ["node", "server.js"]
