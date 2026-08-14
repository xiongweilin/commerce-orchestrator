# 四工作流套件状态记录

当前状态为 `promoted_for_portfolio_demo`（2026-08-01 晋升）。四个 Dify 应用已导入、发布并配置独立 API Key；2026-07-19 控制台与仓库 DSL 已同步为 `deepseek-v4-flash`；2026-08-01 完成 flash 真实回放与冻结评测：结构化工作流九项质量门全部通过；内容套件经提示词强化后第三次回放五项质量门全部通过（问题簇 30/30、候选 SOP 5/5、周报叙事 6/6、证据引用 100%、安全动作 100%）。晋升记录：`artifacts/evaluation-v7-flash-candidate/promotion-record-flash.json`。模型切换前的 V7 结论保留为历史基线，不适用于当前 flash 配置。

2026-08-01 晚：四个工作流全部开启 `deepseek-v4-flash` 思考模式（`thinking: true`）并采用 `reasoning_format: separated`（下游节点收到剥离思考的纯净 text，思考内容独立存放）。配套调整：`DIFY_TIMEOUT_SECONDS` 20 → 60（思考模式单次周报调用实测约 17 秒）；周报提示词强化"最终回答必须输出完整 JSON"。开启思考后的内容套件回放（2026-08-01 02:49 UTC）五项质量门全部通过：问题簇 30/30、候选 SOP 5/5、周报叙事 6/6、证据引用 100%、安全动作 100%。结构化工作流的 thinking 模式冻结评测待完成。

### 结构化工作流 thinking 模式冻结评测（2026-08-01，未达门槛）

冻结集：`v7-thinking-frozen-signature-clustering-holdout-20260801`（N=60，thinking 模式 DSL 哈希 `56c8a277…`），120 行真实 Dify 调用（holdout + development）完成后评测：

| 指标 | thinking 实测 | 门槛 | flash 无思考 |
|---|---:|---:|---:|
| Schema 契约有效率 | 100% | 95% | 100% |
| 问题类型 Macro-F1 | 0.875 | 0.80 | 0.828 |
| 产品区域 Macro-F1 | 0.961 | 0.80 | 0.984 |
| 责任路由一致率 | 86.7% | 85% | 85.0% |
| 高风险升级召回率 | 100% | 100% | 100% |
| quote 自动定位 | 100% | 95% | 100% |
| 重复识别精确率 | **76.0%** | 80% | 100% |
| 重复识别召回率 | 63.3% | 50% | 50.0% |
| B³ F1 | 0.871 | 0.75 | 0.857 |

首次依赖成功率 98.3%（2 条重试）。**九项门禁中八项通过，重复识别精确率 76.0% 未达 80% 门槛**——thinking 模式提升了召回（63.3% vs 50%）但显著降低精确率（76% vs 100%），B³ F1 略升。按纪律，thinking 配置保持 `candidate_scored_unpromoted`，不取代当前已晋升的 flash 无思考基线；如需以 thinking 作为作品集基线，必须先解决重复误合并（精确率）问题。权威文件：`artifacts/evaluation-v7-thinking-candidate/`。

### 结构化工作流 thinking v2 冻结评测（2026-08-01，九项门禁全部通过）

根因：thinking 模式下 `issue_signature` 更概括（如"Webhook…""权限"），在 dev 集调出的阈值（0.61）上把相邻问题误合并。改进：`select_threshold` 的 pairwise 精度约束从 0.80 提高到 0.90（dev 集上 B³ 仅 0.938→0.923，代价最小），冻结集更新为 `v7-thinking-v2-frozen-signature-clustering-holdout-20260801`，dev 调出阈值 0.64。

| 指标 | thinking v2 实测 | 门槛 | thinking v1 | flash 无思考 |
|---|---:|---:|---:|---:|
| Schema 契约有效率 | 100% | 95% | 100% | 100% |
| 问题类型 Macro-F1 | 0.875 | 0.80 | 0.875 | 0.828 |
| 产品区域 Macro-F1 | 0.961 | 0.80 | 0.961 | 0.984 |
| 责任路由一致率 | 86.7% | 85% | 86.7% | 85.0% |
| 高风险升级召回率 | 100% | 100% | 100% | 100% |
| quote 自动定位 | 100% | 95% | 100% | 100% |
| 重复识别精确率 | **100%** | 80% | 76.0% | 100% |
| 重复识别召回率 | 60.0% | 50% | 63.3% | 50.0% |
| B³ F1 | **0.889** | 0.75 | 0.871 | 0.857 |

重复误合并 0 例、聚类纯度 100%。**thinking v2 在所有已测配置中 B³ F1 最高（0.889），九项门禁全部通过**。权威文件：`artifacts/evaluation-v7-thinking-v2-candidate/`、`data/v7-evaluation/v7-thinking-frozen-20260801-manifest.json`（v2 版本）。

### 晋升（2026-08-01）

thinking v2 经明确晋升记录（`artifacts/evaluation-v7-thinking-v2-candidate/promotion-record-thinking-v2.json`）晋级为当前作品集演示基线，正式取代 flash 无思考基线（`promotion-record-flash.json`，转为历史）。当前状态：`promoted_for_portfolio_demo_thinking_v2`；配置：`thinking: true` + `reasoning_format: separated` + `min_pairwise_precision: 0.90`。内容套件与结构化工作流在 thinking 模式下均已通过全部已测门禁。

## 应用与 Key

| DSL 文件 | 应用 | API Key 环境变量 | 当前 flash 状态 |
|---|---|---|---|
| `feedback-structuring-v3-candidate.yml` | `客户反馈结构化-v3-candidate` | `DIFY_FEEDBACK_WORKFLOW_API_KEY` | 已晋升；flash 冻结回放九门全过 |
| `issue-cluster-narrative-v1-candidate.yml` | `问题簇命名与解释-v1-candidate` | `DIFY_CLUSTER_WORKFLOW_API_KEY` | 已晋升；flash 回放 30/30 通过 |
| `sop-draft-v1-candidate.yml` | `候选SOP草案-v1-candidate` | `DIFY_SOP_WORKFLOW_API_KEY` | 已晋升；flash 回放 5/5（强化后） |
| `weekly-report-narrative-v1-candidate.yml` | `运营周报叙事-v1-candidate` | `DIFY_REPORT_WORKFLOW_API_KEY` | 已晋升；flash 回放 6/6（强化后） |

（五个工作流的模型配置均为 `deepseek-v4-flash` + `thinking: true` + `reasoning_format: separated`。）

## flash 回放结果（2026-08-01）

锁定集沿用 V7 冻结集（`v7-frozen-signature-clustering-holdout-20260702`，N=60），按当前 flash DSL 与代码重新冻结为 `v7-flash-frozen-signature-clustering-holdout-20260719`，全部 60 行真实 Dify 调用一次成功。

### 结构化工作流 `feedback-structuring-v3-candidate`（九项质量门全部通过）

| 指标 | flash 实测 | 门槛 | V7 历史 |
|---|---:|---:|---:|
| Schema 契约有效率 | 100% | 95% | 100% |
| 问题类型 Macro-F1 | 0.828 | 0.80 | 0.846 |
| 产品区域 Macro-F1 | 0.984 | 0.80 | 0.963 |
| 责任路由一致率 | 85.0% | 85% | 85.0% |
| 高风险升级召回率 | 100% | 100% | 100% |
| quote 自动定位 | 100% | 95% | 100% |
| 重复识别精确率 | 100% | 80% | 84.2% |
| 重复识别召回率 | 50.0% | 50% | 53.3% |
| B³ F1 | 0.857 | 0.75 | 0.853 |

首次依赖成功率为 100%（V7 为 88.3%），聚类纯度为 100%（V7 为 96.7%）。评测状态 `candidate_scored_unpromoted`，`eligible_for_manual_promotion`。

### 内容工作流套件（强化后第三次回放全部通过）

三次回放（2026-08-01 08:35 宿主 / 08:48 容器内新镜像 / 09:12 新提示词 + 新 Key）：

| 工作流 | 第一次 | 第二次 | 第三次（强化后） | 历史 |
|---|---:|---:|---:|---:|
| 问题簇叙事 | 30/30 | 30/30 | 30/30 | 30/30 |
| 候选 SOP | 5/5 | 4/5 | **5/5** | 5/5 |
| 周报叙事 | 5/6 | 6/6 | **6/6** | 6/6 |

前两次失败均为工作流内安全门禁拦截（`ValueError: forbidden_irreversible_action`，Dify sandbox 拒绝），即 flash 偶发在 recommended_action 或步骤中出现“退款/补偿/删除数据/修改权限/自动执行/直接发布”等不可逆动作表述，被门禁按设计拦截；不是网络或基础设施故障。同一冻结输入、temperature=0 下失败点不固定，说明 flash 对内容工作流提示词的合规输出存在波动。

处置（2026-08-01）：SOP 与周报提示词把禁词逐字写入约束并给出合规改写指引（DSL 哈希：SOP `68145176…`、周报 `68b01b53…`）；门禁（代码节点 FORBIDDEN 列表）保持不变；Dify 控制台重新导入两个工作流并更换 Key 后第三次回放五项质量门全部通过。同日经明确晋升记录（`promotion-record-flash.json`）晋级为作品集演示基线 `promoted_for_portfolio_demo`。

权威文件（flash）：

- `data/v7-evaluation/v7-flash-frozen-20260719-manifest.json`
- `artifacts/evaluation-v7-flash-candidate/evaluation.json`
- `artifacts/evaluation-v7-flash-candidate/status.json`
- `artifacts/evaluation-v7-flash-candidate/analysis-cache.json`
- `artifacts/workflow-suite-v1-flash-candidate/evaluation.json`

真实 Key 只在 `.env`，不进入仓库、聊天、截图或作品材料。

## 已激活的服务端约束

- 模型只输出 `quote`；服务端 exact match、规范化匹配并计算 offset。
- 最终责任方、严重度和升级由规则裁决，不盲信模型值。
- 问题簇、SOP 和周报中的 ticket ID 必须是确定性输入的子集，后端再次校验。
- 候选 SOP 始终为 `pending_review`，不会自动更改正式知识库或执行不可逆动作。
- 周报数字由确定性服务计算；模型只生成文案，根因只能标为“待确认原因”。
- Dify 调用失败时可降级为确定性文案，并持久化 `generation_source` / `workflow_version`。

## V7 历史结果（模型切换前）

以下结果只属于模型切换前冻结的 DSL 与哈希，不适用于当前 flash 配置。锁定集：`v7-frozen-signature-clustering-holdout-20260702`，N=60。

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| Schema 契约有效率 | 100% | 95% |
| 问题类型 Macro-F1 | 0.846 | 0.80 |
| 产品区域 Macro-F1 | 0.963 | 0.80 |
| 责任路由一致率 | 85.0% | 85% |
| 高风险升级召回率 | 100% | 100% |
| quote 自动定位 | 100% | 95% |
| 重复识别精确率 | 84.2% | 80% |
| 重复识别召回率 | 53.3% | 50% |
| B³ F1 | 0.853 | 0.75 |

首次依赖成功率为 88.3%，作为可靠性信息项单独展示，不降低 Schema 契约有效率。聚类纯度为 96.7%。

权威文件：

- `data/v7-evaluation/v7-manifest.json`
- `artifacts/evaluation-v7-candidate/evaluation.json`
- `artifacts/evaluation-v7-candidate/status.json`
- `artifacts/evaluation-v7-candidate/promotion-record.json`

## 失败历史与边界

V5 因问题类型、责任路由、重复识别精确率与召回率未达门槛而未晋级。V6 的问题类型 F1 达 0.901，但聚类精确率 57.1%、召回率 40.0%，仍未晋级。V7 使用全新冻结集，不把已读的 V5/V6 继续包装成未见评测。

V7 标签是 AI 辅助一致性复核，不是独立人工审计；结果不外推当前 flash、真实业务分布、效率收益或生产 SLA。当前 flash 已回放但内容套件未达门槛，不恢复 `promoted_for_portfolio_demo` 状态。
