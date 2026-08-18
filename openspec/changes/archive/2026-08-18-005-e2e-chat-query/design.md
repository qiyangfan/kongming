# 005 设计:端到端问答链路

## Context

见 proposal.md「Why」。现状事实:
- agent 面 8 节点骨架(`apps/agent/kongming_agent/graph/build.py`)全是桩,
  路由函数已就位(含「失败一律进 selfheal 分类计数,不直达 generate」铁律);
  `openai`/`httpx` 已在 agent 依赖。
- api 面 `/internal/sql/execute`(004)响应显式三态 verdict 字段
  (PASS/BLOCKED/ERROR),reason 为编码化摘要(FORBIDDEN_DML/PARSE_ERROR/
  DATA_SCOPE_UNSUPPORTED/驱动层错误摘要)。
- 上游经典 7 节点实现存于上游 git 历史(提交 37b9c95,515 行;当前 HEAD 已是
  ReAct 架构,非移植对象);三份核心 prompt 资产已逐字提取(见下)。
- chat_session/chat_message 表 baseline 已建全(含 status 三态 CHECK、
  result_json、反馈列),本变更零迁移。
- multitenant spec:X-Tenant-Id 头(缺省取用户默认租户),绑定白名单校验,
  SUPER_ADMIN 不豁免。
- 本机 podman;api 8000/agent 8001;agent `.env` 的 LLM key 现为 dummy。

## Goals / Non-Goals

Goals:proposal「What Changes」全部;安全规则类(require_perm/会话隔离/
租户校验/内部通道)测试先行。

设计级边界(非重复 proposal Non-goals):
- LLM 调用只做同步 request/response,不做流式、不做并发扇出;
  三节点各一次调用(understand 有 history 时才调,无 history 走单轮改写也调——
  意图规整对 generate 质量有实测收益,上游如此)。
- 不做 LLM 调用的重试/退避(SDK 默认),selfheal 是唯一重试机制。
- chart 推断为 api 侧纯函数,不进 agent(洞察归 agent,可视化建议归控制面——
  前端只消费一个字段)。

## Decisions

### D1 agent 端点形态:POST /query(同步,一次性返回)

请求:`{trace_id, tenant_key, user_id?, question, history: string[],
datasource_key="default"}`;响应:`{success, verdict?, summary, insights[],
stats{}, columns[], rows[], sql, retry_count, error?}`。
- history 为 user 问句字符串列表(旧→新),与上游 QueryRequest 对齐;
  AgentState 内部亦收敛为 list[str](骨架的 list[dict] 一并改)。
- verdict 取最后一次 execute 的三态;PASS 且 summarize 完成 → success=true。
- 备选:SSE 流式(上游 chat_stream 打字机)——留 query-streaming 域,本变更
  同步响应已满足 e2e。

### D2 三节点 prompt 移植(上游原文保值,逐字)

- understand 多轮 system:「你是 NL2SQL 助手。用户当前问题可能包含指代(这些/
  它们/上面提到的)或省略(再查/接着/只看)。结合历史问题,把当前问题补全为
  一个独立、清晰、可独立理解的查询意图。只输出补全后的问题(一行),不要解释,
  不要重复历史。」;user 模板 = 编号历史(最近 5 轮)+ 当前问题。单轮分支
  走简短改写 prompt。输出 strip 后为空则兜底原问句。
- generate system(SQL_GEN_SYSTEM,上游原文,含只读 SELECT/PG 方言/列投影
  精确/显式 JOIN/聚合识别/明细 LIMIT 100 保护/只输出 SQL 七条规则 + 反例);
  user = 问题 + schema 提示 + selfheal 错误喂回(「上次生成的 SQL 有误:
  {error}请修正。」)。temperature=0.1;输出清洗:剥反引号/sql 前缀标记。
- summarize system(结构化洞察 JSON:summary + insights[{type:max/min/trend/
  anomaly/quality, text}],只输出 JSON);stats 本地确定性计算(行数 +
  数值列 max/min/avg,bool 显式排除);解析失败降级:剥标记 → json.loads →
  首尾花括号子串再试 → 纯文本兜底为 summary,insights 为空,不报错。

### D3 validate = 本地轻量预检(不回调 api 校验端点)

sqlglot 解析:非空、可解析、单语句、根节点 Select。检出非 Select(写操作)
→ 标记 permission_block(终态不重试);解析失败/多语句 → 进 selfheal
(LLM 输出格式问题,值得重试)。深度校验权威仍在 api 防火墙(004 双闸),
预检只省无效往返与配额——不复刻防火墙规则集,不做方言级二次实现。
备选:回调 api 新增独立校验端点——多一次往返且 api 无此端点,execute
本身已含初检,否决。

### D4 三态分类以显式 verdict 字段为准

execute 节点把 api 响应 verdict 存 `state.execution.verdict`;
BLOCKED(含 DATA_SCOPE_UNSUPPORTED/POST_REWRITE)→ _after_selfheal 直接
fail(权限类不重试,nl2sql-agent spec);ERROR → retry_count<2 进 selfheal。
骨架桩的 `reason.startswith("BLOCKED")` 判定替换为显式 verdict 比较。
selfheal 节点把「validate 拦截原因 / execute 的 reason」写入 state.error
(喂回 generate 的关键一跳,上游易漏点);generate 成功时清空 error。
agent 回调 api 的 HTTP 层错误(403/404/500/超时)收敛为 verdict=ERROR 语义
进 selfheal,重试耗尽 fail。

### D5 api 侧 /chat/query 编排顺序

current_user(JWT)→ require_perm("chat:execute")→ X-Tenant-Id 解析
(缺省=用户绑定租户中租户表 sort/created 最早的「默认租户」语义:取用户
绑定的 default 租户,未绑定 default 则取首个绑定租户;X-Tenant-Id 显式
传入时校验绑定)→ 会话(无 session_id 且 persist → 新建,标题=首问前
30 字符;有 session_id → 校验归属当前 user+tenant,不命中一律 404 不区分)
→ history 补全(显式优先,否则按 session 取最近 5 条 user 问句)
→ trace_id=uuid4 hex → 回调 agent → 落消息(USER 先落,ASSISTANT 含
结果/状态后落;agent 失败落 FAILED)→ 响应。
- require_perm 首发实现:claims.sub → DB 联查(用户→角色→角色权限→权限点,
  实时不缓存,满足 rbac spec「撤销立即生效」);无点 403。
- 种子:PERMISSIONS 注册表加 ("chat:execute","问答执行",3);
  SUPER_ADMIN 既有「增量补齐全部注册点」逻辑自动覆盖;DEVELOPER 新增
  「推荐绑定清单」增量绑定(只增不摘,不覆盖运行时改动)。
- 租户归属/会话隔离测试先行(宪法 Article III)。

### D6 /internal/meta/schema 内省实现

控制面会话读 information_schema.tables/columns JOIN pg_description
(obj_description/col_description),schema ∈ {tenant_<key>, public},
对象类型 BASE TABLE + VIEW;按 schema,table 排序,表数上限 50(配置项),
截断置 truncated=true。租户解析复用 004 的 fail-closed 语义(未知/禁用 404)。
非 default 数据源:元数据同样 fail-closed(动态库内省随数据源变更)。
- 缓存:首期无缓存(实时);表结构低频变更,性能可接受(单次几 ms)。

### D7 chart 建议纯函数(chat.service 内,零框架)

规则(确定性,顺序短路):首列值含 date/datetime 类型特征 → line;
类别列基数 ≤8 且有数值列 → pie;存在类别列+数值列 → bar;否则 none。
日期判定用 Python 类型(datetime.date/datetime/str ISO 前缀)+列名启发
(*_at/*_date);数值判定 int/float/Decimal(排除 bool)。

### D8 LLM 客户端与超时预算

AsyncOpenAI(base_url/model/key 来自 settings);单次调用超时 30s;
temperature:understand 0.1 / generate 0.1 / summarize 0.2。
api→agent 总超时 120s(覆盖 understand+generate+≤2 次 selfheal+summarize);
agent→api execute 单次 30s。P95<15s(nl2sql spec)在真 LLM 下由冒烟实测记录。
备选:langchain——上游已用裸 openai SDK 验证,不引入抽象层。

## Risks / Trade-offs

- [LLM 幻觉表名/列名(无 RAG 收窄)] → 过渡形态内省全量提示 + selfheal
  两轮重试消化;dev 库表少可控;rag 变更落地后切换召回收窄。
- [真 LLM 冒烟依赖有效 key;`.env` 现为 dummy] → 冒烟前检查 key;缺失则
  全链路 mock-LLM 测试兜底,真冒烟待用户配 key 后补跑并记录。
- [agent 总延迟多次 LLM 叠加,可能超 15s] → 超时预算 D8;冒烟记录实测延迟,
  超标再议流式/并行(非本期)。
- [prompt 注入诱导生成写 SQL] → 004 双闸(防火墙+只读池)兜底;agent 预检
  为第三道;本变更不新增攻击面。
- [chat_message 随用量增长] → 留存/归档随 audit 域后续变更;本期不背。
- [require_perm 每请求查 DB] → 单行联查成本极低;缓存随 RBAC 管理面变更
  评估(绑定变更立即生效优先于性能)。

## Migration Plan

无 schema 迁移(chat 表 baseline 已建全)。部署顺序:api 先(新端点纯加法,
种子只增不改)→ agent 后。回滚 = 进程回退;种子新增的权限点/绑定不回滚
(只增不改纪律,残留无功能影响)。

## Open Questions

- 内省提示的表注释质量:INTROSPECTION 裸注释对中文业务库可能信息量不足,
  knowledge 域 LLM 润色落地后,retrieve 提示源是否从「实时内省」切换为
  「rag 库润色文本优先」——随 rag/knowledge 变更评估,不影响本变更契约。
- SSE 流式与打字机摘要(query-streaming 域,第三梯队)。
