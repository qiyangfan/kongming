# 011 图表增强(chart-enhance)

## Why

chat 主 spec「图表可视化」已立约 bar/line/pie/scatter 四类型切换、默认类型推断与 PNG 下载,但自 005/006 起实现只兑现了 line/pie/bar 三类型的自动建议渲染:scatter 完全缺位,用户无法手动切换类型,图表也无法导出图片。更现实的缺口是:suggest_chart 对「首列即数值」的双数值列结果(最典型的散点场景)直接判 none,图表区整体不渲染,用户连手动切换的入口都没有。上游 nl2sql-data-platform 已验证手动切换/PNG 下载/scatter 双形态渲染的完整形态可直接借鉴。本变更兑现 chat spec 该项立约残余。

## What Changes

- **图表类型推断补 scatter 入口**:结果存在两个数值列(任意位置)时默认建议
  scatter,x/y 取前两个数值列(此前判 none 不配图);推断优先级
  temporal→line > 双数值→scatter > 低基数→pie/≤50→bar——双度量聚合形态
  (如 [分类,count,avg])默认散点,手动切换器可一键回饼/柱。
- **手动类型切换**:结果卡片图表区新增类型切换器,bar/line/pie/scatter 四类型自由切换;切换为当前消息卡片本地状态,不随类型重算 x/y 映射(与上游同构:映射恒定,换类型只换系列形态);新消息/页面刷新回落后端建议类型。
- **scatter 渲染双形态**:x 列全数值时渲染真 value-value 散点;否则退化为类目轴点图(上游实证坑:value 轴遇非数值静默丢点致整图空白,必须全列探测分流)。
- **图表 PNG 下载**:图表区新增下载按钮,经 ECharts getDataURL 导出 PNG(2 倍像素密度,背景色从设计 token 派生防透明底),文件名「图表-<时间戳>.png」与 010「查询结果-<时间戳>」同构。

## Capabilities

### New Capabilities

(无——全部能力归 chat/web-nav 既有能力域,遵守「一个 change 一个能力域」)

### Modified Capabilities

- `chat`: 修订「图表可视化」requirement——推断规则补「双数值列→scatter」scenario;四类型切换补「映射恒定/退化渲染/状态回落」scenario;PNG 下载补文件名与背景色 scenario。
- `web-nav`: 修订「结果图表渲染」requirement——chart 建议值域扩 scatter;图表区补切换器与下载按钮的呈现语义。

## Non-goals

- 图表 x/y 列的手动重选(切换类型不重算映射;列选择器属更重的可视化编排,随 conversation-artifacts/BI 域远期评估)。
- 后端永不下发完整 ECharts option(保持 type+x+y 轻建议形态;LLM 直出 option 的 artifacts 形态属上游 027 对应远期域)。
- 其他图表类型(雷达/漏斗/热力等)与多系列/双轴编排。
- 图表主题暗色模式(项目暂无暗色 token 体系;PNG 背景色取当前 token 值)。
- 会话维度批量导出图片、图表嵌入导出 CSV/xlsx(010 导出形态不变)。
- 图表交互深化(缩放 dataZoom、框选、下钻)。

## Impact

- **ADR 引用**:ADR-005(契约管线——chart.type 契约 schema 为自由 string,值域扩 scatter 零契约漂移,仍跑 `make contracts` 复验产物 KEPT)。ADR-002/003/004 不触发:纯 api 推断纯函数 + web 呈现层变更,零信任边界/认证/表结构变更,零新端点、零新权限点、零种子变更。
- **apps/api**:chat/models.py suggest_chart 纯函数补 scatter 分支(+单测:双数值→scatter/标识列前置/双度量聚合侵占 pie-bar 形态/首列数值无数值次列仍 none/既有规则零回归);service 落库与回显重算路径随之自然获得 scatter 值。**附带可读性热修(R2-F8 锚点)**:agent `graph/prompts.py` SQL 生成规则 3 重构(明细列举必须附标识名称列)与规则 6 确定性排序随本变更一并落地——scatter 列序解耦正是为消化该 prompt 行为(标识列前置);该资产自此不再与上游逐字一致(文件头注记)。
- **apps/web**:chart-view.tsx 重写(scatter 双形态渲染+切换器+PNG 下载;assistant-card 既有调用点 props 兼容零改动)、新 shadcn 组件 toggle-group(经 CLI 引入,按 006 R4 纪律核验语义类/动画类齐备);下载实现吸收 010 Safari 教训(锚点入 DOM、延迟触发)。
- **contracts**:值域变化在 schema 自由 string 内,openapi.json 预期 KEPT,重生成复验。
- **测试**:api 推断纯函数参数化用例;web 无测试基座(HANDOFF 010 挂账⑭),切换/下载/双形态经 Playwright MCP 冒烟覆盖。
