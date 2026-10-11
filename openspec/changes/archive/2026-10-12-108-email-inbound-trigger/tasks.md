# Tasks

## 1. Schema 与配置(api)

- [x] 1.1 新迁移:触发器邮件配置列(加密凭证 + host/port/tls/folder/
  interval)+ 邮件游标表(触发器 id+文件夹唯一,cursor/uidvalidity),
  验证:升级+降级往返、存量触发器零影响
- [x] 1.2 邮件触发器 CRUD 扩展(凭证加密落库复用 webhook secret 单点、
  掩码回显、空框保持现值、TARGET_NOT_FOUND 同口径),测试先行,验证:
  红→绿;全接口无凭证明文 grep 断言;`make contracts` 重生成

## 2. 轮询与适配(api)

- [x] 2.1 IMAP 轮询器(标准库 imaplib;UID SEARCH 游标续传;UIDVALIDITY
  变更游标重置告警;连接/认证失败分类落 FAILED run),以内存 IMAP 桩
  测试先行,验证:红→绿;新邮件触发/续传不重复/失败落历史三场景
- [x] 2.2 摘要适配器(from/subject/date/text_preview 白名单+截断,进
  目标前过既有输入护栏),验证:恶意主题/超长正文净化断言、原文不落库
  断言
- [x] 2.3 调度接线(轮询间隔到期检查、前次未完成 SKIPPED、停用停轮询/
  启用续传、单触发器失败不扩散),验证:调度集成测试(桩邮箱)四场景;
  既有 cron/webhook 触发器测试零回归

## 3. 表单(web)

- [x] 3.1 触发器表单邮件形态分支(配置字段+凭证空框语义+TLS 开关),
  验证:创建/编辑闭环冒烟,typecheck 绿;375 亮暗走查

## 4. 收尾

- [x] 4.1 `make check` 全量绿(api 除已知代理环境项);HANDOFF 补演示
  ——2026-10-11 收口:四段门禁按环境口径过(同上收口记录);演示配方已入
  HANDOFF,真实 IMAP 联调待条件(无真实邮箱凭证)
  配方(真实 IMAP 邮箱参数/测试邮件发送/触发核验),验证:配方自查可复现
