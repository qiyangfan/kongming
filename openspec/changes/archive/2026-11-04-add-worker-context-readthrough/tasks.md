# Tasks: add-worker-context-readthrough

## 1. 取数端点(api)

- [x] 1.1 task router 增 `POST /internal/task-context`(X-Internal-Token):入参 task_id,返回 goal 全文 + 已完成步骤结论与证据引用,payload 上限钳制(8KB);跨任务/非归属 404 同形;端点单测(含归属拒绝路径)
- [x] 1.2 `make contracts-check` 核验:openapi 含新端点则重生成产物并提交

## 2. 续读工具与截断入口(agent,测试先行)

- [x] 2.1 先写失败测试:截断发生→提示文本存在;未截断→无提示(行为不变);task_context 工具调用→回调端点取全文;非自身任务→拒绝
- [x] 2.2 实现工具注册:`task_context` 只读工具进 worker 缺省内置工具集(授权交集外加),内部回调 + 工具失败语义(可重试/放弃)
- [x] 2.3 实现两处截断尾部提示(`collab_gen.py` 摘要与 `roundtable_gen.py` 黑板,同一提示构造函数单源);2.1 全部转绿

## 3. 对照实验与门禁

- [x] 3.1 eval 对照收口(独立评估栈 18000/18001 载入工作区现态,不经 owner 常驻栈):长/短目标两臂各一轮 webhook 触发,终态/步骤/预算/协作账本数据记入 `apps/api/evals/README` 专项对照记录(不设阈值);续读调用数无审计痕迹记为观测缺口
- [x] 3.2 全套门禁:`make check`(api/agent ruff+mypy+pyright+pytest、web tsc+test+eslint+format);测试失败修复后重跑全套
