# 客户反馈结构化分析 Agent

[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=metratio_feedback-analysis-agent&metric=alert_status)](https://sonarcloud.io/dashboard?id=metratio_feedback-analysis-agent)
[![Coverage](https://sonarcloud.io/api/project_badges/measure?project=metratio_feedback-analysis-agent&metric=coverage)](https://sonarcloud.io/dashboard?id=metratio_feedback-analysis-agent)
[![CI](https://github.com/ratiolin/feedback-analysis-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/ratiolin/feedback-analysis-agent/actions)

面向虚构项目协作 SaaS 的工单结构化、重复问题聚类、候选 SOP 与周报演示系统。


## 定位

系统把客服工单转化为可审核、可追溯的问题池：

```text
CSV / 在线工单
→ Dify + DeepSeek 结构化建议
→ Schema / quote 定位 / 责任路由 / 严重度硬门
→ BGE 中文向量聚类
→ 候选 SOP
→ 可下钻到 ticket_id 的周报
```

边界：

- LLM 只输出摘要、分类建议、逐字 `quote` 和待确认原因。
- 服务端负责原文 offset、责任方、严重度、升级与人工复核。
- 根因假设只能进入“待确认原因”，不能进入周报事实结论。
- 候选 SOP 不会自动进入正式知识库。
- 公开审核只写入 24 小时匿名会话沙箱。
- 合成数据不能证明真实业务收益、真实分布或生产 SLO。

## 当前证据

```text
合成工单：240（开发/演示 180，锁定抽检 60）
问题类型：8
产品区域：8
当前问题簇：40（20 个重复簇 + 20 个单例）
候选 SOP：20
工作流：4 个已发布；flash 回放完成（2026-08-01）
```

四个工作流均已发布并晋升为作品集演示基线。当前基线为 **thinking v2**（2026-08-01 晋升，记录见 `artifacts/evaluation-v7-thinking-v2-candidate/promotion-record-thinking-v2.json`）：`deepseek-v4-flash` + `thinking: true` + `reasoning_format: separated` + `min_pairwise_precision: 0.90`；结构化工作流九项质量门全部通过（问题类型 Macro-F1 0.875、重复识别精确率 100%、召回 60%、B³ F1 0.889、聚类纯度 100%），内容工作流问题簇 30/30、候选 SOP 5/5、周报叙事 6/6、证据引用与安全动作 100%。历史基线：flash 无思考（B³ F1 0.857，`promotion-record-flash.json`）与 V7 pro（2026-07-02），均只作历史记录。下述 V7 数据来自模型切换前的冻结版本，只作历史基线。

V5、V6 失败结果继续保留。V7 使用全新 `v7-frozen-signature-clustering-holdout-20260702`（N=60）评分，曾在 2026-07-02 由独立 promotion record 晋级为作品集演示基线，2026-08-01 被 flash 配置取代后转为历史基线：Schema 契约有效率 100%、quote 自动定位 100%、问题类型 Macro-F1 0.846、产品区域 Macro-F1 0.963、责任路由一致率 85.0%、升级召回 100%、重复识别精确率 84.2%、召回率 53.3%、聚类纯度 96.7%、B³ F1 0.853。首次依赖成功率 88.3% 是独立信息项，不与 Schema 契约有效率混写。

V7 只证明模型切换前的合成机制门禁通过。60 条标签经过 AI 辅助一致性复核，不是独立人工审计，也不代表真实业务分布、生产 SLA 或效率收益。flash 回放与晋升明细见 `docs/workflow-suite-activation.md` 与 `dify-workflows/suite-v1-manifest.json`。

## 快速开始

```bash
cp .env.example .env
# 设置 FEEDBACK_DB_PASSWORD；Dify 工作流导入后再设置 API Key
docker compose up -d --build

docker compose --profile seed run --rm feedback-seed
curl http://127.0.0.1:18100/feedback
```

本地开发：

```bash
uv sync --extra dev
uv run python tools/generate_dataset.py
uv run pytest
uv run ruff check feedback_app tools tests migrations

docker run --rm -v "$PWD/web:/app" -w /app node:22-alpine \
  sh -lc 'npm ci && npm audit --registry=https://registry.npmjs.org --audit-level=moderate && npm run lint && npm run build'
```

模型评测与候选激活：

```bash
uv run python tools/evaluate.py --analyzer demo --embedding tfidf

# CI 自动化（PR 自动运行）
# 见 .github/workflows/ci.yml

# 历史 V5 套件可复现脚本
./tools/run_v5_suite_evaluation.sh

# V7 的冻结清单、回放缓存与结果见：
# data/v7-evaluation/
# artifacts/evaluation-v7-candidate/
```


## 代码质量

| 工具 | 用途 | 状态 |
|---|---|---|
| [ruff](https://docs.astral.sh/ruff/) | Lint + 格式化 | 零警告 |
| [pytest](https://docs.pytest.org/) | 单元 & 集成测试 | 140 通过，总覆盖率 99% |
| [SonarQube Cloud](https://sonarcloud.io/dashboard?id=metratio_feedback-analysis-agent) | 持续代码质量 | 质量门 OK，新代码覆盖率 98.9%，未解决问题 0 |
| GitHub Actions CI | ruff + pytest + 前端 audit/lint/build + SonarQube | 已配置 |

CI 约束：`portfolio/index.html` 是静态作品页契约测试输入，必须随仓库提交；不要让测试依赖只存在于本地 ignored 文件中。npm 安全审计显式使用 `registry.npmjs.org`，避免镜像站缺少 audit API 时产生无法验证的结果。

近期优化：重构 `rebuild_clusters`（164 行 7 函数）、提取 `_try_cache_hit` / `_perform_analysis` 降低 `process_job` 复杂度、提取 `_single_linkage_clusters` 专责聚类；补齐 API、Worker、分析器、数据库、pipeline、workflow suite 等边界测试；修复前端 root-owned node_modules 环境。


## 目录

```text
feedback_app/       FastAPI、Worker、规则、聚类和周报
web/                Next.js 产品看板与匿名会话 BFF
dify-workflows/     已发布的四工作流 DSL 与冻结套件清单
tools/              数据生成、种子和离线评测
tests/              规则、证据、数据、聚类和报告测试
portfolio/          静态作品页
docs/               架构、部署、评测边界与人工审核说明
```

## 数据与安全

- 数据全部为虚构合成样本。
- 在线输入最长 2,000 字；CSV 最大 1 MB、10 行。
- 邮箱、手机号和疑似密钥在持久化及模型调用前脱敏。
- 公网实时分析限制为每会话每天 5 条、每来源每天 10 条、全站每天 100 条；刷新或清除 cookie 不能绕过来源限额。
- API、Postgres 和 Worker 不暴露公网，仅 Next.js 通过 BFF 访问。
- 密钥只允许放 `.env`，仓库只提供 `.env.example`。
- `Idempotency-Key` 在数据库中按会话唯一，并处理并发插入竞争；重复请求返回原任务。
- 仅对“工作流版本 + 脱敏后原文完全一致”的输入复用分析，避免 evidence offset 因空格或标点差异错位。
- 实时工单、任务与审核结果按匿名会话隔离；过期会话由 Worker 清理。

详见 `docs/architecture.md`、`docs/evaluation-boundary.md` 和 `docs/deployment.md`。

真实密钥不得进入仓库。AI 辅助复核不等于独立人工审计。

四工作流套件的导入、真实回放和 V7 晋级记录见 `docs/workflow-suite-activation.md`。晋级范围仅为合成作品集演示。
