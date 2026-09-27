# 067-guard-reserved-table-single-source 设计

## Context

- 保留表判定当前有两处字面值:api `sqlfw/firewall.py`(权威、较宽)与 `liannu/rules.py`
  (引擎、较窄)。053 处于 shadow,内置路径权威,故暂无泄漏;切 primary 后窄清单上位。
- agent `sqlguard.py:33` 另有 `_READONLY_ROOTS` 第三份拷贝,靠注释人工对齐。
- 规格的清单陈述散在 `sql-execution` / `skill-pack` / `agent-memory` 三处。
- api 与 agent 均以 uv path 依赖 `liannu==0.1.2`(该包 053 时从外部仓并入本仓)。

## Goals / Non-Goals

**Goals:** 一条安全不变量一处定义、一份规格权威;漂移在测试期即红,而不是等灰度对拍跑出
mismatch(而对拍对该缺陷本身是盲的)。

**Non-Goals:** 见 proposal。

## Decisions

1. **常量的家是 `liannu`,不是 api**
   `packages/liannu` 是 api 与 agent **唯一共享**的包,且是进程内纯计算、零凭证、零 IO。
   放在 api 侧则 agent 需反向依赖 api 包,直接违反 ADR-002 信任边界;放在 agent 侧同理不可行。
   放独立新包则是为一个 frozenset 造第三层结构,过度。

2. **api 侧 re-export,不改下游引用点**
   `firewall.py` 保留符号名(`RESERVED_TABLE_PREFIXES` 等)但改为从 liannu 导入,
   `internal/meta.py:29`、`rag/repository.py:43` 的既有 import 面零改动。
   re-export 不构成"第二份定义"(无字面值),且避免全仓改 import 带来的
   import-linter 契约重排风险。

3. **一致性断言用对象恒等(`is`),不用值相等(`==`)**
   本次缺陷的成因正是"两份常量各自都自洽":值相等断言在两处**同步**更新时依然绿,
   只防得住"忘了改一处"的一半场景。断言
   `firewall.RESERVED_TABLE_PREFIXES is liannu.rules.RESERVED_TABLE_PREFIXES`
   封的是"存在第二处可改的地方"本身。引擎侧同理:断 `engine 导出对象 is 本地引用对象`。

4. **mismatch 归零不作为清单一致的证据,须显式补退出条件**
   shadow 只在查询实际触达差异表时产生 mismatch;无覆盖即无信号。因此 053 的切主判据
   从「mismatch 归零 + 一周无 fallback」改为「**新增:一致性断言绿** + mismatch 归零 +
   一周无 fallback」。这是本变更对 053 的唯一实质修订。

5. **引擎版本下限一并断言**
   把常量收进包之后,若运行期 `liannu` 被降级到引入单源之前的版本,窄清单会重新上位。
   启动自检已有"版本不匹配即降级内置路径"的通路;补断言校验引擎导出清单的**成员集合**
   覆盖 `skill_`/`user_prefs`,不覆盖即视为引擎不可用并降级 + 计入 `ENGINE_FALLBACK`。

6. **规格归一:清单住在 `sql-execution`,域内 spec 只留理由**
   `skill-pack:82` 与 `agent-memory:181` 保留"为何这张表敏感"(skill_pack 含跨租户
   术语/示例资产;user_prefs 归属列是 user_id 故 data_scope 不构成防线)与其拒读场景,
   删去对清单成员的共同声明。判定住在执行路由域,理由住在数据域——不是把三条并成一条,
   而是让"完整清单是什么"只有一个出处。

## Risks / Trade-offs

- [liannu 与本仓耦合加深:常量变更也要走包发版] → 该包已是本仓 path 依赖、同仓演进,
  不存在外部发布流程;纪律仍是"改版本号 + 同步下游 `pyproject.toml` 声明"(053 已确立)。
- [re-export 使 api 与包同名符号并存,可能有人重新在 api 里写字面值] → 恒等断言即红;
  符号旁注一行说明"定义在 liannu"。
- [恒等断言在引擎以 wheel 而非 path 安装时可能拿到不同模块实例] → 断言对象是
  **清单成员集合 + 引擎可用性**,而非进程内对象地址;wheel 形态下降级路径已覆盖。
- 净收益:切主风险归零、新增敏感表回到单点变更、规格不再需要读三处。

## Migration Plan

纯代码与规格收口,无数据迁移、无灰度、无开关(行为集不变,故不需要影子期)。
落地顺序:先加恒等断言(此刻应为**红**)→ 收编清单进 liannu → api/agent 改引用 →
断言转绿 → 规格 sync → 在 053 的 tasks/退出条件上补该前置。回滚 = 还原两处常量为本地定义,
无外部状态。
