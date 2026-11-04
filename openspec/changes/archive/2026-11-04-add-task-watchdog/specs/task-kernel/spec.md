# Spec Delta: task-kernel

## ADDED Requirements

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
