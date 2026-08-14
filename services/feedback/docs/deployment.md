# 部署与运维

## WSL2

```bash
cd /srv/stack/feedback-analysis-agent
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:18100/feedback
```

服务：

- `feedback-web`：`127.0.0.1:18100`。
- `feedback-api`：Docker 内部 `8101`，加入 `shared-net`。
- `feedback-worker`：持久化任务消费者。
- `feedback-postgres`：无宿主机端口。

Tailscale：

```bash
sudo tailscale serve --bg --yes --tcp=8100 tcp://127.0.0.1:18100
```

## 云端

云端 Nginx 的 `/feedback` 与 `/feedback/` 转发至 `metratio.tail1f4641.ts.net:8100`。`^~ /feedback/` 用于防止 Next.js 的 JS/CSS 被静态资源正则截获。

静态作品页位于：

```text
/srv/stack/nginx/metratio-static/feedback/index.html
```

云端 Nginx 已为 `/feedback` 和 `/feedback/` 配置静态作品页路由（/feedback, /catalog-ops）；必须放在通用 `/index` location 之前，否则请求会回落到站点总首页。

## 监控

- Prometheus job：`feedback-analysis`。
- Blackbox：`https://metratio.com/feedback`。
- Grafana UID：`feedback-analysis`。
- 指标：工单、问题簇、候选 SOP、待复核、请求率和 p95 延迟。

## 更新流程

```bash
uv run pytest
uv run ruff check feedback_app tools tests migrations
docker compose build
docker compose up -d
docker compose ps
```

Nginx 修改必须先备份、运行 `nginx -t`，成功后再 reload。

运行时迁移与回归检查：

```bash
docker compose exec feedback-api alembic current
docker compose exec feedback-api alembic check
curl -fsS http://127.0.0.1:18100/feedback/api/health
```

## 四工作流回放与当前配置

四个 DSL 导入、发布与 API Key 配置完成后执行：

```bash
cd /srv/stack/feedback-analysis-agent
./tools/run_v5_suite_evaluation.sh
```

该脚本用于复现历史 V5；结果写入 `artifacts/evaluation-v5-suite-candidate/` 和 `artifacts/workflow-suite-v1-candidate/`。V5、V6 均已读取且未晋级，不得再作为调参后的未见集。

当前作品集演示运行配置由 `artifacts/evaluation-v7-candidate/promotion-record.json` 固化，使用 V3 结构化工作流、V3 路由策略和 V7 聚类配置。V7 冻结清单位于 `data/v7-evaluation/v7-manifest.json`。manifest 哈希、审计行或 promotion record 不匹配时，不得声称部署的是已评分配置。

公网 BFF 会把 HttpOnly 会话 cookie 转成内部 `X-Demo-Session`，并将可信代理链末端地址传给 API 生成单向哈希。API 同时执行会话、来源和全站三级日限额；原始地址不持久化。
