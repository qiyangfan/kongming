# 019 tool-calling 设计

## Context

现状(见 proposal Why):agent 面是固定 8 节点 LangGraph 图(`/query` +
`/query/stream`,017 已带流式事件),所有数据获取经 api 面
`/internal/*` 回调(sql/execute、meta/schema、rag/retrieve);api 面
chat 域 `_prepare_query`/`_finalize_reply` 已把同步/流式编排抽成共用段;
权限点体系(007)、审计(004/015)、动态数据源(008)均已就位。
约束:ADR-002(agent 零凭证)、ADR-004(新表走迁移)、ADR-005(契约
生成)、prompt 资产纪律(prompts.py 逐字资产,新 prompt 全新撰写不受
逐字约束但全角纪律沿用)、import-linter 分层契约。

## Goals / Non-Goals

Goals:ReAct 内核落地且老链路逐字节保留;工具执行收口 api 单端点六层
防御;写工具确认流最小闭环;tool_call 事件与前端可视化;管理面
清单+审计查询。Non-Goals 见 proposal(不重复)。

## Decisions

### D1: ReAct 内核 = 独立 LangGraph 图 + 独立端点,老图零改动

agent 新增 `graph/react/`(state/react_nodes/react_build),图拓扑:
`START → react_llm → (有 tool_calls ? tool_dispatch → react_llm : finalize
→ END)`。端点 `POST /query_react` 与 `POST /query_react/stream`(内部
令牌 middleware 已覆盖全 app,零改动)。终态构造复用 `_build_query_out`
扩展形态(见 D7)。
- 备选:在既有图上挂条件分支复用节点——否决:8 节点图是评估基线的
  被测对象,任何拓扑变动都污染 016 基线口径;独立图物理隔离回归面。
- 备选:不用 LangGraph 手写 while 循环——否决:流式事件通道
  (get_stream_writer)与图语义是 017 已验证资产,复用成本最低。

### D2: 工具 schema 由 api 注入,agent 零真理源

`QueryReactIn` 在 QueryIn 字段基础上纯增 `tools: list[ToolSpec]`(name/
description/parameters(JSON Schema dict)/write/require_confirm)。api 侧
注册表(`toolcall/registry.py`,代码内声明式注册)按调用方角色集过滤后
注入。agent 只把 tools 透传给 LLM(chat_with_tools),不校验、不缓存、
不补充。
- 上游同构(Java 注入 Python);Kongming 双进程直接映射。
- 备选:agent 启动时经回调拉取注册表并缓存——否决:缓存失效/多实例
  一致性引入新状态面,每请求注入无状态最简;tools 注入体积 ~KB 级,
  可忽略。

### D3: api 新域包 kongming/toolcall/(宪法四层)

- `models.py`:ToolSpec/ToolCallReq/ToolOutcome 纯数据 + 参数摘要
  (args_digest:截断 500 字符 + 敏感键名 password/secret/token 值替换
  「***」)纯函数 + 幂等键生成(未提供时 sha256(user+tool+args))。
- `registry.py`:内置工具注册表(代码声明式,与权限点同哲学=随代码
  发布的契约数据);每项含 handler 键(validate_sql/execute_sql/
  rag_retrieve/create_announcement)、perm_code、write、require_confirm、
  JSON Schema。
- `service.py`:六层防御编排(见 D4),handler 委托既有能力:
  validate_sql→sqlfw.firewall;execute_sql→internal.service.execute 复用
  (同 session 外独立事务边界,design 注:工具执行不复用 chat 请求
  session,各自 get_session 独立,防半提交);rag_retrieve→rag 域
  service;create_announcement→本域 repository 写公告表。
- `repository.py`:sys_tool_audit 写入/分页查询 + sys_announcement 写入
  (幂等键唯一)+ 待确认记录(见 D5,复用 sys_tool_audit 行存
  PENDING_CONFIRM,免新表)。
- `router.py`:`/internal/tool/execute`(内部面,挂 internal/router.py
  同 middleware)+ `/system/tools` 清单 + `/system/tools/audit/page`
  (管理面)。
- import-linter 第 9 契约:toolcall models/service/ports 零框架。

### D4: 六层防御落点(api 侧 service 单点编排)

L1 内部令牌(middleware 既有)+ user-tenant 绑定复校(复用
internal/service 的租户解析路径,抽共用函数防两份漂移);L2 工具权限点
= repo 查用户角色集权限(复用 auth 域 perm 查询);L3 租户白名单
fail-closed;L4 JSON Schema 校验(新依赖 `jsonschema`,主依赖——
成熟库,无替代手写);L5 sys_tool_audit 全量落(独立短事务,与
SqlAuditWriter 同范式:失败仅日志不阻断拒绝语义)+ Redis 固定窗口
限流(`km:toolrl:{tenant}:{user}` INCR+EXPIRE 60s,默认 60 次/分,
config 可调;Redis 不可用=fail-open 仅告警,与 007 吊销降级同口径
挂账);L6 写工具幂等键 + require_confirm 确认闸。
- 限流不做令牌桶/分布式精确:单 worker 前提(008 挂账②同口径),
  固定窗口足够,多 worker 语义随部署评估登记。

### D5: 写工具确认流 = 挂起 + 独立确认端点(不恢复 ReAct 循环)

时序:tool/execute 遇 require_confirm → sys_tool_audit 落
PENDING_CONFIRM 行(id=confirm_id,含 args_json 密级低、幂等键、
expires_at=+15min)→ 响应 confirm_required → agent 作为工具观察喂回
LLM(「该操作需用户确认,已挂起,confirm_id=…」)→ LLM 终止循环
产出说明性答案 → chat 响应纯增 `pending_tool_calls:[{confirm_id,
tool_name, args_digest}]`(从本会话本次 trace 的 PENDING_CONFIRM 行聚合)
→ 前端渲染确认卡 → 用户点确认 → `POST /chat/tool-confirm
{confirm_id, decision}` → api 校验归属(confirm 行 user_id+会话归属)
+ 过期 + 幂等 → 重走六层执行(参数复校!)→ EXECUTED 审计 +
assistant 回执消息(复用 _finalize 落库段)。
- 备选:序列化 ReAct 状态恢复续跑——否决:状态机序列化引入重量级
  复杂度,确认后 LLM 续做的增量价值低;「执行回执消息」满足可感知。
- 拒绝/过期:落 CONFIRM_REJECTED/EXPIRED 语义审计(status 枚举内),
  回执消息说明。
- confirm_id = sys_tool_audit 行 uuid,天然防枚举(归属校验先决)。

### D6: agent LLM 客户端新增 chat_with_tools

`LlmClient.chat_with_tools(system, messages, tools) ->
{content: str|None, tool_calls: list[{id,name,args_dict}]}`;openai SDK
原生 tools 参数;args JSON 解析失败收敛为「工具观察=参数畸形」喂回
(不裸抛,让 LLM 自纠)。温度 0.1(与 generate 同)。max_tool_rounds
缺省 8(config KM_AGENT_MAX_TOOL_ROUNDS)。
- 备选:prompt 式 JSON 协议(无 tools 参数)——否决:dashscope
  qwen-plus 原生支持 tools(冒烟验证点 S1),原生协议畸形率低一个
  量级;若冒烟证伪再回退(Open Question O1)。

### D7: ReAct 终态同构映射

finalize 节点:summary=LLM 最终文本;columns/rows/sql=循环中最后一次
executed 的 execute_sql 观察(无则空);stats=复用 compute_stats 纯
函数(018 接地口径不变);insights=空(结构化洞察为 8 节点图 summarize
专属形态,ReAct v1 不复制——summary 文本承载;零回归条款只管老链路,
ReAct 是新链路,契约只承诺字段同构非值等价);verdict=最后一次
execute_sql 的 verdict(BLOCKED 语义保留:权限拦截→verdict=BLOCKED
→ chat 侧 REJECTED 映射零改动);retry_count=实际工具调用轮数(语义
复用,审计可溯)。
- insights 留空是字段同构内的合法值([]),前端卡片零改动。

### D8: 流式 = react 图挂 stage_wrapper + tool_dispatch 发射 tool_call

react_llm/tool_dispatch/finalize 挂 017 stage_wrapper(stage 事件:react
循环节点名);tool_call 事件由 tool_dispatch 经 get_stream_writer 发
running/done/rejected(status 映射:executed→done,防御拒绝→rejected,
confirm_required→done 且 result 摘要含「待确认」)。api 中继(017
_consume_agent_stream)事件名白名单加 tool_call;终态落库段零改动。
web dispatchSseBlock 加 tool_call 分支累积进卡片状态。

### D9: chat 侧 agent_mode 分支 = 前段/终态全共用,仅「调谁」不同

`_prepare_query` 零改动;query/start_stream 按 agent_mode 选
agent.query 或 agent.query_react(注入 tools=registry.filter(perms));
`_finalize_reply` 零改动(pending_tool_calls 聚合在 finalize 前查本
trace 的 PENDING_CONFIRM 行附加)。契约:QueryChatIn 纯增
agent_mode;ChatOutcome 纯增 pending_tool_calls。
- 备选:独立 /chat/react_query 端点——否决:双端点双份编排漂移面,
  纯增字段+分支是同构既有先例(datasource_key 形态)。

### D10: web 面

StreamCard/AssistantCard 增 tool 调用链 chips 区(状态图标 lucide:
Loader2/Check/X);确认卡(pending_tool_calls 非空时):工具名+参数
摘要+「确认执行/拒绝」按钮 → tool-confirm 端点 → 回执消息经 react-query
失效刷新;/admin/tools 第八子页(nav.ts 配置单源加项,清单表+审计
分页筛选,复用 015 审计页模式);Agent 模式开关:会话区 Switch
(shadcn 已有组件),datasource-store 同款 zustand 存 km.agent_mode,
缺省 recipe。

### D11: 迁移(ADR-004)

alembic 0008:sys_tool_audit(id uuidv7 pk, trace_id varchar(64),
user_id uuid, tenant_id uuid, tool_name varchar(64), args_digest text,
status varchar(24) CHECK ∈ {EXECUTED,REJECTED,ERROR,RATE_LIMITED,
PENDING_CONFIRM,CONFIRM_REJECTED,CONFIRM_EXPIRED}, error_reason text,
duration_ms int, datasource_key varchar(64), idempotency_key
varchar(128), expires_at timestamptz, created_at timestamptz;
索引 (tenant_id, created_at desc)、(tool_name, created_at desc)、
idempotency_key 普通索引)+ sys_announcement(id uuidv7, tenant_id,
title varchar(200), content text, publisher_id uuid, idempotency_key
varchar(128) UNIQUE, created_at)。
- PENDING_CONFIRM 行的 expires_at 仅写工具确认用,其余 status 为 null。

### D12: 权限点种子(只增不摘)

tool:sql:validate(排序 10)/ tool:sql:execute(11)/ tool:rag:retrieve(12)
/ tool:announcement:create(13)/ system:tool:manage(14);SUPER_ADMIN
自动覆盖(既有机制);DEVELOPER 推荐绑定只读三件套(与 chat:execute
同哲学);smoke-self 活数据无工具权限 → react 退化路径冒烟用例。

### D13: 评估等价验证

evals 支持 `KM_EVAL_AGENT_MODE` 环境变量(缺省 recipe;react 时
/chat/query 请求携带 agent_mode=react),README 记录 ReAct 链路基线
(冒烟轮跑 smoke+full,与 recipe 基线对照);CI 不加跑(成本),评估
环境显式触发(016 既定口径)。

### D14: rag_retrieve 工具内回退内省(实施期评估实证)

ReAct 模式首跑评估 olist_smoke_002 打满轮次上限:知识库未抓取的租户
召回双空,LLM 只能用 execute_sql 盲目探测 information_schema/pg_tables
摸表结构,循环 8 轮不收敛。修=rag_retrieve 工具内回退实时内省(与
8 节点图 retrieve 节点「RAG 优先、内省兜底」同语义,spec「内置工具集」
已补句),修复后 react smoke 5/5。

### D15: ReAct 效率纪律 + 重复熔断(UI 冒烟实证)

「每个影片分类各有多少部电影」在 ReAct 模式打满 8 轮:LLM 拿到表结构后
仍反复 rag_retrieve/探测性 LIMIT 查询不收敛。双管齐下:①REACT_SYSTEM
新增效率纪律三条(≤4 轮/禁系统目录探测/禁重复检索);②tool_dispatch
内核级重复熔断——同 trace 内同工具同参数(规范化 JSON 签名)已成功过
的调用不再回调控制面,直接观察「已执行过,请基于已有信息作答」喂回。
修复后同题一次收敛(16 分类全量+图表+stats 齐)。

### D16: 确认卡独立于快照(UI 冒烟实证,严重)

017 快照退位机制(finalizeOk 后历史 refetch 命中 message_id 即撤
finalized)与确认卡冲突:确认卡最初挂在 finalized 快照上渲染,历史
接管瞬间确认卡消失(pending_tool_calls 是响应级瞬态不落库)。修=
ChatPage 独立 pendingConfirm 状态,确认卡常驻直到全部已决或切换
上下文(取消闸清除);回执消息落会话经 invalidate 呈现。

### D17: tool_call 事件状态映射(UI 冒烟实证)

confirm_required 最初落 rejected(非 executed 即 rejected 的二分),
与 spec「confirm_required 以 done 下发」立约不符;修=outcome.status ∈
{executed, confirm_required} → done。

### D18: LLM 确认指引与确认卡机制对齐(UI 冒烟实证)

REACT_SYSTEM 第 4 条原指引只说「向用户说明待确认事项」,LLM 自由发挥
引导用户「回复确认」——文字回复会走新问答链路而非确认端点,交互错位。
修=prompt 明确「系统已在消息下方弹出确认卡片,请在卡片上点击
确认执行或拒绝,不要让用户用文字回复确认」。

## Risks / Trade-offs

- [qwen-plus tool calling 协议兼容性未实证] → 冒烟 S1 首验;证伪则
  回退 prompt 式 JSON 协议(O1),spec 不动(行为契约与协议解耦)。
- [ReAct 循环 token 消耗数倍于老链路] → 轮次上限 8 + 注入工具集按
  授权收窄 + 缺省 recipe 不变,react 为用户显式选择;用量观察随
  multi-model-routing 域(第二波)采集。
- [确认流增加消息模型复杂度] → pending_tool_calls 纯增、确认端点
  独立,不动既有消息状态机;回执=普通 assistant 消息,历史回显零改动。
- [JSON Schema 新依赖] → jsonschema 为主流成熟库,license MIT;替代
  手写校验被否(边界坑多)。
- [ReAct 自主编排可能跳过 validate_sql 直调 execute_sql] → 可接受:
  执行路由本身含防火墙全量防御(004),validate_sql 工具是 LLM 省配额
  的辅助,非安全闸;安全闸恒在 api 侧。
- [LLM 幻觉工具名/参数] → 未注册工具拒绝+审计(spec 已立约),参数
  畸形作为观察喂回自纠,轮次上限兜底。
- [ReAct 循环不收敛(LLM 过度探索)] → D15 双管:效率纪律 prompt +
  重复熔断内核闸;轮次上限最终兜底。
- [UI 确认点击后页面跳转到旧会话(冒烟观察,未复现)] → 确认全链功能
  已实证闭环(回执落库/公告单行/卡片消退);漂移疑与 HMR 或冒烟操作
  竞态相关,R1 评审复测;若复现按会话 404 收敛 effect 链深挖。

## Migration Plan

1. alembic 0008 两表(空表瞬时,零存量);2. 种子权限点(幂等只增);
3. agent/api/web 滚动重启任意序(agent_mode 缺省 recipe,新旧组合
  均零回归);4. 回滚=agent_mode 不启用即回老链路,表与权限点留库
  无害(种子只增不摘纪律)。

## Open Questions

- O1:若 qwen-plus tools 协议冒烟证伪,prompt 式 JSON 工具协议回退
  形态(工具清单进 system 段+LLM 输出 JSON 指令)——行为契约不变,
  仅 agent 内部协议层替换,实施期冒烟定。(已证:qwen-plus tools 原生可用)
- O2:确认回执后是否自动续跑一轮 ReAct(把执行结果喂回让 LLM 总结)
  ——v1 回执消息即终态,续跑随 UX 深化评估,不影响本 spec。
- O3:tools 注入体积随工具数增长的 token 成本——multi-model-routing
  域落地后按用量数据评估裁剪策略。

## R1 评审修复记录(双路独立评审,全部清零)

**web 路(2 必修+3 建议全修)**:D1 审计页结束日期 off-by-one(end 排他
上界改传次日零点);D2 km.agent_mode 登出/会话终结三路径清态(auth-store
全接线);D3 pending_tool_calls 消费 `?? []` 兜底(版本错位缺键不炸);
D4 确认卡按 ToolConfirmOut.status 行内反馈(过期/权限变化即时可见);
D5(审计页 user/tenant 筛选 UI)按 015 先例转挂账(接口已备)。
**api/agent 路(3 必修+6 建议全修)**:M1 sys_tool_audit/sys_announcement
主键 uuid4→uuid7(ADR-004 铁律);M2 他人确认被拒落 REJECTED 审计
(CONFIRM_FORBIDDEN,尝试者维度新行不触原挂起行);M3 确认时 L1 租户
绑定复校补齐(挂起后解绑租户 403)+spec 复校枚举精确化;S1 重复确认
幂等回放返回首次执行结果(幂等键反查);S2 参数语义预检首关键字白名单
→sqlglot AST 根判定(注释前缀合法 SELECT 不再误拒);S3 EXECUTED 行
清空挂起期 error_reason;S4 流式 GraphRecursionError 与同步同口径收敛;
S5 sys_tool_audit 补 trace_id 部分索引(PENDING_CONFIRM 聚合路径);
S6 轮次上限判定前移到 dispatch 后路由(省一次必被拦的空转 LLM 调用,
finalize 上限判定信号同步改为 rounds+final_answer)。
**R1 直证测试 +5**:他人确认审计/解绑租户 403/幂等回放结果/注释前缀
放行/真并发双确认(gather,公告恰 1 行)。
**观察挂账**:O1 未注册工具先于权限判定(探测 oracle,内部令牌收口暴露
面可忽略);O2 观察截断产生非法 JSON 喂回(LLM 鲁棒,实测观察);O3
args_json 原文未脱敏(当前唯一确认工具 schema 无敏感键,未来带 secret
参数的写工具需重估);O4 确认回执可落本人任意会话(UX 边角);O5
confirm 路径 dispatch 写死 default 数据源(SQL 类工具不经确认路径,
未来引入需重估);URL 漂移观察项(冒烟期一次,未复现)。
