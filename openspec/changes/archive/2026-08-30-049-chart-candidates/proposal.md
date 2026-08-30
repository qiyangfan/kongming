# 049-chart-candidates 提案:图表候选选择机制

## Why

现行图表链路对任何成功结果只产出一个建议类型,且 spec 钉死前端「bar/line/pie/scatter
四种类型切换恒全量提供」——不适配的数据也被四类图全量兜出。同时推断规则把整数标识列
当度量:两维分组结果(如「每个门店库存中各影片分类的库存数量」= store_id×category×count)
命中「双数值列→scatter」,建议 scatter(x=store_id),值轴 scale 切出 1.0/1.2/1.4… 碎刻度,
line/bar 对该形状同样不成立——BI 最常见的两维分组形状反而没有任何正确图型(缺分组柱状图)。
目标:参照 Tableau「Show Me」/Metabase 的业界做法,以列角色画像 + 形状规则给出**有序候选
列表(0~3 个)**,前端只渲染候选,默认取排名最高者;不适配的数据诚实地不配图(表格即答案)。

## What Changes

- **推断升级(纯函数,零 LLM)**:`suggest_chart` 重构为列角色画像 + 形状→候选排序:
  - 列角色:temporal(值形态日期/期间列名)/ dimension(非数值、标识形态、整数稠密序列)/
    measure(其余数值);标识列(store_id 等整型 ID)与期间列(year 等)永久丧失度量资格,
    根除 scatter 误判与碎刻度;
  - 形状→候选(每个候选自带 x/y/series 绑定,≤3 个,每类型至多一个):
    时间维→line;单分类维→bar 为主、pie 按「可加和+全正」语义闸为辅;
    双度量无维→scatter;两维+单度量→**分组柱状图**(新能力:bar 支持 series 维);
    多度量→bar/line 多 y 系列;明细/不适配→零候选不配图;
  - pie 资格闸经 sqlglot 投影判定聚合可加性(COUNT/SUM 入选,AVG/比率排除)+ 标签唯一+值全正。
- **契约纯增(ADR-005)**:`ChatQueryOut`/`MessageResultOut` 新增 `chart_candidates`
  (候选数组,type ∈ 既有四枚举零扩充);既有 `chart` 字段保留=候选首项展平(存量兼容);
  落库 result_json 随档存候选,回显缺档纯函数重算——同步/流式/回显三路径同源不破(026)。
- **前端渲染适配**:切换器只渲染后端候选;单候选隐藏切换器(仅留下载);零候选不出图表卡;
  切换语义从「共享 x/y 映射改系列形态」改为「候选自带绑定」;bar/line 支持多系列渲染
  (分组柱状图=series 维、多度量=多 y)。
- **spec「图表可视化」Requirement 改写**:推断优先级条款→角色画像候选规则;
  「四种类型切换」→「按候选渲染」;新增分组柱状图/单候选隐藏/零候选场景。

## Non-goals

- **不做 LLM 选型**:破坏 026「产物确定性派生、三路径同源」架构(回显重算需纯函数、
  逐查询多一次 LLM 成本与延迟、同数据两次回答不同图违背「回显=事实源」);
  远期若引入,仅允许「LLM 在纯函数候选集内挑默认」,不扩集、不自由生成。
- 不新增图型枚举:不做堆叠柱状图、散点按维着色、双 Y 轴、热力图、面积图;
  分组柱状图复用 bar 类型(series 绑定),前端图标/下载/调参零新增。
- 不改 agent 面:图表派生恒在 api 控制面,agent prompt/链路/回调契约零变化(ADR-002 不涉)。
- 不动导出 CSV/Excel、图表调参(tune)、下载 PNG 能力。
- 存量消息不迁移:旧档读出按既有单 chart 渲染;重算路径自然获得候选。

## Capabilities

### Modified Capabilities

- `chat`:「图表可视化」Requirement 改写——推断规则从「唯一建议+固定优先级」改为
  「列角色画像+有序候选」;切换条款从「四类恒全量」改为「按候选渲染+单候选隐藏」;
  新增分组柱状图(series 绑定)、多度量系列、pie 资格闸、标识列降维场景。

## Impact

- **apps/api**:`chat/models.py`(角色画像+suggest_charts 纯函数)、`chat/service.py`
  (调用点/落库/ChatOutcome 扩展)、`chat/router.py`(ChatCandidateOut + `_out`/`_msg_out`)。
- **contracts**:`make contracts` 重生成 openapi.json 与前端类型(纯增,漂移门禁过)。
- **apps/web**:`chart-shared.ts`(候选/绑定类型)、`chart-view-impl.tsx`(候选切换器+
  多系列渲染)、`chart-view.tsx`(回落语义)、`assistant-card.tsx`(装配候选)。
- **测试**:`test_chat_query.py` 推断钉集改写扩充(标识列/期间列/两维/多度量/pie 闸)、
  `test_artifacts.py` 对齐、契约漂移 CI。
- **无 schema 迁移**(result_json 为 text JSON,结构向后兼容);无 agent 改动;无新依赖。
