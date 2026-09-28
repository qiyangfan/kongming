# 069-chat-quality-observability 设计

## D1 为什么先做确定性信号,LLM judge 明确划界外

ai-chatbot 的 ANSWER_SCORING 是 RAG 场景的答案:grounding(主张 vs 文档证据)主导,
执行维度只占 0.2 权重。NL2SQL 场景质量信号的排序恰好相反——**SQL 执行成败是主导
轴**(用户看到"未找到数据"就是最直接的质量抱怨),而这些信号 Kongming 已经确定性
持有:verdict 四态、retry_count、审计 verdict、评分 rating。

因此本变更只搬方法论的地基(轮次级信号落库 + 观测切片),不搬公式层。评分公式没有
输入基线时只能拍脑袋——ai-chatbot 权重表文档自证是数十个生产误伤案例校准出来的
(16/57/72/90 分误伤史)。Kongming 先用确定性切片回答"质量差在哪",积累自有 badcase
后再议 judge(再进入条件已写进 Non-goals,防范围蔓延)。

## D2 为什么落 chat_message 四列,而不是新表 / ES / outbox

三个候选都判否:

- **新表**(如 sys_turn_quality):轮次本体就在 chat_message,质量信号与它一对一,
  拆表只会在每次查询时多一次 join,且引入第二写入口。
- **ES**:单 PG 解决不了的问题才配 ES;引入新集群 = 新 moving parts,直接违反
  tech-stack 宪法否决清单精神,且观测查询隔离已由 observability 域既有边界
  (只读聚合、故障不波及主链路)保证。
- **outbox + 局部投影**:ai-chatbot 用它是为了解决"多写者异步乱序 + ES 最终一致"
  (TURN/REVIEW/SCORE/DEBUG 四来源版本守卫)。Kongming 同库同行同步写,评分回填时
  对同一行 update 即可,没有乱序问题——搬版本机是给不存在的问题造轮子。

代价如实记录:chat_message 变宽 4 列;存量行 null/0,零回算(历史质量信号本就未采集,
伪造历史比没有更糟)。

## D3 trace_id 列的双重身份

`chat_message` 今天没有 trace_id,而三张观测表(sys_llm_usage/sys_sql_audit/
sys_tool_audit)都以 trace_id 为关联键。补上它同时解决两件事:

1. **聚合 join**:质量汇总的"平均 token / selfheal 伴随的 LLM 调用"依赖
   chat_message ⋈ sys_llm_usage on trace_id;
2. **管理台跳转**:明细分页每行带 trace_id,可直接打开既有 trace 时间线抽屉
   (llmusage/timeline)——观测面三页(用量/反馈/质量)由此互通,而不是三个孤岛。

## D4 prompt_version 的语义与成本边界

定义:**agent 启动时对自身 prompt 资产集合(graph/prompts.py、react/prompts.py、
doc_prompt.py、extract_prompt.py 的模板常量)算一次稳定 hash,取前 8 位,随每轮
QueryOut 携带**。理由:

- prompt 资产是代码常量,不存在运行期动态拼接影响版本身份的部分(数据部分——
  schema/知识——本来就该变化,不进指纹);
- 启动期一次计算,零 per-turn 成本;
- 只存 hash 不存原文,避免把 prompt 全文引入观测面的脱敏/泄露面。

它回答的问题精确为:**"这个轮次跑的哪版 prompt"**——质量切片按 prompt 版本对比,
065 的门禁结论也能按版本对齐线上形态。

## D5 resolved_question 的落库时机

仅当多轮指代消解**实际发生**(history 非空且改写结果与原问题不同)时非空,单轮恒
null。两个理由:单轮是绝大多数轮次,全量写等于把原问题复制一遍(存储与查询噪声);
"有值即可疑可查"的语义让运维看到非空就知道这轮发生过改写,调试时直接命中。

content 安全面:它是用户问题的 LLM 改写产物,内容域与 `chat_message.question`
(已落库的用户原话)同级,管理面另有 `system:chat-quality:view` 权限门禁,不构成新
泄露面。

## D6 聚合查询的索引与口径

汇总查询形态:`chat_message m JOIN chat_session s ON m.session_id = s.id`,
按 `m.created_at` 范围过滤(租户经 s.tenant_id、数据源经 s.datasource_key 收敛),
再按需与审计/用量表 trace_id join。chat_message 现有索引只有
`(session_id, created_at)`,范围扫描无索引可用——迁移补
`idx_chat_message_created (created_at)`。三张观测表的 `(trace_id, created_at)`
复合索引(0026)已满足 join。

趋势口径与用量趋势逐字对齐:Asia/Shanghai 日历日、`day_series LEFT JOIN` 谓词放
ON 内逐日补零(0025/035 先例,防 WHERE 滤掉补零桶)。

## D7 观测面边界(与 observability 域既定约定一致)

- **只读**:两个端点零写路径,任何故障不波及采集与主链路(observability spec
  「观测查询与采集隔离」Scenario 同款保持)。
- **无用户行级切分**:按租户 + 数据源 + 时间切片;与 observability 域
  「观测面数据边界」既定立约一致——不把用量/质量观测变成按用户问责工具。
- **权限单点**:`system:chat-quality:view`,SUPER_ADMIN 种子覆盖;403 形态与
  llmusage 端点一致(权限拒绝落审计,沿用 rbac 域既有行为)。

## D8 为什么不动用户响应与 SSE 契约

四字段是**管理面诊断信号**。agent→api 内部 DTO(QueryOut)纯增两字段在进程内消化,
api 不向前端转发;用户响应与 SSE result 事件的字段集合逐字节不变——query-streaming
spec「result 事件 data SHALL 与同步问答响应逐字段一致」契约不破,因此**不需要
query-streaming 域 delta**,`contracts/openapi.json` 的用户面 schema 零变化(新增的
只有两个管理端点的 path/schema)。

## D9 前端范式:复用不发明

新页照搬 UsagePage/FeedbackPage 既有范式:PageHeader + StatCard 总览 + EChart 趋势
+ DataView(双形态标准件)+ 明细分页 + 筛选;懒加载理由同款(图表库体积)。明确不做的:
不造新的筛选状态机写法(本仓 draft/committed 惯例已在宪法国覆盖下),不造第二个图表
配置样板(图表 option 构造走 `lib/echarts.ts` 单源)。

## D10 明细行动作:复制问题 + 跳转知识库(原 068-turn-status 并入)

NO_DATA 明细是知识补全闭环最直接的输入——哪些问题该补表说明、补文档。行行动作只需两条:
「复制问题」(clipboard,把未覆盖问句带去补知识)与「跳转知识库管理页」(/admin/rag)。
不建「一键转知识」写动作:知识域写端点与内容审核流属另一能力域,观测页保持零写操作。
四态语义不扩:直接消费 044 既有 SUCCESS/REJECTED/NO_DATA/FAILED,不预埋「澄清」「闲聊」等细分终态。
