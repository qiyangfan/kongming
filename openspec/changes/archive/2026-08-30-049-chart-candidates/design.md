# 049-chart-candidates 设计

## Context

现行链路:api 控制面 `suggest_chart(columns, rows)` 首中即停产出唯一建议
(日期→line;双数值列→scatter;首列基数≤8→pie;≤50→bar),前端 ToggleGroup 恒渲染
四类型。缺陷与动机见 proposal「Why」。约束:产物派生必须保持 026 的确定性纯函数
三路径同源(同步/流式/回显,回显缺档可重算);域模型零框架依赖;契约纯增(ADR-005)。

## Goals / Non-Goals

Goals:列角色画像根除标识列误判;形状→有序候选(0~3);两维分组与多度量获得正确图型;
前端只渲染候选。Non-goals 见 proposal(LLM 选型/新图型枚举/agent 面改动等,不再重述)。

## Decisions

### D1 列角色画像:名称信号为主、值形态为辅(纯函数)

对每列产出角色 ∈ `temporal / dimension / measure`,判定顺序(先中先停):

1. **temporal**:列值全部呈日期/时间形态(复用现 `_is_temporal`:date/datetime 实例
   或 ISO 日期前缀串)→ temporal。任意列可判(不再限于首列)。
2. **期间列**:列名小写 ∈ {year, quarter, month, week, day} → dimension
   (名称信号无条件生效,与 spec「恒判定为维度」一致——年份非构成整体,
   即便值形态异常落维度侧后果也安全)。
3. **标识列**:列名小写匹配 `(^|_)id$` / `^id_` / `(^|_)(no|code)$` → dimension。
   度量列命名域(count/sum/total/amount/price/rate/qty…)与该形态不相交,误伤面可忽略;
   `num` 后缀刻意不入信号——中文库 `order_num`(订单数)类计数量是度量,入信号必误伤;
   判定为维度即使语义有偏差(如 category_id)也落在维度侧,后果安全。
4. **整数稠密序列**:非 bool 整数、`min ∈ {0,1}`、连续(max−min+1==distinct)、
   distinct≤16、max≤64 → dimension(无名形态的小值域枚举/序号兜底;阈值刻意收紧)。
   **仅当降维后仍剩至少一个度量时生效**——防小值域计数列(如恰好 1..6 连续的 count)
   被降维后度量集归零、把本可配图的结果打成零候选;标识列(规则 3)名称信号强,
   无条件生效。
5. **measure**:其余非 bool 数值(int/float/Decimal)。
6. 其余(非数值)→ dimension(分类)。

中文列注释(041 标签)不参与画像:注释非必在,画像必须纯值面可复算(回显重算同源)。

### D2 pie 资格闸:可加性 + 标签唯一 + 值非负

pie 表达「部分合成整体」,度量必须可加。经 SQL 投影判定:y 列对应的投影为聚合调用且
函数 ∈ {COUNT, SUM} → 可加;AVG/MIN/MAX/表达式/裸列/**解析失败** → 不可加(fail-closed,
pie 是锦上添花,排除比误推更符合「宁缺毋滥」)。另要求:x 标签在行集内唯一
(distinct==行数,GROUP BY 键语义)、x 非期间维(年份不是构成整体)且该列值全非负。
条件齐备 pie 才入候选,排 bar 之后。

### D3 形状→候选映射(有序、去重:每类型至多一个)

记 T=temporal 维、D=分类维、m=度量;维基数=行集内 distinct 值数:

| 形状 | 候选(按序) | 绑定 |
|---|---|---|
| 含 T,另有低基数分类维(≤8) | [line] | x=T, y=全部度量(截前 3), series=分类维(时序分组) |
| 含 T,无其他维 | [line] | x=T, y=全部度量(截前 3) |
| 单分类维,card≤8,标签唯一 | [bar, (pie)] | bar: x=D, y=全部度量;pie: x=D, y=[首度量](过 D2 闸) |
| 单分类维,card 9~50 | [bar] | x=D, y=全部度量 |
| 两分类维 + 单度量,低基数维≤8 且高基数维≤50 且 x 标签跨行重复 | [bar] | x=高基数维, y=[该度量], series=低基数维(分组柱状图;标签重复闸排除小表明细——真 GROUP BY 形状 x 标签必跨系列重复,明细行每行一个 x 值) |
| 无维 + ≥2 度量 | [scatter] | x=度量1, y=[度量2] |
| 其余(单度量无维/任一维 card>50/两维多度量/≥3 维) | [] | 零候选,不配图 |

- 分组柱状图**不新增类型枚举**:仍 bar,series 为绑定字段;前端图标/下载/调参零新增。
- y 聚合为列表是多度量的自然表达;pie/scatter 恒单 y;散点 ≥3 度量只取前二
  (散点矩阵非目标)。
- 两维+多度量零候选:信息维度过高,任何单图必失真,宁缺毋滥。

### D4 领域模型与契约形态(纯增)

- `chat/models.py`:新增 `ChartCandidate`(frozen:`type: str, x: str|None,
  y: tuple[str, ...], series: str|None`)与 `suggest_charts(columns, rows, sql="") ->
  list[ChartCandidate]`(内部 `_profile_columns` 画像);**删除** `suggest_chart`
  (三个调用点同批迁移)。`ChartSuggestion` 保留为「默认单图」值对象(type/x/y 语义
  不变),由首候选展平构造——落库 `chart` 键与 `ChatOutcome.chart` 契约形态不变。
- `ChatOutcome`/`MessageResult` 增加 `chart_candidates`;落库 result_json 纯增
  `chart_candidates` 键;`_to_message` 读档优先读候选,存量档缺失时以
  `suggest_charts` 重算(落库输入=原始输入,重算=原始输出,同源不破)。
- `router.py`:新增 `ChartCandidateOut(type, x, y: list[str], series)`(y 为数组是
  新生对象的契约,与存量 `ChartOut.y: str|None` 并存不冲突);`ChatQueryOut` 与
  `MessageResultOut` 纯增 `chart_candidates: list[ChartCandidateOut] = []`。
  `chart` 字段保留=首候选展平(旧读者零回归)。SSE result 帧经 `_out` 自动携带。
- `make contracts` 重生成;类型纯增,漂移门禁零删改。

### D5 前端:候选驱动渲染

- `chart-shared.ts`:内部绑定类型改为 `{type, x: string|null, y: string[],
  series: string|null}`;存量单图 normalize 为 `y=[y], series=null`。
- 切换器:渲染候选去重类型后的图标序列(顺序=候选序,类型仍四种、图标复用);
  候选≤1 时**不渲染** ToggleGroup(仅留下载按钮)。
- 渲染:`y.length>1 或 series 非空` → bar/line 多系列(series 数组 + bottom 图例,
  图例与 pie 同款不挤压绘图区);pie 恒单 y;scatter 绑定不变(x=度量1)。
- 回落语义:useEffect 依赖改为候选列表指纹,回落到首候选绑定。

### D6 三路径同源(026 保持)

写入(`_finalize` 算一次)→ 同步 JSON / 流式 SSE 同一 `_out`;回显读档,缺档重算。
候选函数为纯函数,同样输入恒同样输出——确定性使三路径无需额外同步机制。

## Risks / Trade-offs

- [SQL 不可解析时 pie 恒被排除] → fail-closed 是刻意取舍:pie 收窄为一等场景
  (聚合问数,SQL 必然简单可解析);解析失败属异常路径,少一个候选无害。
- [稠密序列阈值误伤小值域计数列] → 后果=零候选不配图(表格即答案),不产生错误图;
  阈值收紧至 min∈{0,1}/distinct≤16/max≤64,真实计数分布极少命中。
- [多系列图例挤压画布] → 图例置底与 pie 同款;series 维基数≤8 硬闸,图例项可控。
- [契约 y 数组与存量 y 字符串并存] → 分属两个对象(候选 vs 旧单图),无同名字段歧义;
  前端生成类型自然区分。
- [旧前端/缓存读者只认 `chart` 字段] → `chart` 保留且语义不变(首候选展平),零回归。

## Migration Plan

无 schema 迁移、无数据回填:result_json 为 JSON 文本,新键向后兼容;存量档走单图分支。
回滚=还原代码(读档对多余键忽略),数据无损。

## Open Questions

无。阈值类参数(min∈{0,1}/distinct≤16/系列≤8/y 截前 3)已在 D1–D3 定值,
由测试钉集固化;若实测误伤再以测试用例形式修订,不构成方案级悬念。
