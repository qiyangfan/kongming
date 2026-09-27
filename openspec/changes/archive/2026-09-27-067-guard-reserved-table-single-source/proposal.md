# 067-guard-reserved-table-single-source 提案:保留表清单单源化与规格收编

## Why

「控制面保留表拒读」是一条平台级安全不变量,但它在仓内**有两条实现、三份规格出处**,且三者已经不一致。

实测对照(以代码实际行为为准):

| 位置 | 保留前缀 | 精确名 |
|---|---|---|
| `apps/api/kongming/sqlfw/firewall.py:22,27-29` | `sys_` `chat_` **`skill_`** | `alembic_version` `user_identities` `user_memory` **`user_prefs`** |
| `packages/liannu/liannu/rules.py:15,16` | `sys_` `chat_` | 前三项,**缺 `user_prefs`** |

`liannu/rules.py:1` 的文件头自称「与消费方 kongming sqlfw 一比一移植,语义与 reason 逐字一致」——
这句话与当前代码行为冲突,以代码为准:**不同步**。缺的两项是后续变更搭车加进 api 侧的
(`skill_` 前缀随 056 skill-pack、`user_prefs` 随 059 偏好记忆),加的时候只改了一处常量。

规格侧同样碎:拒读义务的完整枚举需要读三个文件——`sql-execution` 的
「控制面保留表拒读」原文只列 `sys_`/`chat_` 与三个精确名(**未收编 `skill_`/`user_prefs`**),
`skill_` 的拒读写在 `skill-pack/spec.md:82`,`user_prefs` 的写在 `agent-memory/spec.md:181`。
一条安全清单被拆成三处陈述,是"下一张敏感表该写进哪份 spec"没有答案的根因。

### 为什么现在必须处理:053 的退出判据对这个缺陷是盲的

053 当前处于 shadow(内置路径权威),所以**今天没有泄漏通路**。但 053 的归档退出条件是
「shadow mismatch 归零 + 一周无 `ENGINE_FALLBACK`」后切 `KM_SQLGUARD_MODE=primary`——
切主后 **liannu 的窄清单成为权威,`skill_pack` 与 `user_prefs` 停止被拒读**。

而 mismatch 归零**不构成清单一致的证据**:只有查询实际触达这两类表时才会产生 mismatch,
评估集与自然流量都不保证覆盖它。也就是说,这条按原计划推进会"验证通过"的灰度,
恰好把这个缺陷原样放进主力形态。

同类机制的第三次拷贝在 agent 面:`apps/agent/kongming_agent/sqlguard.py:33` 有一份
`_READONLY_ROOTS`,靠注释「与 api 防火墙只读根一致(sqlfw/firewall.py)」人工同步——
同一漂移结构。

## What Changes

1. **常量单源**:保留表前缀、保留表精确名、保留 schema、只读根语句类型、危险函数五组常量,
   唯一定义收进 `packages/liannu`(api 与 agent 唯一共享的纯计算包);两处消费方改为**引用**,
   不再各自定义字面值。
2. **清单收编(不改现有拦截集)**:把 `skill_` 前缀与 `user_prefs` 精确名并入单源清单——
   即与 api 当前实际行为对齐,本变更**不放宽也不收紧**任何一条判定。
3. **规格收编**:`sql-execution`「控制面保留表拒读」升格为该清单的**唯一权威枚举**
   (补齐 `skill_`/`user_prefs`);`skill-pack` 与 `agent-memory` 两处只保留"该表为何敏感"
   的域内理由与拒读场景,不再各自陈述清单成员。
4. **一致性以对象恒等断言,而非值相等**:新增单测断言各消费方读到的就是同一个常量对象,
   并断言已加载引擎导出的清单与之恒等。值相等断言挡不住本次这类缺陷(两份常量各自
   "看起来都对"仍绿),只有恒等断言能封死"第二处定义"。
5. **053 退出条件补一条**:切 primary 前必须通过第 4 项断言;mismatch 归零不再是充分条件。

## Non-goals

- **不改判定语义**:除把 api 现状写进共享常量外,不新增、不移除任何保留名,不改 reason 文案。
- **不给 agent 面新增保留表判定**:agent 预检继续只判只读根,保留表权威在 api 防火墙
  (既有纵深防御分工与 ADR-002 边界不变,不因此变更而扩 agent 职责)。
- **不把清单搬进数据库或管理台热配**:保留表是随代码发布的平台级安全不变量;
  可热改即引入"运行期被改窄"的攻击面,与 `rbac` 域「权限点是随代码发布的契约数据」同一哲学。
- **不引入策略引擎(OPA 类)或外部配置中心**:五组常量是纯数据,单源化即可解决,
  引中间件属过度设计。
- **不重排 sqlglot AST 判定逻辑**,不动 053 的单遍解析产物与深度门。

## Capabilities

### Modified Capabilities

- sql-execution:「控制面保留表拒读」补齐 `skill_` 前缀与 `user_prefs` 精确名,并新增约束
  ——该清单 SHALL 只有一处定义,所有执行面判定点 SHALL 引用同一来源,SHALL NOT 存在
  需人工同步的平行拷贝;新增敏感表 SHALL 为单点变更并被一致性断言覆盖。

## Impact

- **落点**:`packages/liannu/liannu/rules.py`(唯一定义)、
  `apps/api/kongming/sqlfw/firewall.py`(改为引用/re-export)、
  `apps/agent/kongming_agent/sqlguard.py`(只读根改为引用,删本地拷贝)、
  `apps/api/kongming/internal/meta.py`、`apps/api/kongming/rag/repository.py`
  (既有引用面经 firewall re-export 保持不变,禁止出现第三处)。
- **信任边界(ADR-002)**:liannu 是进程内纯计算、零凭证、零 IO,收进常量不改变 agent 面
  无凭证属性;agent 继续不持有任何数据库配置。
- **无 schema 变更**(ADR-004);**无 OpenAPI 信封变更**(ADR-005 零触发)。
- **版本纪律**:liannu 升版须同步 `apps/{api,agent}/pyproject.toml` 的 `liannu==` 声明
  (仓内既有约定);一致性断言同时兜住"引擎被降级到旧版"这一路径。
- **测试先行**(宪法 Article II/III + rules.tasks):安全规则类,先写失败测试再实现。
