# Design: 运行时命令执行器（command-executor）

## Context

锦官跨服务命令队列是新的；kongming 已有 reindex/缓存/任务取消能力。本地运行命令不应被控制面队列绑架。

## Goals / Non-Goals

**Goals:**
- 跨服务命令可执行、可回执。
- 服务恢复后收敛；本地命令独立可用。

**Non-Goals:**
- 不执行任意脚本。
- 不接管本地运行命令。
- 不做控制台 UI。

## Decisions

1. 只接受有限命令枚举；按 X-01 action/params schema/version 校验；未知 action/版本拒绝；拒绝任意 SQL/脚本。
2. 以服务身份 claim 后执行；幂等键防重复；ack 结果。
3. 执行失败可重试，超限标 failed。
4. 本地运行命令走本地管理台，与方法 1-3 共用执行器但不经队列。

## Risks / Trade-offs

- 重复执行 → 幂等键。
- 长任务超时 → 状态/心跳/上限。
- 双通道 → 来源标注 + 统一审计。

## Migration Plan

greenfield：新增命令执行器；本地命令保留。
