## MODIFIED Requirements

### Requirement: 闭合偏好槽位

系统 SHALL 以 (user, tenant) 单行配置存储用户偏好槽位，槽位集合封闭：preferred_datasource、default_time_range、metric_aliases、chart_pref、language、timezone、display_style。写入时未知键 SHALL 拒绝；更新 SHALL 以槽位为单位覆盖，不追加历史版本。槽位 SHALL 仅表达默认值与表达习惯，SHALL NOT 作为业务数据范围或 WHERE 约束源。显式更新省略的字段 SHALL 保留；显式 null SHALL 删除对应槽位；空对象、空字符串和空列表 SHALL 保持既有空补丁语义，不表示清空。归一化后没有非空设置或显式 null 删除的 PUT SHALL 返回当前值，SHALL NOT 创建或修改配置行、推进人工版本或使在途自动提取失效。显式 null 即使目标槽位已不存在也 SHALL 作为有效人工指令推进版本。并发更新 SHALL 基于最新配置合并。

#### Scenario: 槽位读写

- **GIVEN** 用户设置 preferred_datasource=default、default_time_range=近30天
- **WHEN** 保存并再次问答
- **THEN** 槽位持久化且下次可读回
- **AND** 未知键字段被拒绝

#### Scenario: 未提及槽位保持不变

- **GIVEN** 用户已有语言与图表偏好
- **WHEN** 仅更新语言或提交空对象
- **THEN** 未提及的槽位保持不变，空对象不删除任何值

#### Scenario: 显式空值删除单个槽位

- **GIVEN** 用户已有 language 与 timezone
- **WHEN** 显式提交 language=null
- **THEN** language 被删除，timezone 保留
- **AND** 空字符串和空列表不被解释为删除

#### Scenario: 空补丁没有写入副作用

- **GIVEN** 一轮自动提取仍在执行，用户提交的 PUT 归一化后既无非空设置也无显式 null
- **WHEN** 服务处理该请求
- **THEN** 返回当前配置，不创建或修改配置行，不推进人工版本，不使该自动补丁失效
- **AND** 不记录实际修改审计

#### Scenario: 并发更新不同槽位均保留

- **GIVEN** 两个人工请求更新本人本租户的不同槽位
- **WHEN** 请求并发到达
- **THEN** 两项修改都被保存，同槽位按实际提交顺序生效

### Requirement: 隐式提取收窄

会话后隐式记忆提取 SHALL 只允许产出偏好槽位补丁，未知字段 SHALL 丢弃，空补丁 SHALL NOT 落库。系统 SHALL NOT 将对话中的业务事实、事件或结论写入长期记忆，SHALL NOT 再产生自由文本 IMPLICIT 记忆。自动提取 SHALL 只补当前缺失槽位，不覆盖已有值或执行删除；源问答开始准备后若发生人工修改、删除或清空，旧问答的自动补丁 SHALL 放弃。提取或版本读取失败 SHALL 不影响问答，不将未知版本当成初始版本。人工显式写槽位 SHALL 保持覆盖语义。

#### Scenario: 只补槽位

- **GIVEN** 一轮对话含「上月销售额是 100 万」与「回答尽量结论先行」
- **WHEN** 隐式提取
- **THEN** 不落业务事实「上月销售额 100 万」
- **AND** 仅 display_style 槽位可被补全
- **AND** 槽位集之外的偏好一律丢弃

#### Scenario: 空补丁不落库

- **GIVEN** 对话无可提取偏好
- **WHEN** 隐式提取
- **THEN** 无任何写入

#### Scenario: 隐式提取不覆盖已填槽位

- **GIVEN** 用户已设置 display_style
- **WHEN** 自动提取包含另一个 display_style
- **THEN** 已有值保持不变，用户显式修改仍可覆盖

#### Scenario: 人工修改后旧提取不得覆盖

- **GIVEN** 一轮问答准备完成后，用户修改了偏好
- **WHEN** 该旧问答的自动提取任务稍后开始或完成
- **THEN** 其补丁被放弃，不覆盖人工修改

#### Scenario: 清空后旧问答不得恢复偏好

- **GIVEN** 用户清空了全部偏好，即使此前配置行不存在或已为空
- **WHEN** 清空之前准备的问答任务尝试写入
- **THEN** 不产生旧偏好；清空之后开始的新问答仍可补齐新的缺失槽位

#### Scenario: 并发自动补丁合并缺失槽位

- **GIVEN** 没有人工修改，两个自动任务补齐不同缺失槽位
- **WHEN** 两个补丁并发落库
- **THEN** 两项补齐均保留，不因整份覆盖丢失另一项

### Requirement: 存量与自管理

系统 SHALL 停止新增自由文本隐式记忆；存量隐式记忆默认不注入，自管理页可见并可删除，标注 LEGACY。用户 SHALL 能在既有记忆页查看、编辑、逐项清除与清空本人本租户偏好。操作对象 SHALL 限本人本租户，资源越权保持 404 同形；既有认证失败与租户前置校验语义不变。偏好与笔记管理操作 SHALL 留操作日志。清空 SHALL 仅影响偏好，不删除笔记或团队记忆。控制面保留表清单 SHALL 含 user_prefs，数据面执行路由 SHALL 拒绝引用。

#### Scenario: 存量隐式不注入

- **GIVEN** 存在历史 IMPLICIT 自由文本记忆
- **WHEN** 问答
- **THEN** 该记忆不进入提示
- **AND** 自管理页可见 LEGACY 标记并可删除

#### Scenario: 越权同形拒绝

- **GIVEN** 用户 A 持有用户 B 的偏好资源地址
- **WHEN** A 尝试读写
- **THEN** 404 同形，无副作用

#### Scenario: 数据面拒读 user_prefs

- **GIVEN** 语句引用 user_prefs
- **WHEN** 到达执行路由
- **THEN** verdict=BLOCKED，reason 含 RESERVED_TABLE，落审计

#### Scenario: 本人偏好管理入口

- **GIVEN** 用户已登录并进入记忆页
- **WHEN** 读取、编辑或逐项清除偏好
- **THEN** 页面显示服务端确认值，错误可见且可重试
- **AND** 切租户后旧响应不能覆盖新语境，操作留下审计

#### Scenario: 清空全部偏好不影响笔记

- **GIVEN** 用户同时拥有偏好和个人笔记
- **WHEN** 确认清空全部偏好
- **THEN** 返回空 prefs，下一问不注入旧偏好
- **AND** 个人笔记、团队记忆保持不变
