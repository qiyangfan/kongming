# 031 triggers-webhooks 实施任务

## 1. 迁移与域包骨架

- [x] 1.1 0019 迁移(sys_trigger_definition 双 CHECK+webhook_path
  全局唯一/sys_trigger_run 三态)+conftest 清单
- [x] 1.2 models:cron 解析纯函数(五字段全形态/非法拒/双非*拒/
  next_fire)+ports+import-linter 契约
- [x] 1.3 权限点种子(system:trigger:manage 24)+种子测试计数

## 2. cron 与调度(测试先行)

- [x] 2.1 失败测试:cron 解析全形态/next_fire 边界(月末/周日 0/7)/
  非法拒
- [x] 2.2 scheduler.py(lifespan 薄壳 30s tick;到期判定;min_interval
  跳过;进行中 SKIPPED;独立会话执行)

## 3. webhook 入站与适配器(测试先行:安全类)

- [x] 3.1 失败测试:签名正确 202+异步触发/错误缺失禁用不存在同形
  401/404/body>1MB 413/custom 透传/github 标准化
- [x] 3.2 POST /webhooks/{uuid} 端点+adapters.py+HMAC 校验
  (compare_digest)

## 4. 管理面与目标执行(测试先行:安全类)

- [x] 4.1 失败测试:CRUD/启停/404 同形/密钥不回显(创建一次性
  展示)/历史分页/目标执行身份=创建者(行级复校)
- [x] 4.2 /system/triggers/* 端点族+trigger service(调用 029/025
  组合根装配,channel="cron"/"webhook")
- [x] 4.3 出站通知(notify_url:SSRF 判定复用 029+X-Kongming-Signature
  +5s 超时)

## 5. web

- [x] 5.1 /admin/triggers 子页(列表/新建编辑[cron 反馈/webhook
  URL+密钥一次性展示]/启停/删除受控/历史分页)

## 6. 验证与收尾

- [x] 6.1 make check 全绿;make contracts;016 评估 smoke 复验;
  冒烟(cron 到期真触发工作流;webhook 签名全链 curl;GitHub
  形态 payload;SSRF 通知拒)
- [x] 6.2 R1 独立评审+修复清零
- [x] 6.3 HANDOFF+挂账(重试/多实例互斥/更多事件源/双表关联
  展示/时区)+归档
