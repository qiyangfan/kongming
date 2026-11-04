# Proposal: add-task-watchdog

## Why

任务域的在途任务(最长 960s 墙钟)目前只有**拉取式闸门**(`begin_step` 逐步复核状态/代际/预算)与**启动清扫**(重启时滞留任务标 INTERRUPTED),二者都拦不住「进程存活但任务实际停滞」的中间态:卡死的任务要白白等满 960s 传输层超时才被收口,期间没有任何组件在观察它。同时,后续规划的事件驱动立项(外部事件自动创建任务)以「自主发起」为前提——审计优先的定位要求自主发起必须有运行中监督作对价,本变更是该方向的前置监督层,也是外部控制回路(Control Loop)+ LLM-as-judge 这套模式的落地载体。

## What Changes

- **api 侧新增在途任务巡检循环**:并入 lifespan 既有常驻循环形态(归档/账单/支付过期/附件清理之后第五个),周期扫描 RUNNING/PLANNING 任务;AWAITING_APPROVAL/PAUSED 等设计等待态绝不触碰。
- **心跳停滞检测**:以任务事件账本最后写入时间为心跳,滞留超过阈值(且未到 960s 传输层超时)视为候选停滞。
- **LLM 停滞判读(单次)**:候选停滞任务交 agent 新内部端点 `/internal/judge-stagnation` 判读一次——输入目标/事件轨迹/滞留时长,输出「停滞/仍进展 + 一行理由」;判读结论落任务事件账本。「仍进展」则写事件重置心跳窗继续观察,同一滞留段只判读一次(事件标记去重)。
- **确认停滞即夺权收口**:复用既有 epoch 夺权原语——`bump_dispatch_epoch` 原子自增使旧编排循环后续回调全部失配退出,任务状态迁移 INTERRUPTED(CAS + 原因 + 账本事件),恢复语义不变(归属人显式恢复)。
- **判读不可用的机械兜底**:判读端点不可达/超时/报文畸形时,沿用 AgentClient 既有降级契约,二次超窗直接机械收口 INTERRUPTED(原因注明判读不可用),不无限等待。
- **配置项**:看门狗开关、巡检间隔、停滞阈值、判读超时,沿用 `config.py` Field 钳制惯例。
- **无 schema 变更**:检测依据(任务事件账本)、夺权原语(dispatch_epoch)、状态机迁移均为既有机制复用,零新表零新列。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `task-kernel`: 新增「运行中看门狗监督」requirement——系统 SHALL 对 RUNNING/PLANNING 在途任务做周期停滞检测,确认停滞的任务 SHALL 被夺权收口为 INTERRUPTED 并全程留痕;设计等待态 SHALL NOT 被看门狗触碰;判读与收口行为 SHALL 全部落任务事件账本。

## Impact

- **apps/api/kongming/task/**: `service.py` 新增巡检与夺权用例(复用 `append_task_event`/`transition_task_system` CAS/`bump_dispatch_epoch`);`repository.py` 新增在途任务心跳查询。
- **apps/api/kongming/main.py**: lifespan 增设第五个常驻巡检循环(形态对齐既有四循环 + 收尾 cancel)。
- **apps/api/kongming/chat/agent_client.py**: 新增 `judge_stagnation` 方法(降级契约对齐 `score_answer`/`rewrite_title` 范式)。
- **apps/agent/kongming_agent/**: 新增 `/internal/judge-stagnation` 端点与停滞判读 prompt(X-Internal-Token 守门,与既有 `/internal/*` 端点族同构)。
- **apps/api/kongming/config.py**: 看门狗配置项(开关/间隔/阈值/判读超时)。
- **契约管线(ADR-005)**: 无新外部路由、无模型字段变更,`contracts/openapi.json` 不受影响;watchdog 判读结论经既有任务事件账本对外可见,详情接口形态不变。
- **信任边界(ADR-002)**: LLM 判读走 agent 内部端点,api 不直连 LLM,零凭证纪律零改动。
- **schema(ADR-004)**: 零迁移。

## Non-goals

- **不做持续运行的 Watchdog LLM Session**:不建 DeepSeek 文章中那种可自然语言询问、可在线改提示词的独立看门狗会话;本变更只做单次判读的确定性巡检循环。
- **不做自动重试/自动恢复**:确认停滞只收口 INTERRUPTED,不自动重启编排;恢复保持归属人显式操作(与「检查点与恢复」requirement 一致)。
- **不触碰设计等待态**:AWAITING_APPROVAL/PAUSED 是设计上允许无限等待的状态,看门狗 SHALL NOT 检测、判读或收口它们;审批超 TTL 清理仍是启动扫描既有职责。
- **不含事件驱动立项**:外部事件自动创建任务(trigger 接通 task/collab + 两级事件过滤)是后续独立变更,本变更只交付其前置监督层。
- **不含 worker 上下文续读入口与 chat 异步工具调用**:各自独立立项,不搭车。
- **不做多副本选主**:巡检为单进程形态,与 trigger 调度器同口径;多副本部署形态留待部署形态演进时另议。
- **不引入消息队列/外部调度器**:进程内 asyncio 循环,符合技术栈锁定(docs/tech-stack.md)。
