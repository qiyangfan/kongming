# 054-gates-env-parity 提案:CI 门禁与本地等价 + 环境示例与运行现状对齐

## Why

2026-09-12 配齐的三仓门禁体系存在两处「名单上有、机制上没有」的真实缺口(2026-09-13 聚合复查确认):

1. **make check ≠ CI,pyright 是本地专属门禁**。本地 `api-check`/`agent-check` 均执行 `pyright`(根 Makefile),但 CI 的 api/agent 两个 job 只跑 ruff format/check + mypy + pytest(`.github/workflows/ci.yml`),无任何 pyright 步骤。pyright 与 mypy 的检查面不同(推断规则、unreachable、类型收窄等),pyright 独有的报错只在开发者本机暴露——CI 绿不能证明门禁绿,「以 CI 为准」的协作前提失效。
2. **`.env.example` 与运行现状脱节,冷启动默认绕过网关**。根 `.env.example` 的 `KM_AGENT_LLM_BASE_URL` 仍是 dashscope 直连值,而运行环境已于 2026-09-11 切换 muniu 网关(ADR-007,灰度中);全文件亦无 `KM_SQLGUARD_MODE` / `KM_AGENT_SQLGUARD_MODE`(053 引擎模式开关,缺省 shadow)条目。新环境按 example 起服务会:LLM 出口直连上游(绕过计量与对账)、SQL 安全引擎处于未声明的状态——环境复制是本项目最常用的冷启动路径,示例文件的错误会系统性复制。

## What Changes

1. **CI 补齐 pyright**:api job 增加 `pyright kongming`,agent job 增加 `pyright kongming_agent`,配置沿用各 app pyproject/`pyrightconfig` 现行设定,与本地 make check 完全同参。
2. **本地/CI 门禁对照表**:在 HANDOFF 门禁小节补一张「make check 子门禁 × CI job 步骤」对照表,作为今后增删门禁时的双向核对基线(防止再次出现单边增删)。
3. **`.env.example` 对齐运行现状**:`KM_AGENT_LLM_BASE_URL` 示例值改为 muniu 网关形态(`http://127.0.0.1:8787/v1`,注释保留 dashscope 直连作回滚参照);补 `KM_SQLGUARD_MODE` / `KM_AGENT_SQLGUARD_MODE` 示例(注释缺省 shadow,注明切主条件见 053)。

## Non-goals

- 不做「example 键位与 settings 字段自动对齐门禁」——键位集合小且变动低频,人工对照表已足够;自动化核对脚本属过度设计。
- 不改 CI 其它 job(liannu/web),不调 pytest 覆盖率阈值,不引入新类型检查器。
- 不在本变更内执行 053 切主或 ADR-007 灰度收口(时间依赖,另行推进)。

## Capabilities

### Modified Capabilities

(无 specs delta——本变更为工程门禁与环境文件修正,不触及任何能力契约的行为语义。)

## Impact

- 代码:无业务代码改动。
- CI:`.github/workflows/ci.yml`(api/agent 两 job 各加一步)。
- 文件:`.env.example`。
- 文档:`docs/HANDOFF.md` 门禁小节(对照表)。
- 风险:pyright 在 CI 首跑可能暴露本地未报但 CI 环境差异导致的报错(概率低,同版本同配置);若有,按门禁失败正常修复,不放宽配置。
