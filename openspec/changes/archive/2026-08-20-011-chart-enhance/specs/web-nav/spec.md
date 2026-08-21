# web-nav 能力域 delta(011 图表增强)

## MODIFIED Requirements

### Requirement: 结果图表渲染

查询结果携带图表建议(line/pie/bar/scatter)时 SHALL 渲染对应图表,
无建议(none/缺失)SHALL 不渲染图表区;图表配色 SHALL 从设计
token 派生,不硬编码色值。图表随结果卡片呈现,历史回显同样适用。
图表区 SHALL 提供四种类型切换器与 PNG 下载按钮;scatter 渲染分流
(数值散点/类目点图)与切换状态语义依 chat 域「图表可视化」立约。

#### Scenario: 按建议渲染图表
- GIVEN 应答 chart 建议 type=pie
- WHEN 结果卡片渲染
- THEN 呈现饼图,系列色从设计 token 派生

#### Scenario: scatter 建议渲染
- GIVEN 应答 chart 建议 type=scatter
- WHEN 结果卡片渲染
- THEN 呈现散点图,系列色从设计 token 派生

#### Scenario: 无建议不渲染图表
- GIVEN 应答 chart 建议 type=none
- WHEN 结果卡片渲染
- THEN 不出现图表区(表格照常呈现)

#### Scenario: 图表区操作件呈现
- GIVEN 图表已渲染
- WHEN 结果卡片呈现
- THEN 图表区含四种类型切换器与 PNG 下载按钮
