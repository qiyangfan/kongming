# Tasks: add-task-watchdog

## 1. 配置与心跳查询(api 侧基座)

- [x] 1.1 `config.py` 新增看门狗配置:`watchdog_enabled`(默认 true)、`watchdog_interval_sec`(60, ge=10)、`watchdog_stale_sec`(300, ge=60)、`watchdog_judge_timeout_sec`(20, gt=0),注释说明阈值须显著小于任务墙钟预算
- [x] 1.2 `task/repository.py` 新增在途任务心跳查询:RUNNING/PLANNING 且 `updated_at` 早于 now() − stale_sec,返回任务 id/目标/代际/事件尾部;附查询单测

## 2. 停滞判读链路(agent ↔ api)

- [x] 2.1 agent 新增 `POST /internal/judge-stagnation`(X-Internal-Token 守门):输入 goal/事件尾部/滞留秒数,输出 `{verdict, reason}`;prompt 落 agent prompts 侧并遵循其 docstring 登记约定;端点单测(合法报文/畸形拒绝)
- [x] 2.2 api `AgentClient.judge_stagnation`:独立短超时 + 既有降级契约(不可达/非 200/报文畸形 → None);单测覆盖三条降级路径

## 3. 监督用例(测试先行)

- [x] 3.1 先写失败测试(按 spec 场景):候选检测、设计等待态(AWAITING_APPROVAL/PAUSED)不被触碰、同一滞留段判读去重、「仍进展」重置观察窗、判读停滞夺权、判读不可用二次超窗机械收口、旧循环终态回写被代际失配拒绝、收口后归属人可显式恢复
- [x] 3.2 `task/service.py` 实现巡检用例:逐候选走两段式决策(判读一次 → 收口/重置/兜底),事件 kinds `watchdog_judged`/`watchdog_taken_over`;谓词未命中(CAS 失败)即放弃
- [x] 3.3 实现夺权收口:`bump_dispatch_epoch` 原子自增 + `transition_task_system(from RUNNING → INTERRUPTED, expect_epoch=新代数)`;3.1 失败测试全部转绿

## 4. 常驻巡检循环

- [x] 4.1 `main.py` lifespan 增设第五常驻循环(开关关闭时不启动),形态对齐既有四循环,收尾 cancel 收割;循环内异常不中断整体巡检(单任务失败记日志继续)

## 5. 门禁与外围核验

- [x] 5.1 核验 web 任务详情事件流对新 kind 的展示:存在 kind 白名单映射则补 `watchdog_judged`/`watchdog_taken_over` 展示(兜底显示原始 kind),无则记录零改动结论
- [x] 5.2 全套门禁:`make check`(api/agent ruff+mypy+pyright+pytest、web tsc+test+eslint+format)+ `make contracts-check` 确认契约零漂移;测试失败修复后重跑全套
