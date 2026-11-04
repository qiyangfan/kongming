## Purpose

把自主性从「一轮 ReAct + 会话内 Todo 账本」升级为**跨请求的持久任务**:目标分解为步骤清单,步骤由工具执行证据驱动状态,检查点支撑暂停/恢复/重启续跑,高危步骤挂起等人工审批,预算耗尽诚实披露部分完成。任务执行的一切工具调用走既有回调与六层防御,零凭证纪律(ADR-002)零改动;本能力是多智能体协作(后续变更)的宿主形态。

## Requirements

### Requirement: 任务实体与状态机

系统 SHALL 提供持久任务实体:字段 MUST 含目标描述(自然语言)、状态、归属(user/tenant)、可选的发起会话关联、预算配置与消耗、创建/更新时间。状态集合 ∈ {PLANNING, RUNNING, AWAITING_APPROVAL, PAUSED, INTERRUPTED, DONE, FAILED, CANCELLED}(VARCHAR + CHECK,ADR-004/VI)。状态迁移 SHALL 由白名单约束,非法迁移(如 CANCELLED → RUNNING)MUST 拒绝并留审计;终态(DONE/FAILED/CANCELLED)SHALL NOT 自动复活,复活语义只存在于「从终态副本新建任务」的显式操作。任务创建权限点 `task:create`,读取 `task:read`,管理操作(暂停/恢复/取消/审批)`task:manage`;列表与详情仅返回本人本租户任务,他人任务 404 同形。

#### Scenario: 目标建任务

- **GIVEN** 授权用户(task:create)提交目标「梳理华东区近 30 天销售异常并给出归因」
- **WHEN** 任务创建
- **THEN** 落库为 PLANNING 并进入计划生成,进度可查

#### Scenario: 非法迁移拒绝

- **GIVEN** 任务已 CANCELLED
- **WHEN** 请求恢复该任务
- **THEN** 状态机拒绝迁移并落审计,任务保持 CANCELLED

#### Scenario: 他人任务不可见

- **GIVEN** 用户 B 持有用户 A 的任务标识
- **WHEN** B 请求详情/暂停/取消
- **THEN** 一律 404 同形

### Requirement: 计划与步骤账本

任务 SHALL 持久化计划(步骤清单),每步 MUST 含:目标、状态 ∈ {PENDING, RUNNING, AWAITING_APPROVAL, DONE, FAILED, SKIPPED, CANCELLED}、证据引用、失败/跳过原因、序号。步骤状态迁移 SHALL 由工具执行结果驱动:工具成功且产出结果方可置 DONE 并写证据引用;工具失败、防御拒绝、空结果或能力不适用 SHALL 置 FAILED/SKIPPED 并填原因,SHALL NOT 置 DONE——语言模型自述不构成完成证据(与会话内 Todo 账本同源原则的跨请求形态)。计划 SHALL 可在执行中追加步骤(后发现子目标),追加 SHALL 落任务事件;已终态步骤 SHALL NOT 被改写。

#### Scenario: 有证据才完成

- **GIVEN** 步骤「查询华东日销量」对应查询工具成功返回
- **WHEN** 结果回写
- **THEN** 该步置 DONE 且证据指向该次工具调用(trace/审计引用)

#### Scenario: 执行中追加步骤

- **GIVEN** 任务执行至第 3 步发现需先补「核对数据口径」
- **WHEN** 计划追加该步
- **THEN** 步骤清单落库且任务事件记录追加,既有步骤状态不变

### Requirement: 检查点与恢复

每步到达终态后系统 SHALL 落检查点(已完成步骤集 + 执行上下文摘要)。任务 SHALL 可从最后检查点恢复(PAUSED/INTERRUPTED → RUNNING):未完成步骤继续,已完成步骤 SHALL NOT 重复执行产生重复副作用。进程重启后 RUNNING/PLANNING 任务 SHALL 被标记 INTERRUPTED,SHALL NOT 静默继续也 SHALL NOT 静默标记完成;恢复为显式操作(任务归属人)。

#### Scenario: 暂停后恢复续跑

- **GIVEN** 五步任务完成两步后用户暂停
- **WHEN** 用户恢复任务
- **THEN** 从第 3 步继续,前两步不重执行,证据保留

#### Scenario: 重启不谎称完成

- **GIVEN** 进程重启时有任务处于 RUNNING(3/7 步)
- **WHEN** 系统重启完成
- **THEN** 该任务标记 INTERRUPTED,状态与进度如实,不出现 DONE

### Requirement: 审批门

步骤可声明需人工确认(require_confirm,通常为携带写操作或高影响动作的步骤)。执行到该步时任务 SHALL 进入 AWAITING_APPROVAL 并挂起(不执行该步),通知面沿用既有会话内确认卡形态;审批 SHALL 仅任务归属人可操作(归属校验,他人操作拒绝并落审计);批准后该步经全量防御执行并继续,拒绝后该步置 SKIPPED(带原因)或按步骤声明终止任务;审批 SHALL 有过期时间,过期未决该步置 SKIPPED。同一审批标识的重复提交 SHALL NOT 产生重复执行(幂等)。

#### Scenario: 高危步骤挂起

- **GIVEN** 步骤 4 为「发布数据修正公告」(require_confirm)
- **WHEN** 执行到步骤 4
- **THEN** 任务进入 AWAITING_APPROVAL,该步不执行,用户收到确认卡(步骤目标/预期副作用)

#### Scenario: 他人审批被拒

- **GIVEN** 任务属于用户 A
- **WHEN** 用户 B 提交审批
- **THEN** 拒绝并落审计,任务保持挂起

#### Scenario: 审批过期

- **GIVEN** 审批挂起超过过期时间
- **WHEN** 过期触发
- **THEN** 该步置 SKIPPED(原因=审批过期),任务按步骤声明继续或终止

### Requirement: 任务预算与诚实收口

任务 SHALL 受预算约束(步数/LLM token/时长,任务级配置 + 部署级上限),耗尽时任务进入 PAUSED 并附预算消耗摘要;终态收口 SHALL 与步骤账本一致:存在非 DONE 步骤时,收口表述 SHALL 逐项如实说明未完成/失败原因,SHALL NOT 使用全称完成措辞;全部 DONE 方可 DONE。任务执行的一切 LLM 调用落既有用量采集并可按任务维度归集,过程事件挂同一任务 trace 语义进既有时间线。

#### Scenario: 预算耗尽暂停披露

- **GIVEN** 七步任务完成四步后 token 预算耗尽
- **WHEN** 任务暂停
- **THEN** 状态 PAUSED,摘要披露 4/7 与剩余步骤,不声称完成

#### Scenario: 全部完成才 DONE

- **GIVEN** 七步中六步 DONE、一步 SKIPPED(带原因)
- **WHEN** 任务收口
- **THEN** 任务终态如实反映(非全 DONE 的完成语义按实现定义披露跳过项),收口文本不含「已全部完成」措辞

### Requirement: 任务列表与观测

系统 SHALL 提供任务列表(按状态/时间筛选,分页)与详情(目标/计划/步骤进度/预算消耗/事件时间线);暂停/恢复/取消为详情内操作(权限 task:manage)。任务事件(创建/计划变更/步骤迁移/审批/暂停恢复/终态)SHALL 持久化并可按任务查询,事件流与既有 trace 时间线可对齐查看。

#### Scenario: 列表筛选与详情

- **GIVEN** 用户有 3 个 RUNNING、1 个 DONE 任务
- **WHEN** 按状态 RUNNING 筛选列表
- **THEN** 返回 3 条,详情含步骤进度与预算消耗

#### Scenario: 事件时间线可查

- **GIVEN** 任务经历 挂起→批准→恢复→DONE
- **WHEN** 查看详情事件流
- **THEN** 各事件按时间可查,与 trace 时间线对齐

### Requirement: 运行中停滞检测与判读

系统 SHALL 对 RUNNING/PLANNING 状态的在途任务做周期性停滞检测:以任务事件账本的最后写入时间为心跳,滞留超过配置阈值的任务视为候选停滞。候选停滞任务 SHALL 在同一滞留段内获得且仅获得一次停滞判读,判读结论(停滞/仍进展)与理由 SHALL 落任务事件账本;判读为「仍进展」时 SHALL 记录观察事件并重置心跳观察窗。判读不可用(端点不可达/超时/报文畸形)SHALL 降级为机械判定,SHALL NOT 阻塞检测循环。AWAITING_APPROVAL/PAUSED 状态 SHALL NOT 被检测、判读或收口。

#### Scenario: 滞留任务触发判读

- **GIVEN** 任务处于 RUNNING 且事件账本最后写入已超过停滞阈值
- **WHEN** 巡检扫描到该任务
- **THEN** 触发一次停滞判读,判读结论与理由落任务事件账本

#### Scenario: 判读仍进展则重置观察窗

- **GIVEN** 候选停滞任务被判读为「仍进展」
- **WHEN** 判读结论落账本
- **THEN** 心跳观察窗自该事件重起,任务保持 RUNNING,后续同一滞留段不再重复判读

#### Scenario: 设计等待态不受监视

- **GIVEN** 任务处于 AWAITING_APPROVAL 已超过停滞阈值的三倍时长
- **WHEN** 巡检扫描
- **THEN** 该任务不被检测、不判读、不收口,状态保持不变

#### Scenario: 判读不可用降级机械判定

- **GIVEN** 候选停滞任务的判读端点不可达
- **WHEN** 巡检到达该任务
- **THEN** 按机械判定路径处理并留痕,巡检循环对其余任务继续有效

### Requirement: 停滞任务夺权收口

确认停滞的任务(判读为停滞,或判读不可用后二次超窗)SHALL 被夺权收口:派发代际原子自增使旧编排循环的后续回调失配退出,任务状态经条件迁移置 INTERRUPTED 并附原因,迁移与夺权 SHALL 各落任务事件账本。收口 SHALL NOT 自动重启编排,恢复保持归属人显式操作;被收口任务的旧循环延迟返回 SHALL 被代际失配拒绝,SHALL NOT 覆写收口结果。

#### Scenario: 确认停滞夺权收口

- **GIVEN** 候选停滞任务被判读为「停滞」
- **WHEN** 收口执行
- **THEN** 派发代际自增、任务置 INTERRUPTED(附原因),夺权与迁移事件均落账本

#### Scenario: 旧循环延迟返回被拒绝

- **GIVEN** 任务已被看门狗收口为 INTERRUPTED
- **WHEN** 原编排循环的滞留调用最终返回并尝试回写终态
- **THEN** 回写因代际失配被拒绝,任务保持 INTERRUPTED 与看门狗原因

#### Scenario: 二次超窗机械收口

- **GIVEN** 候选停滞任务判读不可用且滞留达到第二观察窗上限
- **WHEN** 巡检再次扫描到该任务
- **THEN** 不再判读,直接夺权收口为 INTERRUPTED,原因注明判读不可用

#### Scenario: 收口后可显式恢复

- **GIVEN** 任务被看门狗收口为 INTERRUPTED
- **WHEN** 归属人请求恢复
- **THEN** 按既有检查点恢复语义从最后检查点续跑,已完成步骤不重复执行
