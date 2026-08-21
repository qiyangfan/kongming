# 011 图表增强 — 实施任务

> 本变更无安全面;api 纯函数仍按项目惯例测试先行(先写失败测试再实现)。
> web 无测试基座(HANDOFF 010 挂账⑭),呈现层以 Playwright MCP 冒烟覆盖。
> 每完成一组跑对应 make check 子集;动 api 模型后跑 make contracts 复验。

## 1. 后端推断补 scatter(api,测试先行)

- [x] 1.1 失败测试先行——suggest_chart 新用例:双数值列(任意位置)→scatter 且 x/y=前两个数值列;标识列前置([title,length,rental_rate])与双度量聚合([分类,count,avg] 侵占 pie/bar 形态)均锁定;首列数值但无第二数值列→none 维持;首列 temporal 仍优先进 line(不被 scatter 分支截胡);首列含 None 行的双数值→scatter(行级容错);既有 line/pie/bar/none 用例零回归;跑红
- [x] 1.2 `chat/models.py::suggest_chart` 实现:全列扫描数值列(含首列),≥2 个即 scatter(x=第一个数值列,y=第二个);temporal 首列优先 line 不变;仅单数值列时首列数值→none、首列分类→pie/bar 不变(吸收「列序解耦」:prompt 附标识列后数值列未必在首列,推断解耦优于约束 prompt 列序)
- [x] 1.3 落库/回显双路核验:查询落库路径与 service 缺 x/y 旧档重算路径均能产出 scatter(读档优先语义不变,存量完整档不受影响);`make check` api 子集绿
- [x] 1.4 `make contracts` 复验 openapi.json KEPT(chart.type 为自由 string,预期零漂移;若有意外漂移查明原因再定夺)

## 2. web 组件引入(shadcn toggle-group)

- [x] 2.1 `pnpm dlx shadcn@latest add toggle-group` 引入组件;按 006 R4 纪律核验:组件引用的语义类(bg-accent/text-accent-foreground 等)在本仓 index.css @theme 有定义、无动画类依赖、radix-ui 聚合包单轨不新增散包;`tsc -b` + eslint 绿

## 3. chart-view 增强(scatter/切换器/PNG 下载)

- [x] 3.1 scatter 双形态渲染:x 列全数值探测(显式排除 null/undefined/'' 防 Number(null)===0 误判)→ xAxis value + series scatter 二元组 [[x,y],…] + tooltip item;否则退化 xAxis category + 一维点图;色板/轴色 token 派生维持
- [x] 3.2 手动切换器:卡片头行左侧 ToggleGroup(type="single",四类型 bar/line/pie/scatter,xs 尺寸);activeType 卡片本地 useState 初值=chart.type;useEffect 重置(依赖=chart.type/x/y 基元+data.columns/rows 数组引用,react-query structural sharing 稳定——实施自查修正:外层内联包装对象恒新引用,父组件无关重渲染(输入框逐键)会误重置手动切换);切换只换系列形态,x/y 映射恒定;四类型恒可切换不做禁用
- [x] 3.3 PNG 下载:头行右侧 Button(ghost/icon,lucide ImageDown)经 ReactECharts ref → getEchartsInstance().getDataURL({type:'png', pixelRatio:2, backgroundColor: cssVar('--card')});动态锚点入 DOM→click→移除(Safari 形态);文件名「图表-yyyyMMdd-HHmmss.png」本地时间
- [x] 3.4 assistant-card.tsx 核验:ChartView props 签名未变(chart+data),既有调用点零改动兼容;chart.type=none 不渲染图表区语义不变;`tsc -b` + eslint 绿

## 4. 冒烟与收尾

- [x] 4.1 真 LLM + Playwright MCP 冒烟:①双数值列问答(如「各价格区间的电影数与租金」类)出 scatter 默认;②四类型来回切换渲染正确、映射不变;③类目列结果手动切 scatter 退化点图不空白;④含 NULL 行结果切 scatter 不错位丢点;⑤PNG 下载(文件名中文时间戳、背景非透明、2 倍密度);⑥刷新页面回落建议类型;⑦既有 bar/pie/line 推断零回归;⑧chart=none 结果仍无图表区;⑨切换器激活态/聚焦态视觉核验(006 R4 纪律)
- [x] 4.2 `make check` 全量绿;HANDOFF.md 更新当前状态与已知 TODO(动画帧导出/偏好记忆等挂账登记)
