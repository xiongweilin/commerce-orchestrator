# 数据所有权契约（唯一事实源）

> 本文档定义领域事实 ownership、字段级 write boundary 与 projection 规则。责任层新增记录不会改变 Shopify/Odoo 原有业务事实 ownership。

## 1. Domain ownership

| Domain | Authoritative facts | Storage | Write rule |
| --- | --- | --- | --- |
| Feedback Intelligence | structured feedback、cluster、AI candidate proposal | Commerce PostgreSQL / feedback service | AI 只写 suggestion/evidence，不写业务 authority fact |
| Operating Policy | approval boundary、SOP、sensitive-category rule | Commerce PostgreSQL | policy/compliance path |
| Catalog-PIM | catalog revision、listing content/version | Commerce PostgreSQL / catalog service | catalog workflow + `catalog_owner` decision |
| Offer/Pricing | pricing/promotion rule | Commerce PostgreSQL + Odoo execution | domain approval boundary |
| Shopify | channel-side product/order/refund reality | Shopify | only Shopify adapter/effect path |
| Odoo Product | Odoo product master | Odoo 19 | Odoo-owned write path |
| Inventory | stock reality | Odoo 19 | stock move / inventory adjustment only |
| Sales/Purchase | sale order / PO / receiving/shipping business facts | Odoo 19 + Commerce workflow references | controlled domain workflows |
| Finance | invoice/bill/credit note/accounting fact | Odoo 19 | accountant/Odoo accounting mechanisms |
| Workflow Control | WorkflowRun、WorkItem、Decision、event、inbox/outbox、idempotency、effect ledger、runtime heartbeat | Commerce PostgreSQL + DBOS system DB | API/worker services only |
| Responsibility / Authority | ExecutionAuthorization、ResponsibilityBinding/Record/Event/KnowledgeProjection/Obligation、ConfirmedOutcome | Commerce PostgreSQL | responsibility services/API only; append-only or explicit lifecycle |
| Qualification / Reality | PublicationQualificationAssessment、EffectRealizationAssessment、ReconciliationResolution | Commerce PostgreSQL | explicit append/review APIs and worker verification paths |
| Reconciliation | ReconciliationRun/Diff + canonical comparison result | Commerce PostgreSQL; actual state read from Shopify/Odoo | reconciliation services; no auto-smoothing |
| Metabase | read-only operations projection | projection DB | read only; never authoritative |

## 2. Ownership does not imply semantic equivalence

The new responsibility plane records Commerce governance/execution semantics; it does not re-own external reality.

Examples:

```text
ExecutionAuthorization
  owns Commerce authority fact
  != Shopify/Odoo business fact

EffectLedger.succeeded
  owns adapter/execution report
  != external reality

EffectRealizationAssessment
  records explicit verification evidence
  != universal objective truth

ConfirmedOutcome
  records a bounded confirmed business outcome
  != universal workflow responsibility discharge
```

## 3. Field-level rules

- 每字段 single owner；禁止 last-writer-wins。
- cross-system projection 必须保留可追溯来源，例如 `sourceRevision` / `observedAt` / `owner` 或等价稳定 reference。
- posted invoice/bill 不直接改写；通过 Odoo credit-note/correction mechanism 修正。
- inventory 不直接改 quantity field；通过 stock move/adjustment。
- AI 只生成 candidate suggestion；不 approve、不 mint `ExecutionAuthorization`、不 dispatch effect。
- console/read projection 不允许 write-back 或 client-side mint authority。

## 4. Decision / authorization ownership

`WorkItemDecision` 与 `ExecutionAuthorization` 都属于 Commerce governance facts，但 ownership 不使二者等价。

- Decision 由 work-item decision path 记录。
- 需要 effect authority 的 approve 必须通过 `authorized-decisions`。
- ExecutionAuthorization target/operations/profile 由 server-owned Commerce logic 选择。
- return financial scope 从当前 `ReturnCase` 推导。
- dispatch 重新验证 current subject/version/fingerprint/policy/environment/expiry/revocation。

客户端不能通过提供 scope、allowed operations、历史 approval 或 role claim 扩大 authority。

## 5. CURRENT / HISTORICAL ownership

Historical Experience reliance 作为 immutable historical record 保存；current eligibility 是重新计算出的 explanation。

```text
historical record owner: responsibility/Experience persistence
current eligibility owner: server evaluation logic
```

历史记录存在不代表当前仍 qualified/authorized。

Responsibility obligation discharge 不删除原始 obligation；历史 lifecycle 仍属于 Commerce responsibility facts。

## 6. Reconciliation ownership

Canonical reconciliation 比较：

`listing` · `order` · `procurement` · `return` · `catalog` · `effect`

规则：

- external actual facts 仍由 Shopify/Odoo 拥有；
- Commerce owns reconciliation observation/diff/resolution records；
- missing reader/unchecked required domain 不能被记录成“0 diff success”；
- manual resolution 记录 disposition，不直接重写 external source of truth；
- subsequent read-back/reconciliation 才能证明 external state 与 expected state 一致。

## 7. Approval boundaries

固定角色：

`catalog_owner` · `commerce_lead` · `finance_approver` · `procurement_lead` · `budget_owner` · `warehouse_staff` · `inventory_supervisor` · `accountant` · `customer_service` · `compliance` · `system_admin`

`system_admin` 是运维角色，不自动获得全部业务 approve authority。

典型边界：

| Change | Decision roles / sequence |
| --- | --- |
| catalog/listing | `catalog_owner`; `compliance` may veto where applicable |
| pricing | `commerce_lead`; finance gate when policy requires |
| PO | `procurement_lead` propose -> `budget_owner` approve |
| receiving/shipping | `warehouse_staff` |
| inventory adjustment | `inventory_supervisor`; finance gate if valuation impacted |
| invoice/bill/credit note | `accountant` |
| return/refund | `customer_service` -> `warehouse_staff` -> financial approval/effect-specific profile |

Four-eyes checks are server-side and cannot be delegated to the console.

## 8. Sensitive data

Raw webhook/shipping/customer payload is stored in the encrypted `sensitive_payload` vault when retention is required.

- encryption: Fernet via `COMMERCE_ENCRYPTION_KEY`;
- match-without-recovery customer refs: HMAC pseudonymization via `COMMERCE_PII_HASH_KEY`;
- workflow/event payloads should carry stable references/minimal fields rather than duplicated raw PII;
- cleanup clears expired ciphertext then records tombstone metadata;
- logs/traces/metrics/alerts must not expose plaintext PII or tokens;
- Dify receives redacted feedback only.

Exact current settings are defined by `backend/app/config.py` and `.env.example`.

## 9. Projection rule

Metabase and console projections are read models. A projection may be rebuilt and may be stale; it cannot become the fact owner by being displayed.

The Responsibility Inspector is also a projection: top-level `authorityBearing=false`. It explains durable facts/current evaluation but does not create authority.
