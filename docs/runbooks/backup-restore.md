# Runbook：Odoo 备份与隔离恢复演练

## 目标

为 Odoo 19（权威账本）建立 **DB + filestore 基线备份**，并在**每批 Odoo 模块安装前**建立新基线；**先恢复验证，再进入下一批**，防止不可逆的模块迁移事故。

> 备份范围包括：除 Odoo 外，业务主库 `commerce` 与 DBOS 系统库 `dbos`
> （以及监控/告警配置）纳入同一备份与隔离恢复流程；backup/restore 演练通过是验收要求。

## 原则

- 每批模块安装前：建基线 → 恢复验证 → 通过后才安装下一批。
- 备份必须可独立恢复（隔离目录/容器），不能只“有文件”。
- 备份与恢复使用同一 Odoo 大版本（19）。

## 步骤

### 1. 记录当前状态

```bash
# 记录 Odoo 版本与已装模块清单，作为基线元数据
python odoo-bin --version
# 模块清单以运行实例的 ir.module.module 为准（状态 installed）
```

### 2. 建立基线备份

```bash
# 数据库（PostgreSQL，容器内执行）
docker compose exec -T odoo-db pg_dump -Fc -U <db_user> <odoo_db> \
  > backups/odoo_baseline_YYYYMMDD_HHMM.dump

# filestore（容器数据目录）
tar -czf backups/odoo_filestore_YYYYMMDD_HHMM.tar.gz <odoo_filestore_dir>
```

同一批次内 DB 与 filestore 应取自同一时刻（先停写入或接受极小窗口并记录）。

### 3. 校验备份可读性

```bash
pg_restore --list backups/odoo_baseline_YYYYMMDD_HHMM.dump | head
sha256sum backups/odoo_*_YYYYMMDD_HHMM.* > backups/BACKUP_MANIFEST.txt
```

### 4. 隔离恢复演练（验证）

- 在**独立目录/独立容器**中恢复，不得覆盖当前运行实例。
- 恢复 DB：`pg_restore -d <recovery_db> backups/odoo_baseline_YYYYMMDD_HHMM.dump`
- 恢复 filestore 并映射到恢复容器。
- 启动恢复实例，做 smoke test：登录成功、关键模型计数、附件可下载、模块列表与基线元数据一致。

### 5. 验证通过后进入下一批

- 演练通过 → 记录基线号到台账 → 开始安装本批模块。
- 本批失败 → 用刚验证的基线恢复运行实例，回到批次前状态。

### 6. 轮换与保留

- 保留最近 N 份基线（如 3 份）用于回滚；超期基线按保留策略归档或删除，台账同步更新。
- 台账记录：基线号、时间、Odoo 版本、模块清单、演练结果。

## 注意事项

- 备份/恢复路径涉及容器内外映射时，先确认数据目录挂载点（以 infra/ 为准）。
- 所有备份文件放入备份目录并纳入权限控制；本 runbook 只描述流程，不存放任何凭据。

## 验收要求

1. **备份范围**：`commerce`（业务库）、`dbos`（DBOS 系统库）、Odoo 库 `odoo`
   （DB + filestore）分别 `pg_dump -Fc`；监控/告警配置以 git 为备份。
2. **隔离恢复演练**：在独立容器/目录恢复，不得覆盖运行实例；恢复后验证：
   - `alembic current` 等于 head（或按备份时刻记录并重跑 `migrate`）；
   - API `/livez`、`/readyz` 恢复为 200（worker 心跳恢复后）；
   - 关键业务计数与备份清单一致；inbox/outbox 无半状态积压；
   - 恢复后跑一次对账验证账实一致（差异清零才算演练通过）。
3. **证据留存**：备份可读性校验（`pg_restore --list` + sha256 manifest）、
   演练结果记录到台账。
4. **不可逆清理前置**：明文 PII 清空类操作必须在备份验证与回滚期结束后执行
   （见 [privacy-cleanup.md](privacy-cleanup.md)）。
