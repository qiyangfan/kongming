## MODIFIED Requirements

### Requirement: 结果图表渲染

查询结果携带图表建议(line/pie/bar/scatter)时 SHALL 渲染对应图表,
无建议(none/缺失)SHALL 不渲染图表区;图表配色 SHALL 从设计
token 派生,不硬编码色值。图表随结果卡片呈现,历史回显同样适用。
图表区 SHALL 提供四种类型切换器与 PNG 下载按钮;scatter 渲染分流
(数值散点/类目点图)与切换状态语义依 chat 域「图表可视化」立约。
temporal 类目轴(line/bar 时间轴)标签 SHALL 以本地日期格式呈现
(如 2005-07-05),不直接渲染 ISO 原文(2005-07-05T00:00:00);
scatter 悬停提示(tooltip)SHALL 呈现该点的 x/y 数值,信息量与
bar/line 悬停提示对齐。

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

#### Scenario: temporal 轴标签本地化
- GIVEN 应答 chart 建议 type=line 且 x 列为日期值
- WHEN 结果卡片渲染
- THEN 轴标签呈本地日期格式(如 2005-07-05),不含 ISO 时间后缀

#### Scenario: scatter 悬停提示含数值
- GIVEN 散点图已渲染
- WHEN 悬停任一数据点
- THEN tooltip 呈现该点 x 与 y 数值

### Requirement: 会话侧栏交互

侧栏会话列表 SHALL 按更新时间倒序展示当前用户在当前租户的会话,
支持新建/切换/删除;当前会话高亮;切换会话即切换对话区内容;
删除带确认且删除后列表与路由即时收敛;删除确认请求进行中,
取消操作 SHALL 不可用(防「请求已发出但用户以为已取消」的语义
误导),确认结果到达后对话框按既有受控语义收敛(成功关闭,失败
保持开并呈现错误);刷新页面后当前会话恢复(从持久化存储读回,
不强制回到首页)。

#### Scenario: 会话列表倒序与当前会话高亮
- GIVEN 用户在租户 T 下有多个会话
- WHEN 侧栏渲染
- THEN 按 updated_at 倒序列出,当前打开的会话呈选中态

#### Scenario: 新建对话
- GIVEN 用户在任一会话中
- WHEN 点击「新对话」
- THEN 对话区清空为初始提问态,不发起任何后端请求
- AND 后续首次提问自动新建会话并出现在列表

#### Scenario: 切换会话
- GIVEN 侧栏列出多个会话
- WHEN 点击另一会话
- THEN 对话区加载该会话历史消息,URL 反映当前会话(可收藏/刷新直达)

#### Scenario: 删除会话需确认且即时收敛
- GIVEN 当前打开会话 S
- WHEN 点击删除并确认
- THEN 会话及其消息被删除,侧栏列表移除 S
- AND 若 S 是当前会话,视图回到新建对话初始态

#### Scenario: 删除进行中取消不可用
- GIVEN 删除确认对话框已确认、删除请求进行中
- WHEN 对话框处于等待结果态
- THEN 取消操作(「再想想」)禁用不可点
- AND 结果到达后成功则关闭,失败则保持开启并呈现错误

#### Scenario: 刷新恢复当前会话
- GIVEN 用户正在会话 S 中刷新页面
- WHEN 应用壳重新加载
- THEN 自动回到会话 S 并完整回显历史消息
