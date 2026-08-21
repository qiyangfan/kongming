# 011 图表增强 — 设计

## Context

现状(动机见 proposal.md):
- api `chat/models.py::suggest_chart` 纯函数产出 `ChartSuggestion(type,x,y)`,
  值域 none/line/pie/bar;「首列即数值」判 none 不配图——双数值列(最典型
  散点场景)无图表区,手动切换入口都不存在。
- web `chart-view.tsx` 按 chart.type 渲染 pie/line/bar,x/y 列索引由建议
  给出(缺省第 0/1 列);无切换器、无下载钮、无 scatter 分支。
- 契约 `ChartOut.type`/`ChartInfoOut.type` 为自由 string(无枚举),值域
  扩 scatter 零契约漂移。
- 落库/回显双路:查询时算一次落 result_json;回显读档优先,仅存量缺 x/y
  的旧档由 suggest_chart 确定性重算兜底(006 既定)。

约束:web 裸元素红线(切换器必须组件库);echarts 读不了 CSS 变量,色值
经 getComputedStyle 从 token 派生(006 既定模式);echarts-for-react 已用
canvas renderer(getDataURL 前提);上游调研结论见提案背景(scatter 双形态、
PNG 背景色、切换不重置映射)。

## Goals / Non-Goals

**Goals:**
- suggest_chart 补双数值→scatter 分支,既有三规则逐字不动
- 图表卡片内四类型切换器 + PNG 下载钮,全部 shadcn/lucide 组件
- scatter 双形态渲染(数值散点/类目点图),全列数值探测分流
- 切换状态卡片本地、刷新/新结果回落建议类型

**Non-Goals:**
- 不改 chart 契约结构(type+x+y 轻建议形态不变)
- 不做列重选/多系列/暗色主题/其他图型(proposal Non-goals 已列)

## Decisions

### D1: scatter 推断=任意位置双数值列,x/y 取前两个数值列(吸收列序解耦)

suggest_chart 现有短路序:temporal→line ⇒ 首列数值→none ⇒ 基数≤8→pie ⇒
≤50→bar。改为:**全列扫描数值列(含首列),存在≥2 个数值列即 scatter
(x=第一个数值列,y=第二个数值列)**;仅 1 个数值列时首列数值→none 维持、
首列分类→pie/bar 维持;temporal 首列优先级不变(日期列非数值,天然互斥)。

- 为什么解耦列序(012 前的可读性修缮,吸收进 011):生成 SQL 的 prompt
  会给明细查询附带标识列(名称/标题),形态如 [title, length, rental_rate]
  ——若 scatter 仍要求首列数值,标识列前置的双数值结果会误走 pie/bar。
  推断自己变聪明,优于用 prompt 列序约束做脆弱耦合。
- 语义等价面:首列数值+后继数值的原形态(数值列恰为前两个)输出不变;
  [title, 数值] 单数值形态仍走分类路径。
- 备选(否):prompt 规定「标识列放最后」——把图表推断的列序假设泄漏进
  prompt,LLM 遵从度不稳且对自然 SQL 形态(标识列习惯在前)别扭。

### D2: 切换器=shadcn toggle-group(Radix 单轨)

`pnpm dlx shadcn@latest add toggle-group` 引入(006 已切 radix-ui 聚合包,
预期零新依赖类别)。type="single" 四选一,激活态走 accent token。

- 备选(否):Button 组手动管激活态(重复造 toggle 语义,裸元素红线边缘);
  Select 下拉(多一次点击,且「类型=可视化维度」语义不贴);Tabs(语义是
  面板切换,会暗示内容替换)。
- 006 R4 纪律:引入后核验其引用的语义类(toggle 用 bg-accent/text-accent-
  foreground 等)在本仓 index.css @theme 有定义;toggle-group 无动画类依赖。
- 四类型恒可切换,不做可用性禁用(pie 高基数扇区不可读等由用户负责;
  spec 字面「四种类型切换」无限制条件;退化渲染兜底不散架)。

### D3: scatter 双形态渲染,x 列全数值探测分流(上游逻辑移植)

- xNumeric = x 列每一行值均非 null/undefined/'' 且 Number.isFinite(Number(v))
  ——必须显式排除 null:JS `Number(null)===0` 会把空值误判为数值 0(上游
  Java instanceof Number 天然免疫,JS 侧需手挡)。
- xNumeric=true:xAxis type=value,series type=scatter,data=[[x,y],…]
  二元组,tooltip trigger=item。
- xNumeric=false:xAxis type=category + labels,series type=scatter,
  data=一维 values(类目轴点图),tooltip 与 bar/line 同构。
- 为什么必须分流(上游实证):value 轴遇 NaN 静默丢点→整图空白,无任何
  报错线索。every 语义=一行脏数据即整体退化,宁退化不丢点。

### D4: PNG 下载=getDataURL + token 背景 + 中文时间戳文件名

- `echartsRef.getEchartsInstance().getDataURL({type:'png', pixelRatio:2,
  backgroundColor: cssVar('--card')})`——背景显式取卡片底色 token,防透明底
  贴到深色查看器上难看(上游同款处理;Kongming 无暗色体系,取当前值即对)。
- 文件名「图表-yyyyMMdd-HHmmss.png」,本地时间格式化(与 010 服务端
  「查询结果-<时间戳>」同构,前端自算)。
- 触发:动态 a 元素 href=dataURL + download 属性,**入 DOM 后 click,再
  移除**(010 Safari 教训:不入 DOM 的锚点 click 在 Safari 静默无效;
  dataURL 非 blob,无 revokeObjectURL 需求)。
- 图标 lucide ImageDown(语义=导出图片,与 ExportButtons 的 Download
  区分)。

### D5: 切换状态=卡片本地 useState,prop 变更重置

activeType 初值=chart.type;useEffect 在建议类型/x/y/列/行变更时重置回建议
类型。依赖取基元(chart.type/x/y)+ data.columns/data.rows 数组引用
(react-query structural sharing 保证缓存内稳定)——**不能依赖外层内联组装
的包装对象**(assistant-card 每次 render 新建 `{columns,rows}` 字面量与
normalize 产物,包对象恒新引用会让父组件无关重渲染(如输入框逐键)误重置
手动切换;实施自查抓回并修正,浏览器实证「切 pie 后打字不重置」)。
上游坑①:用户手动切换被下一条消息静默覆盖是既定语义——刷新/新结果回落
建议值,spec 已立约;上游坑②:列表复用组件实例残留旧类型——消息列表
key=msg.id 天然隔离,useEffect 双保险。

### D6: 布局=卡片头行左切换器右下载钮,图表本体不变

图表区卡片(assistant-card 既有 rounded border 容器)内顶部加一行 flex:
左 ToggleGroup(xs),右 ImageDown ghost icon 钮;ReactECharts 高度 260、
canvas renderer、notMerge/lazyUpdate 维持。切换只改 option 系列段,经
notMerge 全量重绘(echarts 对 pie↔笛卡尔互转的坐标轴残留天然忽略,上游
注释实证无害)。

## Risks / Trade-offs

- [scatter 推断改变「首列数值」存量行为的对外观感:此前无图,此后出散点]
  → 属 spec 立约兑现;双数值散点正是该形态的合理默认;测试锁定新行为。
- [toggle-group 新组件引入重蹈 006 R4 语义类缺失] → 引入后按纪律核验
  index.css 覆盖 + 浏览器实测激活态/聚焦态,纳入冒烟清单。
- [切换 pie 到高基数结果扇区爆炸不可读] → 接受(spec 允许自由切换);
  默认推断的基数门槛仍在,只是手动选择不设卡。
- [getDataURL 在入场动画进行中点击可能导出不完整帧] → echarts 默认动画
  ~1s,用户从看到图到点下载的间隔大概率已过;登记观察,不设卡。
- [null 误判数值 0 致散点 x 轴错位] → D3 探测显式排除 null/undefined/'',
  冒烟覆盖含 NULL 行的结果集。
- [导出 PNG 背景取 --card 与图表实际容器(bg-card)耦合,未来容器换色
  会漂移] → 同一 token 引用,换色时同步正确,非硬编码。

## Migration Plan

纯行为变更:零表结构、零迁移、零种子、零新端点。部署=常规发版;
回滚=revert 即恢复(存量落库 chart 数据不受影响)。

## Open Questions

- pixelRatio=2 对印刷/大屏场景是否够用:默认 2(约 2 倍清晰度)覆盖
  汇报贴图主流场景;更高倍率配置化随真实诉求再加,不预设。
- 手动切换的类型偏好是否应跨消息记忆(会话级「我喜欢看 scatter」):
  spec 已立约回落建议类型,偏好记忆随 personalization 远期评估。
