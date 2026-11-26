# Proposal: 2026-11-26-add-kongming-runtime-command-executor（运行时命令执行器（command-executor））

## Why

锦官需要在服务恢复后执行跨服务 reindex/清缓存/取消任务等命令；kongming 需要统一命令执行器。本地运行命令独立执行，不进入控制面队列。

## What Changes

- 以服务身份拉取/claim 跨服务命令；执行/ack。
- reindex/缓存清理/任务取消等有限动作；action/params schema/version 按 X-01 校验。
- 本地运行命令由本地管理台直接执行。
- 幂等/结果/审计；未知 action/版本拒绝。

## Non-goals

- 不执行任意 SQL/脚本。
- 不接受控制面直接写库。
- 不接管本地运行命令。
- 不做命令 UI（控制台在锦官）。

## Capabilities

### New Capabilities

- `command-executor`：控制面命令的本地执行与回执

### Modified Capabilities

无

## Impact

apps/api 命令客户端/执行器；控制面命令队列；不写控制面之外的数据。
