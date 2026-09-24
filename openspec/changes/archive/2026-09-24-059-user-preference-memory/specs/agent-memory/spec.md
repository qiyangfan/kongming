## ADDED Requirements

### Requirement: 闭合偏好槽位

系统 SHALL 以 (user, tenant) 单行配置存储用户偏好槽位,槽位集合封闭:
preferred_datasource、default_time_range、metric_aliases、chart_pref、
language、timezone、display_style。写入时未知键 SHALL 拒绝;更新为
覆盖式(以槽位为单位),不追加历史版本(v1)。槽位 SHALL 仅表达
**默认值与表达习惯**,SHALL NOT 作为业务数据范围或 WHERE 约束源。

#### Scenario: 槽位读写

- GIVEN 用户设置 preferred_datasource=default、default_time_range=近30天
- WHEN 保存并再次问答
- THEN 槽位持久化且下次可读回
- AND 未知键字段被拒绝

### Requirement: 隐式提取收窄

会话后隐式记忆提取 SHALL 只允许产出偏好槽位补丁:输出中的未知字段 SHALL
丢弃,空补丁 SHALL NOT 落库。系统 SHALL NOT 将对话中的业务事实、事件或
结论写入长期记忆;SHALL NOT 再产生自由文本 IMPLICIT 记忆。提取失败 SHALL
静默放弃且不影响问答。

#### Scenario: 只补槽位

- GIVEN 一轮对话含「上月销售额是 100 万」与「回答尽量结论先行」
- WHEN 隐式提取
- THEN 不落业务事实「上月销售额 100 万」
- AND 仅 display_style 槽位可被补全
- AND v1 槽位集之外的偏好(如区域习惯)一律丢弃,不落库

#### Scenario: 空补丁不落库

- GIVEN 对话无可提取偏好
- WHEN 隐式提取
- THEN 无任何写入

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

系统 SHALL 停止新增自由文本隐式记忆;存量隐式记忆默认不注入,自管理页
可见并可删除(标注 LEGACY)。用户 SHALL 可查看/编辑/清空本人偏好槽位;
操作对象限本人本租户,越权 404 同形。偏好与笔记的管理操作 SHALL 落
操作日志。控制面保留表清单 SHALL 含 `user_prefs`,数据面执行路由 SHALL
拒绝引用。

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
