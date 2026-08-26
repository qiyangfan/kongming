# web-nav 能力域

## Purpose

前端主布局的导航与外壳契约:应用壳结构、会话侧栏交互、RBAC 菜单守卫、
路由分流(未认证/无权限/首登改密)、租户切换与响应式行为——
「对话即主体验」的可观察行为在此立约(roadmap D 层钦定随行立 spec)。

## Requirements

### Requirement: 应用壳结构

登录后的受保护区 SHALL 呈现统一应用壳:左侧导航侧栏(品牌区、「新对话」
入口、会话列表、能力导航区、用户区)+ 右侧内容区。侧栏导航区 SHALL 配置
驱动——导航项声明标识/文案/图标/所需权限点,新增能力域入口零改壳代码。
内容区按路由渲染对话页或后续能力页。

#### Scenario: 登录后进入应用壳
- GIVEN 已登录且非首登改密用户访问受保护路由
- WHEN 页面渲染
- THEN 呈现侧栏(品牌区/新对话/会话列表/导航区/用户区)与内容区

#### Scenario: 品牌区呈现
- GIVEN 应用壳渲染
- WHEN 查看侧栏品牌区
- THEN 呈现品牌标识与「万事,问孔明。」主口号

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

### Requirement: RBAC 菜单守卫

导航项 SHALL 声明所需权限点;当前用户权限集不含该权限点时菜单项
SHALL 不渲染(而非禁用置灰)。对话入口受 `chat:execute` 约束。
无任何受权限保护入口可见时,导航区不渲染空容器。

#### Scenario: 无权限菜单项不渲染
- GIVEN 用户权限集不含 system:user:manage
- WHEN 导航区渲染
- THEN 管理相关入口不出现在侧栏(不置灰、不隐藏占位)

#### Scenario: 权限变更后菜单即时反映
- GIVEN 用户登录态已建立
- WHEN 权限集经重新拉取用户信息更新
- THEN 导航区按新权限集重算可见性

### Requirement: 路由守卫分流

受保护路由 SHALL 分流:未认证 → 登录页;首登强制改密 → 改密页;
已认证但无该路由所需权限点 → 403 页(不回退静默隐藏);
未知路由 → 404 页。403/404 页均提供返回对话首页的出口。

#### Scenario: 未认证访问受保护路由
- GIVEN 无有效登录态
- WHEN 直接访问对话页 URL
- THEN 重定向登录页,登录后回到原目标

#### Scenario: 无权限直捣 URL 得 403
- GIVEN 已登录用户权限集不含目标路由权限点
- WHEN 直接输入该路由 URL
- THEN 呈现 403 页,不渲染路由内容

#### Scenario: 未知路由 404
- GIVEN 已登录用户
- WHEN 访问不存在的路由
- THEN 呈现 404 页,可返回对话首页

### Requirement: 租户切换

用户区 SHALL 提供租户切换器,列出当前用户可切换租户;切换后所有
会话相关请求 SHALL 携带新租户标识,会话列表与对话区按新租户刷新;
当前会话选择 SHALL 重置(不跨租户保留会话)。仅一个可切换租户时
展示当前租户(只读态)。选择持久化,刷新后保持。

#### Scenario: 切换租户刷新会话域
- GIVEN 用户绑定 default 与 sakila 两租户,正停留在 default 的会话中
- WHEN 切换到 sakila
- THEN 会话列表换为 sakila 下的会话,视图回到新建对话初始态
- AND 后续问答请求携带 sakila 租户标识

#### Scenario: 单租户只读展示
- GIVEN 用户仅绑定一个租户
- WHEN 查看用户区
- THEN 租户切换器呈只读态(无下拉交互)

### Requirement: 对话区渲染

对话区 SHALL 支持多轮问答:历史消息按时间升序回显(user 问句 +
assistant 应答);提问框常驻底部,Enter 发送(Shift+Enter 换行),
提问期间防重复提交;assistant 应答 SHALL 结构化呈现:摘要文案、
结果表格(列头 + 行,空结果有明确空态)、SQL 折叠查看、洞察列表、
统计信息;被拦截(REJECTED)与失败(FAILED)的应答 SHALL 有区别于
成功的视觉呈现并附原因文案。提问进行中对话区 SHALL 呈现流式过程
(事件契约见 query-streaming spec):阶段指示按节点完成顺序逐个点亮
(running 有进行中视觉,done 有完成视觉),触发自愈时显示
「第 N 次自愈重试」;sql 事件到达即渲染 SQL 折叠卡片(内容与终态
一致,多次下发以最后一次为准);summary 事件逐条追加呈现(打字机语义);
result 终态到达后流式过程呈现 SHALL 被完整结果卡片接管(与历史回显同形态)。
用户切会话/新建会话/切租户/切数据源/离开页面时 SHALL 中止在途流,迟到的流
事件 SHALL NOT 写入新上下文(守卫按流的发起上下文丢弃过期写入);
流异常结束(无终态)SHALL 按失败呈现并回填问句可重试。

#### Scenario: 完整问答回合渲染
- GIVEN 用户在会话中提交问题且问答成功
- WHEN 应答返回
- THEN user 问句与 assistant 应答按序追加;应答含摘要/表格/SQL(折叠)/洞察/统计

#### Scenario: 拦截态应答呈现
- GIVEN 问答被安全策略拦截
- WHEN 应答返回
- THEN 呈现拦截态视觉与策略原因文案,不渲染结果表格

#### Scenario: 提问中防重复提交
- GIVEN 一次提问尚未返回
- WHEN 再次尝试提交
- THEN 提交被阻止(输入保留,不产生重复请求)

#### Scenario: 空会话初始态
- GIVEN 新建对话未提任何问题
- WHEN 对话区渲染
- THEN 呈现品牌空态引导文案,提问框可用

#### Scenario: 流式阶段逐个点亮
- GIVEN 流式问答进行中
- WHEN 阶段事件依次到达
- THEN 阶段指示按到达顺序逐个从进行中视觉转为完成视觉

#### Scenario: SQL 提前渲染
- GIVEN sql 事件到达而终态未到
- WHEN 流式过程呈现
- THEN SQL 折叠卡片已渲染,内容与终态 SQL 一致

#### Scenario: 终态接管流式呈现
- GIVEN result 终态事件到达
- WHEN 呈现切换
- THEN 流式过程(阶段/打字机)被完整结果卡片替换,与历史回显同形态
- AND 会话列表与消息历史按新落库结果刷新

#### Scenario: 切换上下文丢弃在途流
- GIVEN 流式问答进行中
- WHEN 用户切换会话或租户或数据源或离开页面
- THEN 在途流被中止,流式过程呈现不残留到新上下文

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

### Requirement: 响应式外壳

应用壳 SHALL 响应式:窄屏(断点以下)侧栏收起为抽屉,经菜单按钮
唤出,遮罩点击关闭;对话区单列自适应;宽屏侧栏常驻。抽屉态下
守卫与交互行为与常驻态一致。

#### Scenario: 窄屏抽屉
- GIVEN 视口窄于断点
- WHEN 打开应用壳
- THEN 侧栏默认收起,菜单按钮可见
- AND 点击菜单按钮唤出抽屉侧栏,遮罩点击关闭且交互与常驻态一致

### Requirement: 查询错误态统一渲染

用户面与管理面所有数据查询(react-query useQuery 族)失败时 SHALL
以统一形态显式渲染:role=alert 的错误提示(「加载失败」语义文案,
支持变体文案),不得伪装成空态、不得静默空白、不得只依赖 console。
该形态 SHALL 由共享组件单一实现承载;新增页面 MUST 复用共享组件,
不得各自手写错误分支或省略错误态(038 用量页为首个范式,本增量
收敛为全站组件)。

#### Scenario: 查询失败显式呈现

- GIVEN 某管理页数据查询返回 5xx
- WHEN 页面渲染
- THEN 该页显示 role=alert 的加载失败提示(非空白非空态伪装)

#### Scenario: 共享组件单一实现

- GIVEN 多个页面均消费查询错误态
- WHEN 检查实现
- THEN 错误态由同一共享组件渲染,无页面级手写副本
