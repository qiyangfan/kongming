# 040 · 任务

## 1. RAG schema 封闭集合(后端)

- [x] 1.1 测试先行:schemas 枚举端点与上传越界校验的失败测试——default 源返回 `[tenant_<key>, public]`、动态源返回 `[default_schema]`、不可用数据源 fail-closed 同形、上传越界 schema 400 `SCHEMA_NOT_ALLOWED`、合法 schema 上传不受影响
- [x] 1.2 repository 抽取「按 (tenant_key, datasource 行) 计算可扫描 schema 列表」为独立方法,`introspect_all` 改为复用(消灭第二处内联)
- [x] 1.3 新增 `GET /system/rag/schemas` 端点:system:rag:manage 权限 + 租户白名单复校 + 精确 response_model,不可用数据源走既有同形拒绝
- [x] 1.4 上传 service 增 schema 存在性校验(语法闸之后、文档解析之前),`SchemaNotAllowed` → 400 `SCHEMA_NOT_ALLOWED`
- [x] 1.5 rag 域测试全绿(api 测试子集 + mypy/ruff)

## 2. 触发器目标存在性(后端)

- [x] 2.1 测试先行:create 目标不存在 400 `TARGET_NOT_FOUND`、update 仅改 target_key 时重校验、目标已删的存量触发器仍可停用/改非目标字段(止损不堵)——三条失败测试
- [x] 2.2 `trigger/ports.py` 增 `target_exists(target_type, target_key, tenant_id)` 端口,组合根注入实现(workflow/bot 各自按 key+租户存在性查询,与派发期解析同源)
- [x] 2.3 service create 必校验、update 仅 target_key 变更时校验,`TargetNotFound` → 400 `TARGET_NOT_FOUND`
- [x] 2.4 trigger 域测试全绿(api 测试子集 + mypy/ruff)

## 3. 语义关系存在性 + 分层归位(后端)

- [x] 3.1 测试先行:手工关系 source/target 任一为未知表 400 `RELATION_TABLE_UNKNOWN`、双已知表 201 落库 source=manual——失败测试
- [x] 3.2 `semantic/ports.py` 增 `knowledge_table_exists(tenant_id, datasource_key, schema_table)` 端口,组合根以 rag 知识仓储实现
- [x] 3.3 关系创建从 router 下沉为 `SemanticService.add_relation`(数据源校验/存在性校验/落库收进 service),`RelationTableUnknown` → 400 `RELATION_TABLE_UNKNOWN`
- [x] 3.4 semantic 域测试全绿(api 测试子集 + mypy/ruff)

## 4. 契约管线

- [x] 4.1 `make contracts` 重导出 openapi.json + 前端类型再生成,核对新增端点/错误码无意外漂移
- [x] 4.2 api 侧全量 make check 子集绿

## 5. 前端 · RagPage schema 选择框

- [x] 5.1 上传表单 schema 由 Input 换 Select:数据 = schemas 端点(随上传数据源联动刷新),切换数据源重置已选 schema,候选未加载/为空时禁用上传按钮
- [x] 5.2 `SCHEMA_NOT_ALLOWED` 400 映射为可读错误文案(表单内 role=alert 既有形态)

## 6. 前端 · BotsPage 工具多选 + 弹窗补齐

- [x] 6.1 工具字段由 CSV Input 换 Checkbox 勾选清单:数据 = `toolList` 注册表,每项展示名称+描述+写操作标记,表单态 `string[]`,默认勾选 execute_sql + rag_retrieve,空选合法(零工具 Bot 形态)
- [x] 6.2 新建 Bot 弹窗包 `<form>` 回车提交(对齐既有弹窗范式),主按钮改 submit
- [x] 6.3 创建密钥复制按钮加 2s「已复制」反馈态

## 7. 前端 · TriggersPage 目标联动下拉

- [x] 7.1 目标标识 Input 换按 target_type 联动的 Select(workflow → 工作流列表,bot → Bot 列表);编辑回显目标不在列表时注入 `(已删除) <key>` 禁用占位项防静默改值
- [x] 7.2 `TARGET_NOT_FOUND` 400 映射为可读错误文案

## 8. 前端 · SemanticPage 表/列选择器

- [x] 8.1 新增关系表单:左/右表 Input 换 Select(当前数据源知识条目,展示 `schema.table` + 业务别名);JOIN 键拆左列/右列两个 Select(数据 = 所选表条目列清单),表条目缺失时列 Select 降级 Input,表未选时列 Select 禁用
- [x] 8.2 `RELATION_TABLE_UNKNOWN` 400 映射为可读错误文案

## 9. 前端一致性三小件

- [x] 9.1 ChangePassword 错误提示补 `role="alert"`
- [x] 9.2 FeedbackPage 处置弹窗包 `<form>` 回车提交

## 10. 收口

- [x] 10.1 web 侧 tsc + eslint 全绿
- [x] 10.2 浏览器实测眼见为实:四处表单新交互逐一走通(空态/联动/回显),三处 400 文案在 API 直调下可见
- [x] 10.3 全量 make check + contracts 零漂移核对

## 11. 用户裁决收编(原挂账两项)

- [x] 11.1 测试先行:cron 预览端点失败测试(合法表达式返回递增 3 次/非法 400 同文案口径)
- [x] 11.2 `GET /system/triggers/cron-preview` 端点:复用 parse_cron/next_fire 迭代取 3 次,CronError → 400,MANAGE 权限 + response_model
- [x] 11.3 UsagePage 模型/任务筛选换 Select:候选自既有 usageSummary 响应 items 派生(纯前端,零契约),首位「全部」项
- [x] 11.4 TriggersPage cron 输入 400ms 防抖预览(接下来 3 次/非法红色原因),弹窗关闭停查
- [x] 11.5 make contracts 重导出 + 三域测试与 web 检查全绿
