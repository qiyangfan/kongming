# 065-eval-ci-gate 提案:评估回归定时门禁

## Why

016 建了完整评估体系——Execution Match 黄金指标(严格/宽松双分)、olist + sakila 数据集、single/multi-turn 分层、匹配器纯函数单测。但 `pyproject.toml` 明确 `testpaths = ["tests"]`,评估必须**手动指定 `evals` 目录 + 真 LLM key** 才跑;CI 五个 job(api/agent/liannu/web/contracts)无一触碰。`ai-eval` 域现行契约也把「评估环境非 CI 标配」写成了 Requirement。

结果是:**prompt 改动、模型切换、schema 变更,任何质量退化都没有自动防线**,只能靠人肉发现。而本批 063(表预筛)、064(召回闸门)全是提示词与召回构成变更——没有回归门禁,它们无法被验证"没有变差",只能凭感觉上线。

需要一个 PR 上不跑、但按周期跑的闸门:PR 上跑既烧真 token 又 flaky,会把门禁变噪音;完全不跑则退化不可见。

## What Changes

1. **`ai-eval` 域「CI smoke 守护」Requirement 修订**:评估环境仍 SHALL NOT 作为 PR 门禁;系统 SHALL 提供定时(nightly)回归门禁——在具备评估环境的 runner 上跑 smoke 层(约 5 题),严格/宽松通过率落 artifacts,**劣化 ≥5pp 逐题溯源**(与既有回归红线同口径),失败即 job 失败。
2. CI 新增 `eval-nightly` job:`schedule`(cron 每日)+ `workflow_dispatch`(手动,可选 `full` / `multiturn` 层);LLM 凭证走 repository secret,不入库不打印。
3. **评估可重复口径固化进 job**:prompt 类改动后重跑前清 `query_history` 同题沉淀与 `ragctx` 缓存(evals/README 已记载的 011 教训——否则验证的是缓存不是 prompt);该口径写入 job 步骤与注释。
4. 基线可比:smoke 通过率随运行落 artifacts,并可与上一次运行比较(不做趋势看板,人工看 artifacts 即可)。

## Non-goals

- **PR 上跑评估**:真 token 成本 + 结果 flaky,决定它只配做定时门禁。
- **gold 数据集扩充与多数据集**(sakila 之外):属数据集工程,另立 change。
- **历史基线趋势看板 / 自动归因到 commit**:先人工看 artifacts。
- **把评估改造成确定性离线回放**:真实 LLM 的本质不变,追求确定性只会造出新轮子且失真。
- **匹配器逻辑改动**:已由 `tests/test_eval_matchers.py` 常规门禁守护,不动。

## Capabilities

### Modified Capabilities

- ai-eval:「CI smoke 守护」Requirement 修订——由"smoke 集仅在具备评估环境处显式触发运行"扩展为"显式触发 + 定时门禁双形态",并明确定时门禁的红线口径与失败语义。

## Impact

- **落点**:`.github/workflows/ci.yml`(新增 `eval-nightly` job)、`openspec/specs/ai-eval/spec.md`、`apps/api/evals/README.md`(口径同步)。
- **需运维动作**:repository secret 配评估用 LLM 凭证(非代码变更;缺凭证时 job SHALL 显式失败而非静默跳过——静默跳过等于没有门禁)。
- **无 schema / 契约 / ADR 变更**。
