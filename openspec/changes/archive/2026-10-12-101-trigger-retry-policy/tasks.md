# Tasks

## 1. Schema 迁移

- [x] 1.1 新迁移:`sys_trigger` 加 `max_retries INTEGER NOT NULL DEFAULT 0`
  (CHECK 0–5);`sys_trigger_run` 加 `retry_of_run_id UUID NULL`、
  `attempt INTEGER NOT NULL DEFAULT 1`、`next_retry_at TIMESTAMPTZ NULL`
  + 挂起重试部分索引,验证:升级+降级往返、存量行缺省零回归断言

## 2. 调度重排与执行链(api)

- [x] 2.1 重试判定纯函数(FAILED+剩余次数>0 → 安排;SKIPPED/成功/耗尽 →
  终态;退避计算),测试先行,验证:红→绿,边界(0 次/耗尽/SKIPPED)逐类断言
- [x] 2.2 调度器扫描挂起重试到期派发(同身份同防御层,SKIPPED 保护对
  重试执行生效),验证:重试执行集成测试(mock 目标先败后成);
  重试执行中 cron 到期记 SKIPPED 的交织测试
- [x] 2.3 停用/删除取消挂起重试,验证:停用后到期不派发测试
- [x] 2.4 notify 终态化(链终态单次发送,中间失败不发),验证:链中/链终
  通知次数断言(mock notify 端点)
- [x] 2.5 触发器 CRUD/历史端点扩展(max_retries 读写、历史含 attempt 与
  首跑关联),验证:`make api-check` triggers 子集绿;`make contracts` 重生成

## 3. 表单字段(web)

- [x] 3.1 触发器表单加重试上限数字输入(0–5,缺省 0,校验提示),历史
  抽屉/表单呈现尝试序号,验证:创建/编辑闭环冒烟,typecheck 绿

## 4. 收尾

- [x] 4.1 `make check` 全量绿;冒烟以集成测试等价(本机无运行栈):
  test_retry_chain_fail_then_success 精确断言「首跑 FAILED→挂起重试行→
  到期派发 SUCCESS→历史三行、中间失败不通知、终态通知恰一次」,
  验证:冒烟步骤可复现(配方=同测试脚本形态)
