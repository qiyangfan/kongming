# Proposal: 2026-11-26-add-kongming-runtime-policy（运行时策略执行（runtime-policy））

## Why

共享策略包的激活/作用域/版本改由锦官拥有；kongming 保留护栏规则体、规则类型与执行实现。

## What Changes

- 按锦官策略包快照执行注入/PII/输出/工具审核。
- 本地保留规则目录/规则体编辑（服务域配置）；共享策略包激活只读。
- 策略拒绝/脱敏审计；服务 admin API 本地写规则，锦官只读展示。

## Non-goals

- 不在本地编辑共享策略包激活/作用域。
- 不做通用策略 DSL。
- 不把服务域规则体上收锦官。
- 不把策略塞进 prompt。

## Capabilities

### New Capabilities

- `runtime-policy`：控制面策略快照的本地执行

### Modified Capabilities

无

## Impact

guardrail/toolcall 执行层；删除本地策略编辑；不写控制面。
