# chat 能力域 delta(011 图表增强)

## MODIFIED Requirements

### Requirement: 图表可视化

图表区支持 bar/line/pie/scatter 四种类型切换,按列特征推断默认类型(日期列→line 时序趋势,低基数分类≤8 类→pie 占比,双数值列→scatter 散点),图表可下载 PNG。

手动切换 SHALL 为当前结果卡片内的本地状态:切换只改变系列形态,SHALL 不改变图表建议的 x/y 列映射(映射恒定);页面刷新或新消息进入后回落到后端建议类型。scatter 渲染 SHALL 按 x 列全数值探测分流:x 列全部可数值化时渲染数值-数值散点,否则退化为类目轴点图,不因非数值丢点致整图空白。

#### Scenario: 多类型图表切换
- GIVEN 查询返回可可视化的结果
- WHEN 结果展示
- THEN 图表区支持 bar/line/pie/scatter 四种类型切换
- AND 切换不改变 x/y 列映射,仅改变系列形态

#### Scenario: 切换状态回落
- GIVEN 用户已手动切换图表类型
- WHEN 页面刷新或同卡片新结果进入
- THEN 图表类型回落到后端建议类型

#### Scenario: 图表类型推断
推断优先级:首列日期 → line;否则存在两个数值列(任意位置)→ scatter;
否则按首列基数走 pie/bar。
- GIVEN 结果首列含日期
- WHEN 推断默认类型
- THEN 默认 line(时序趋势)
- GIVEN 结果存在两个数值列(任意位置,含标识列前置形态)
- THEN 默认 scatter(散点),x/y 取前两个数值列
- GIVEN 结果首列为低基数分类(≤8 类)且仅一个数值列
- THEN 默认 pie(占比)

#### Scenario: scatter 退化渲染
- GIVEN 用户手动切换到 scatter 且 x 列含非数值
- WHEN 渲染
- THEN 退化为类目轴点图,不因非数值丢点致整图空白

#### Scenario: 下载图表 PNG
- GIVEN 图表已渲染
- WHEN 点击下载
- THEN 生成 PNG(背景取当前主题卡片底色,非透明)并由浏览器下载「图表-<时间戳>.png」
