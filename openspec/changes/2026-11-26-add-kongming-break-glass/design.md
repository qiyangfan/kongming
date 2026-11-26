# Design: 孔明运行时 break-glass（break-glass）

## Context

控制面/运行时分离后，运行时数据修复不能走 admin 常规写路径；需要独立应急通道。

## Goals / Non-Goals

**Goals:**
- 应急修复可控、可审计。
- 不成为第二写者/日常路径。

**Non-Goals:**
- 不做 UI。
- 不做任意 SQL 控制台。
- 不替代 HA/备份。

## Decisions

1. 独立二进制/脚本，独立 DB 凭证；不随 admin 部署。
2. 每次操作需原因、二次确认、时间窗与审计记录（操作者/摘要/影响行数）。
3. 只允许文档化修复场景；常规修复走服务 API/命令。

## Risks / Trade-offs

- 高危凭证泄露 → 独立保管/限时/审计。
- 误操作 → 备份 + 二次确认 + 影响行数上限。

## Migration Plan

greenfield：新增工具；无数据迁移。
