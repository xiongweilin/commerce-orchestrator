# ADR 001: Shadowbot 单体拆分

## 状态
已采纳（2026-07）

## 上下文
catalog_odoo_rpa.py 683 行单文件含 XML-RPC、产品构建、HTTP 编排三种职责。

## 决策
拆为 odoo_adapter / product_builder / catalog_odoo_rpa 三层。

## 理由
- odoo_adapter 跨项目可复用
- product_builder 可独立测试值构建
- 编排器只关心流程

## 后果
- shadowbot 新增包结构
- XML-RPC 模块无法真单元测试，已从覆盖率范围排除
- 编排器可通过 Mock 适配器集成测试
