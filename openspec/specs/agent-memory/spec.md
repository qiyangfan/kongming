## Purpose

让 Agent 沉淀用户长期记忆(偏好/事实/目标),运行时按问题向量召回注入提示,跨会话「越用越懂你」;user_id + tenant_id 双重 fail-closed 隔离,用户对自己的记忆有完整数据主权。

## Requirements

### Requirement: 记忆存储与向量召回

系统 SHALL 以 user_memory 表存储用户长期记忆:content(文本)、category(分类)、confidence(置信度)、source(EXPLICIT/IMPLICIT)、2560 维 embedding、last_hit_at、dormant(休眠标志),user_id + tenant_id 双维归属。问答链路 SHALL 按当前 2560 维问句向量召回 top-3 非 dormant 记忆,召回查询 MUST 强制 user_id + tenant_id 双过滤(fail-closed,跨用户/跨租户永不召回);召回命中 SHALL 刷新 last_hit_at。无记忆或召回为空时 SHALL 不注入,链路行为与引入前逐字一致(零回归)。

#### Scenario: 记忆与查询使用相同向量维度

- GIVEN 用户记忆已生成 2560 维 embedding
- WHEN 问答链路使用问题向量召回
- THEN 查询向量同为 2560 维且维持 user_id + tenant_id 双过滤

#### Scenario: 召回注入仅本人本租户

- GIVEN 用户 A 在租户 T1 有记忆「偏好看周报口径」
- WHEN A 在 T1 发起相关问答
- THEN 召回该记忆并注入提示
- AND 用户 B(同租户)或 A 在其他租户召回不到该记忆

#### Scenario: 无记忆零回归

- GIVEN 用户无任何记忆
- WHEN 发起问答
- THEN 链路行为与 022 之前完全一致(无注入段,响应同构)

### Requirement: 记忆形成(显式 + 隐式)

系统 SHALL 支持两条形成路径:**显式**——用户输入含「记住/请记住」意图词时,系统提取其后内容形成记忆(confidence=1.0,source=EXPLICIT),并经助手答复告知已记住;内容命中槽位语义时改写偏好槽位(见「显式记忆分流」)。**隐式**——问答成功后后台任务经 LLM 提取候选,自 059 起仅允许产出偏好槽位补丁(见「隐式提取收窄」),SHALL NOT 再落自由文本 IMPLICIT 记忆。两条路径在落库前 SHALL 经 PII/凭证检测(复用 prompt 护栏规则集):命中密码/密钥/token/身份证/手机号等形态 SHALL 拒绝落库并记操作日志。

#### Scenario: 显式记忆

- GIVEN 用户输入「请记住:我看报表只关心月度口径」
- WHEN 系统处理
- THEN 落一条显式笔记(EXPLICIT),答复确认已记住

#### Scenario: 隐式只补槽位

- GIVEN 一轮成功问答(用户表达「回答尽量结论先行」)
- WHEN 后台提取任务执行
- THEN 仅补全 display_style 槽位,不落自由文本 IMPLICIT 记忆

#### Scenario: PII 拒绝落库

- GIVEN 用户输入「请记住:我的密码是 Xyz123」
- WHEN 形成前检测
- THEN 拒绝落库,答复说明敏感信息不记忆,落操作日志

### Requirement: 遗忘与置信度

系统 SHALL 将 confidence<0.3 或超过 90 天未命中(last_hit_at)的记忆标记 dormant;dormant 记忆 SHALL 不参与召回但在自管理页可见(可恢复——编辑即唤醒)。dormant 惰性标记 SHALL 强制 user_id + tenant_id 双过滤,仅作用于当前召回用户本人当前租户的记忆(与召回查询同隔离铁律,禁止跨用户跨租户批量翻牌)。v1 置信度固定(形成时定值,不做自动演化)。

#### Scenario: 休眠记忆不召回

- GIVEN 一条 90 天未命中的记忆
- WHEN 召回执行
- THEN 该记忆不在召回集内,自管理页可见其 dormant 状态

#### Scenario: 惰性休眠只作用本人本租户

- GIVEN 租户 T1 用户 A 存在应休眠记忆,租户 T2 用户 B 也存在应休眠记忆
- WHEN 用户 A(租户 T1)执行一次召回触发惰性休眠
- THEN 仅 A 在 T1 的记忆被标记 dormant;B 在 T2 的记忆不受影响

### Requirement: 记忆自管理(数据主权)

系统 SHALL 提供登录用户管理本人记忆的端点:列表(含 dormant)/编辑(内容修改;IMPLICIT 编辑或确认即视为用户认可)/删除/导出(JSON)。操作对象 MUST 为当前登录用户本人记忆(直接传他人记忆 id SHALL 404 同形,不暴露存在性);租户维度按当前租户上下文。形成/编辑/删除 SHALL 落操作日志。

#### Scenario: 本人记忆管理

- GIVEN 用户有 3 条记忆
- WHEN 列表/编辑/删除/导出
- THEN 操作生效且落操作日志

#### Scenario: 越权操作同形拒绝

- GIVEN 用户 A 持有用户 B 的记忆 id
- WHEN A 尝试编辑/删除该记忆
- THEN 返回 404(不暴露存在性),无副作用

### Requirement: 团队记忆双路召回

记忆召回 SHALL 合并两路:用户个人记忆 top-3 ∪ 用户所在团队的
团队记忆 top-3(user_memory.team_id 非空者,限成员团队),合并
注入形态与个人记忆一致;团队记忆仅可经显式写入(OWNER/MEMBER
成员,403 同形),隐式提取与休眠流转维持仅个人语义(team_id
NULL);非成员的团队记忆 MUST NOT 被召回。

#### Scenario: 双路注入

- GIVEN 用户有个人记忆,且其团队 T 有团队记忆
- WHEN 问答召回
- THEN 两路记忆一并注入

#### Scenario: 非成员不召回

- GIVEN 团队 T 的记忆
- WHEN 非 T 成员问答
- THEN 不注入

### Requirement: 闭合偏好槽位

系统 SHALL 以 (user, tenant) 单行配置存储用户偏好槽位,槽位集合封闭:preferred_datasource、default_time_range、metric_aliases、chart_pref、language、timezone、display_style。写入时未知键 SHALL 拒绝;更新 SHALL 以槽位为单位覆盖,不追加历史版本。槽位 SHALL 仅表达**默认值与表达习惯**,SHALL NOT 作为业务数据范围或 WHERE 约束源。显式更新省略的字段 SHALL 保留;显式 null SHALL 删除对应槽位;空对象、空字符串和空列表 SHALL 保持既有空补丁语义,不表示清空。归一化后没有非空设置或显式 null 删除的 PUT SHALL 返回当前值,SHALL NOT 创建或修改配置行、推进人工版本或使在途自动提取失效。显式 null 即使目标槽位已不存在也 SHALL 作为有效人工指令推进版本。并发更新 SHALL 基于最新配置合并。

#### Scenario: 槽位读写

- GIVEN 用户设置 preferred_datasource=default、default_time_range=近30天
- WHEN 保存并再次问答
- THEN 槽位持久化且下次可读回
- AND 未知键字段被拒绝

#### Scenario: 未提及槽位保持不变

- GIVEN 用户已有语言与图表偏好
- WHEN 仅更新语言或提交空对象
- THEN 未提及的槽位保持不变,空对象不删除任何值

#### Scenario: 显式空值删除单个槽位

- GIVEN 用户已有 language 与 timezone
- WHEN 显式提交 language=null
- THEN language 被删除,timezone 保留
- AND 空字符串和空列表不被解释为删除

#### Scenario: 空补丁没有写入副作用

- GIVEN 一轮自动提取仍在执行,用户提交的 PUT 归一化后既无非空设置也无显式 null
- WHEN 服务处理该请求
- THEN 返回当前配置,不创建或修改配置行,不推进人工版本,不使该自动补丁失效
- AND 不记录实际修改审计

#### Scenario: 并发更新不同槽位均保留

- GIVEN 两个人工请求更新本人本租户的不同槽位
- WHEN 请求并发到达
- THEN 两项修改都被保存,同槽位按实际提交顺序生效

### Requirement: 隐式提取收窄

会话后隐式记忆提取 SHALL 只允许产出偏好槽位补丁,未知字段 SHALL 丢弃,空补丁 SHALL NOT 落库。系统 SHALL NOT 将对话中的业务事实、事件或结论写入长期记忆,SHALL NOT 再产生自由文本 IMPLICIT 记忆。自动提取 SHALL 只补当前缺失槽位,不覆盖已有值或执行删除;源问答开始准备后若发生人工修改、删除或清空,旧问答的自动补丁 SHALL 放弃。提取或版本读取失败 SHALL 不影响问答,不将未知版本当成初始版本。人工显式写槽位 SHALL 保持覆盖语义。

#### Scenario: 只补槽位

- GIVEN 一轮对话含「上月销售额是 100 万」与「回答尽量结论先行」
- WHEN 隐式提取
- THEN 不落业务事实「上月销售额 100 万」
- AND 仅 display_style 槽位可被补全
- AND 槽位集之外的偏好一律丢弃

#### Scenario: 空补丁不落库

- GIVEN 对话无可提取偏好
- WHEN 隐式提取
- THEN 无任何写入

#### Scenario: 隐式提取不覆盖已填槽位

- GIVEN 用户已设置 display_style
- WHEN 自动提取包含另一个 display_style
- THEN 已有值保持不变,用户显式修改仍可覆盖

#### Scenario: 人工修改后旧提取不得覆盖

- GIVEN 一轮问答准备完成后,用户修改了偏好
- WHEN 该旧问答的自动提取任务稍后开始或完成
- THEN 其补丁被放弃,不覆盖人工修改

#### Scenario: 清空后旧问答不得恢复偏好

- GIVEN 用户清空了全部偏好,即使此前配置行不存在或已为空
- WHEN 清空之前准备的问答任务尝试写入
- THEN 不产生旧偏好;清空之后开始的新问答仍可补齐新的缺失槽位

#### Scenario: 并发自动补丁合并缺失槽位

- GIVEN 没有人工修改,两个自动任务补齐不同缺失槽位
- WHEN 两个补丁并发落库
- THEN 两项补齐均保留,不因整份覆盖丢失另一项

### Requirement: 显式记忆分流

用户显式「记住…」时,系统 SHALL 分流:内容命中槽位语义(默认数据源/
时间范围/展示风格等)则写入偏好槽位;否则写入用户自述笔记(仅显式,
confidence 最高,类别为笔记),SHALL NOT 进入槽位。笔记可用于召回,
注入时 SHALL 标注为「用户备注」且不得作为业务事实或数据范围约束。

#### Scenario: 显式写槽位

- GIVEN 「请记住:我看报表默认用 default 数据源,时间范围近30天」
- WHEN 处理
- THEN 写入 preferred_datasource 与 default_time_range
- AND 答复确认已记住

#### Scenario: 显式笔记不入槽位

- GIVEN 「请记住:我们组周五出周报」
- WHEN 处理
- THEN 落一条显式笔记,不写入偏好槽位
- AND 注入时标注为用户备注

### Requirement: 偏好注入语义

问答链路 SHALL 以独立语义段注入已填偏好槽位,固定前缀声明「默认值,
可忽略,不得作为 WHERE/数据范围约束」。用户未指定时间时,default_time_range
可作理解层默认但 SHALL 在回答中可见所采用的时间范围;preferred_datasource
仅作数据源选择默认。零槽位且零笔记时 SHALL 不注入(与引入前逐字一致)。
系统 SHALL NOT 因偏好静默改写业务过滤条件。

#### Scenario: 默认值可忽略

- GIVEN 槽位 display_style=结论先行
- WHEN 生成回答
- THEN 表述风格可遵循该偏好
- AND 不因该偏好改变 SQL 结果范围

#### Scenario: 时间默认可见

- GIVEN default_time_range=近30天,用户问「销售额多少」未指时间
- WHEN 回答
- THEN 采用近30天并在回答中说明该范围

### Requirement: 存量与自管理

系统 SHALL 停止新增自由文本隐式记忆;存量隐式记忆默认不注入,自管理页可见并可删除,标注 LEGACY。用户 SHALL 能在既有记忆页查看、编辑、逐项清除与清空本人本租户偏好。操作对象 SHALL 限本人本租户,资源越权保持 404 同形;既有认证失败与租户前置校验语义不变。偏好与笔记管理操作 SHALL 留操作日志。清空 SHALL 仅影响偏好,不删除笔记或团队记忆。控制面保留表清单 SHALL 含 `user_prefs`,数据面执行路由 SHALL 拒绝引用。

#### Scenario: 存量隐式不注入

- GIVEN 存在历史 IMPLICIT 自由文本记忆
- WHEN 问答
- THEN 该记忆不进入提示
- AND 自管理页可见 LEGACY 标记并可删除

#### Scenario: 越权同形拒绝

- GIVEN 用户 A 持有用户 B 的偏好资源地址
- WHEN A 尝试读写
- THEN 404 同形,无副作用

#### Scenario: 数据面拒读 user_prefs

- GIVEN 语句 `SELECT * FROM user_prefs`
- WHEN 到达执行路由
- THEN verdict=BLOCKED(reason 含 RESERVED_TABLE),落审计

#### Scenario: 本人偏好管理入口

- GIVEN 用户已登录并进入记忆页
- WHEN 读取、编辑或逐项清除偏好
- THEN 页面显示服务端确认值,错误可见且可重试
- AND 切租户后旧响应不能覆盖新语境,操作留下审计

#### Scenario: 清空全部偏好不影响笔记

- GIVEN 用户同时拥有偏好和个人笔记
- WHEN 确认清空全部偏好
- THEN 返回空 prefs,下一问不注入旧偏好
- AND 个人笔记、团队记忆保持不变
