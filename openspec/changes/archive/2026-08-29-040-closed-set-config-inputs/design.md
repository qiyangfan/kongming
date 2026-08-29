# 040 · 设计

## Context

四处管理面配置入口对封闭集合开放自由输入，写边界只有语法闸没有存在性闸（见 proposal Why）。可枚举集合的真实来源：RAG 内省扫描的 schema 范围计算现内联在 `rag/repository.py::introspect_all`（default 源 = `[tenant_<key>, public]`，动态源 = resolver 取 `default_schema`）；工具注册表已有管理端只读查询（`toolList`）；workflows/bots 均有既有列表查询；知识条目表清单即 rag 列表查询（含 `columns_comment` 列清单）。语义关系创建当前是 router 直连 `SqlSemanticRepo`，用例逻辑落在适配层；触发器创建只校验 `target_type` 枚举，目标存在性留给派发期。

## Goals / Non-Goals

**Goals**

- 写边界（service 层）对三处封闭集合 fail-fast：rag schema、触发器目标、语义关系表；前端选择框全部镜像同一集合来源。
- 集合计算单一真理源：schema 集合只允许一处实现，端点/上传校验/内省三方共用。
- 语义域分层归位：关系创建收进 service，跨域存在性经 port 反转。

**Non-Goals**

见 proposal Non-goals（挂账项、存量数据回溯、bot tools 后端语义变更均不在内）。

## Decisions

### D1 · schema 集合：抽取单一计算点，不另起第二套

`rag/repository.py` 内把「按 (tenant_key, datasource 行) 算可扫描 schema 列表」从 `introspect_all` 抽为独立方法（default 特判 + 动态源 `default_schema`），`introspect_all`、新端点 `GET /system/rag/schemas`、上传存在性校验三方全部调它。上传校验放 service 层 `upload_blocks` 入口（datasource 可用性已解出之后），错误类型 `SchemaNotAllowed` → router 映射 400 `SCHEMA_NOT_ALLOWED`。

- 备选：前端由 datasource options 自行推导（web 知道租户 key 与 default_schema）→ 否，内省口径将来变化（如 default 源纳入更多 schema）时前端必漂移，且把域逻辑复制进适配层。
- 备选：schemas 并入既有 datasource options 响应 → 否，该端点是切换器共用的低权限面，管理配置信息不应外溢；专用端点挂 `system:rag:manage` 权限，职责干净。
- **排序**：语法闸（router，解析前）拦畸形值；存在性闸在 service 层 `upload_blocks` 入口（解析之后）——解析受文件大小上限约束（1MB/10MB 分档）为毫秒级，「贵活」担忧不成立，闸留在 service 使 API 直调同受约束（纵深）。
- 动态源校验需要 datasource 行（拿 `default_schema`）：service 复用 `resolve_for_read`（只读解析），不新开查询路径。

### D2 · Bot 工具多选：Checkbox 组，不引新组件

工具注册表约十几项，shadcn `Checkbox` + `ScrollArea` 的勾选清单即最优：一屏可扫、键盘可达、零新依赖，每项带描述与「写」标记（注册表元数据现成），管理员勾选时即完成一次权限认知。默认勾选维持 `execute_sql` + `rag_retrieve`。空选合法（039 零工具 Bot 形态）。表单态从 CSV 字符串改为 `string[]`，提交契约（`tools: list[str]`）不变。

- 备选：multi-select combobox（badge 形态）→ 否，为十几项的固定集合引 combobox 交互复杂度不值；badge 形态在窄弹窗内易换行溢出。
- 后端不动：`resolve_tools` 交集是 039 漂移容忍裁决（注册表删工具不炸存量 Bot），拒绝未知名与之冲突。不对称是有意的：tools 的语义是「声明能力的上界、容忍漂移」，schema/目标/表的语义是「引用必须存在」。

### D3 · 触发器目标：type→target 级联 Select + 存在性端口

前端：`target_type` Select 切换时，目标 Select 换数据源（workflow → `listWorkflows`，bot → `/system/bots`），提交值仍为 key。编辑回显时目标不在列表（已删）→ 注入 `(已删除) <key>` 禁用态占位项，防「打开编辑再保存」静默改值。

后端：`trigger/ports.py` 增 `target_exists(target_type, target_key, tenant_id) -> bool` 端口，组合根注入实现（workflow/bot 仓储各自按 key+租户的存在性查询）。`create` 必校验；`update` 仅当请求携带且变更 `target_key` 时重校验（`set_enabled` 与非目标字段更新不查——目标已删的存量触发器必须留得掉停用/删除的止损路径，见 delta 场景）。错误 `TargetNotFound` → 400 `TARGET_NOT_FOUND`。

- 备选：校验写在 router → 否，用例逻辑归 service（域纪律）；本项目已有语义域 router 直连 repo 的前车之鉴，不再复制该形态。
- 备选：级联禁用被删目标的触发器 → Non-goal（运行失败执行历史可见；级联语义牵扯 bot/workflow 删除域，独立议题）。

### D4 · 语义关系：表/列选择器镜像知识条目，service 归位

前端：左/右表 Select 数据 = 当前数据源知识条目（`ragApi` 列表一次拉全，`page_size` 取列表上限值，管理面量级为数十条；展示 `schema.table` + 业务别名，值 = `schema.table`）。JOIN 键拆左列/右列两个 Select，数据 = 所选表条目的 `columns_comment` 键集；表条目缺失（理论不可达，防御保留）时该列 Select 降级 Input。表未选时列 Select 禁用。

后端：`add_relation` 从 router 下沉为 `SemanticService.add_relation`；`semantic/ports.py` 增 `knowledge_table_exists(tenant_id, datasource_key, schema_table) -> bool` 端口，组合根以 rag 知识仓储实现（按四元键前缀查存在）。source/target 双查，错误 `RelationTableUnknown` → 400 `RELATION_TABLE_UNKNOWN`。指标表单（名称/口径/别名）不在本变更：开放词表与专家 SQL 片段，形态正确。

- 备选：只改前端不改后端 → 否，API 直调仍可写死配置，写边界闸是本变更的存在理由；前端选择框只是同一集合的镜像。

### D5 · 一致性三小件：跟随既有范式，不立新范式

- 改密页错误 `<p>` 补 `role="alert"`（对齐全站 20+ 处既有形态）。
- BotsPage 新建弹窗、FeedbackPage 处置弹窗包 `<form onSubmit={e => e.preventDefault(); submit}>`，回车提交对齐既有 11 处弹窗；主按钮改 `type="submit"`。
- 创建密钥复制按钮加 2s「已复制」态（useState + setTimeout 回写），图标不动。

### D6 · 错误码与契约

三个新 400 detail（`SCHEMA_NOT_ALLOWED` / `TARGET_NOT_FOUND` / `RELATION_TABLE_UNKNOWN`）与既有 detail 风格一致（大写蛇形、机器可读）；`GET /system/rag/schemas` 挂精确 `response_model`（ADR-005 既有教训：GET 不挂 response_model 会把前端逼到手写类型）。完成后 `make contracts` 重导出，前端类型再生成。

### D7 · UsagePage 筛选 combobox：纯前端派生，零契约变化

模型/任务候选直接自既有 `usageSummary` 响应的 items（每行自带 `model`/`task` 维度）`Array.from(new Set())` 派生——summary 本就是该页已在拉的查询，不新增端点、不改契约。控件换 Select，首位「全部」项（value=all → 过滤串空串）。原判「需新枚举端点」系过强断言，收回（用户质疑成立）。

### D8 · cron 预览：小端点 + 前端 400ms 防抖

后端 `GET /system/triggers/cron-preview?expr=`：复用 `parse_cron`/`next_fire` 纯函数迭代取**接下来 3 次**（单次看不出日历跳跃类缺陷——036 教训；循环 after=上次结果，跨度天然有界），`CronError` → 400 附原文案（与保存期校验同口径），MANAGE 权限 + response_model。前端 cron 输入 400ms 防抖后查询，输入框下方 muted 文案展示「接下来 3 次」，400 时同位置展示红色原因；弹窗关闭即停查（query enabled 绑 form）。

## Risks / Trade-offs

- **BREAKING：API 直调方按旧口径上传越界 schema 将 400** → 收益即目的（毒化数据不再产生）；存量越界条目不受影响（闸只管增量），列表页可见可删。发布说明点出。
- **schema 集合与库内实态的偏差**：default 源返回的 `tenant_<key>` 是路由约定 schema，物理上随租户首查创建；若库尚未初始化，枚举仍返回约定值——上传的文档先行于建表是合法序（文档与 DDL 无外键耦合），非缺陷。
- **动态源 default_schema 之外的 schema 无法手工建条目**（原自由输入的「逃生门」消失）→ 有意裁决：扫描通道同样不覆盖那些 schema，其条目本就是永不召回的死数据；真需求出现时再议「扫描范围扩展」而非「上传越界」。
- **触发器 update 端口查询多一次存在性往返** → 仅在 target_key 变更时发生，管理面低频操作，忽略不计。
- **语义页多拉一次知识列表** → 管理面量级（数十条），一次拉全换两处 Select 的数据一致性，值当。

## Migration Plan

纯增量契约 + 三处 400 收紧，无数据迁移、无表结构变更。部署顺序无感（前后端可同批发；旧前端在新后端下仅失去 schema 自由输入的意义，值域仍合法）。回滚 = 回退镜像，无状态残留。

## Open Questions

（无）
