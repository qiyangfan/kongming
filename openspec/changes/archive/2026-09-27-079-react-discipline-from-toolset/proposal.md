# 079-react-discipline-from-toolset 提案:ReAct 纪律段按注入工具集派生

## Why

`apps/agent/kongming_agent/graph/react/prompts.py:13-27` 的 `REACT_DISCIPLINE` 用散文
**点名了四个具体工具**:`rag_retrieve`、`validate_sql`、`execute_sql`、
`create_system_announcement`。而实际可用工具集是控制面按调用者**实时权限**过滤后注入的
(`chat/router.py:309 authorized_tools` → `QueryReactIn.tools` → `react/nodes.py:37 openai_tools`)。

`build_react_system` 只区分了两种形态:工具集为空 → 换成 `REACT_NO_TOOLS_DISCIPLINE`;
非空 → 用这份写死工具名的完整纪律段。**"有工具,但不是这四个"这一常态没有覆盖**——
而它恰恰是 ReAct 侧最普遍的形态:Bot 权限裁剪(025)、商店零工具闸、MCP 动态工具(028)、
按角色 `filter_specs(perm_codes)` 的任意子集。

后果三条,都可复现:

1. **教模型调用它没有的工具**:未持 `execute_sql` 的 Bot 仍被规则 1 指示"经 validate_sql
   校验后用 execute_sql 执行" → 触发 L2 `TOOL_PERMISSION_DENIED` → 白烧一轮,
   且模型大概率把失败归因为"工具不可用"答给用户(用户看到的是能力缺陷,真实原因是权限裁剪)。
2. **该告知的工具没告知**:注入的 MCP 工具在纪律段里没有任何提及,模型只能从参数 schema
   猜用法与适用时机。
3. **能力边界陈述会变成假话**:规则 3「除 `create_system_announcement` 外没有任何写工具」
   是对 `toolcall/registry.py` 的**全局静态断言**。注册表一旦新增写工具,这行字仍宣称
   "没有别的写工具"。这不是文档陈旧问题——058 把"任务完成度如实陈述"立为契约,
   而系统提示里的能力自述是同一条诚实性红线的另一半。

`graph/react/prompts.py` 的文件头注明纪律段「任何形态下强制保留,spec 立约」,
但那条契约(`custom-agent`「ReAct 系统提示与工具集一致性」)要的是**一致**,不是**静态**——
它现在只规定了两态,而两态本身就是这处漂移的产物。

同一问题的正解已在仓内:换成"按工具集形态选择纪律段"这一机制是对的,只是粒度停在
`0 vs >0`,没有推到 `0 vs 1..n`。本变更把粒度补齐,不新增机制。

参考项目对同一问题的结论可直接借用其原则:所有"能力边界"文本走单一出餐口,
源是工具配置本身,新增工具自动演进边界——静态枚举必然过期。

## What Changes

1. **纪律段里出现的工具名 SHALL 由本次注入的工具集渲染**;工具集里没有该工具时,
   引用它的那条条款 SHALL NOT 出现在提示词里。
2. **工作流教学按实际组合自适应**:缺 `rag_retrieve` 则不教检索、缺 `validate_sql`
   则校验步骤消失、缺 `execute_sql` 则不教执行——三条都在时,教学与现状逐字一致。
3. **写能力边界由元数据派生**:哪些工具可写、哪些需人工确认,取自注入的
   `ToolSpec` 的 `write` / `require_confirm`,SHALL NOT 静态点名;
   无任何写工具时 SHALL 明确陈述"当前没有任何写工具"。
4. **零回归红线(锁死,不靠自觉)**:
   - 工具集为空 → 与现状同串(`REACT_NO_TOOLS_DISCIPLINE` 路径不动);
   - 工具集恰为当前默认四件套 → 渲染结果与现 `REACT_DISCIPLINE` **逐字相同**;
   - 其余子集 → 只缺不多,已授权工具的条款文本不变。
5. 效率/终止纪律段中与具体工具绑定的表述(如规则 7 点名 `information_schema`、
   `rag_retrieve`)同上按可用集裁剪。
6. 按 `graph/react/prompts.py` 文件头的资产纪律登记本次改动(该段非上游逐字移植资产,
   可改,但须在头部 ledger 记一笔)。

## Non-goals

- **不做"自然语言能力清单"文本注入**:工具名与参数 schema 已随 `tools` 出站,再写一份
  清单进 prompt 是制造第二个会漂移的事实源。本变更的职责是让纪律段**不撒谎**,
  不是多加一段说明。
- **不改 `ToolDef` 结构、不改注册表、不改权限过滤逻辑**:`write`/`require_confirm`
  已存在,`filter_specs` 已是单点。
- **不动 web 侧 `TOOL_LABELS` 静态表**:未知工具名回落原始名已是诚实行为;
  为派生中文标签而给 `ToolDef` 加 display name 属契约扩张,收益不成立。
- **不改 ReAct 循环、预算、熔断、观察截断与终态同构**(`tool-calling` 域零触动)。
- **不做 MCP 工具用法自动生成说明**(动态工具的说明书属工具提供方职责,不在本仓生成)。
- **不改 Bot 系统提示的注入与替换语义**(身份段行为不变)。

## Capabilities

### Modified Capabilities

- custom-agent:「ReAct 系统提示与工具集一致性」由"空/非空两态选择"扩展为
  "纪律段内容按实际注入工具集与写/确认元数据派生",并固化三条零回归约束。

## Impact

- **落点**:仅 `apps/agent`——`graph/react/prompts.py`(纪律段改为可渲染模板 +
  常量拆分)、`graph/react/nodes.py` 或 `build_react_system` 调用点(传入工具集)。
- **信任边界(ADR-002)**:无新增凭证、无新增回调;工具集本就由控制面下发。
- **无 schema 变更**(ADR-004);**无 OpenAPI 信封变更**(ADR-005 零触发)——
  改的是发给模型的提示文本,不改响应字段。
- **效果验证**:纪律段变化会移动 065 的评估基线口径,故须按 065 对拍
  (ReAct 侧至少冒烟集不退化)后方可视为完成;默认四件套逐字相同这一断言把
  回归面压到"仅权限子集场景"。
