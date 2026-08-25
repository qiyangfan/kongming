# 022 agent-memory 设计

## Context

既有:chat 链路 _prepare_query 前段共用;agent /internal/embedding 批量
向量化通道(009);020 MASKERS 脱敏规则集;pgvector 1536 维在 rag 域已用;
BackgroundTasks 异步任务模式(010 标题改写)。约束:零新依赖、prompt
资产系统模板不动、ADR-002(向量与 LLM 提取都经 agent 回调)。

## Goals / Non-Goals

Goals:双路径形成 + 向量召回注入 + dormant + 自管理四端点;零回归。
Non-goals 见 proposal(团队记忆/演化算法/确认交互不做)。

## Decisions

### D1: memory 域包(api)+ user_memory 表(0011)

kongming/memory/(models/ports/service/repository/router)。表:user_memory
(id uuidv7, user_id UUID NOT NULL, tenant_id UUID NOT NULL, content
TEXT NOT NULL, category VARCHAR(32) DEFAULT 'general', confidence
NUMERIC(3,2) NOT NULL, source VARCHAR(16) CHECK(EXPLICIT/IMPLICIT),
embedding vector(1536), last_hit_at TIMESTAMPTZ, dormant BOOLEAN NOT
NULL DEFAULT false, created_at/updated_at;索引 (user_id, tenant_id)+
HNSW(embedding vector_cosine_ops,仅 dormant=false 部分索引))。

### D2: 召回挂接 chat service(MemoryReader 端口)

query/start_stream 在 _prepare_query 后(输入护栏后)调
MemoryReader.recall(user_id, tenant_id, question)→embedding(经 agent
回调,组合根复用 009 通道)→向量 top-3(dormant=false,双过滤)→命中
刷 last_hit_at(同事务);拼 memory_hint 文本段传 agent。任何失败
(embedding 不可达等)降级 None 仅日志——问答主链路零影响。流式同路径。
显式「记住」短路在 recall 之前判定(见 D3),短路时不走 agent。

### D3: 显式形成=规则意图词 + 护栏拒绝

chat service:_explicit_memory_intent(question) 正则
^(记住|请记住|帮我记住|记一下)[:：,，]?\s*(.+)→内容提取(截断 500);
内容过 PII 检测(020 MASKERS 全量)命中→拒绝(答复「敏感信息不记忆」
+operlog);通过→落库(EXPLICIT, confidence=1.0, embedding 异步补——
同步路径 embedding 失败不阻断,向量缺失的记忆仅不参与召回,编辑时补)。
答复=assistant SUCCESS 消息「已记住:…」(复用终态组装,无 SQL)。

### D4: 隐式形成=后台任务 + agent /internal/extract-memory

首个 SUCCESS 轮后(与标题改写同触发点)BackgroundTasks:agent 新端点
/internal/extract-memory(question+answer 摘要)→LLM 判定是否含值得
记忆的用户偏好/事实(全新撰写 prompt,非逐字资产),返回
{memories: [{content, category}]}(≤3 条);api 落库 IMPLICIT
confidence=0.5(过 PII 检测;去重:同 user+tenant 内容完全相同的
不重复落)。失败/空候选静默(后台任务语义)。

### D5: dormant 与命中刷新

dormant 判定不在查询期动态计算,而是落库标志:召回查询
WHERE dormant=false;后台无需定时任务——召回时顺带
UPDATE dormant=true WHERE confidence<0.3 OR last_hit_at < now()-90d
(惰性标记,同一事务);编辑(任何 PATCH)即 dormant=false 唤醒。
召回命中 UPDATE last_hit_at=now()。

### D6: 自管理端点(登录即可,无新权限点)

GET /memory(本人+当前租户头,含 dormant)+ PATCH /memory/{id}
(content/category;编辑即唤醒+重算 embedding)+ DELETE /memory/{id}
+ GET /memory/export(JSON 数组)。归属收口:全部 WHERE id=:id AND
user_id=:me AND tenant_id=:t(404 同形)。operlog:memory.create/
update/delete(015 ip 链路自动覆盖)。

### D7: agent 侧改动

QueryIn 纯增 memory_hint: str = "";generate user 拼接:`关于这位用户的
已知信息:\n{memory_hint}\n\n` 前缀(在 schema 提示之前);react 首轮
user 消息同款前缀(initial_user_prompt 加可选参数)。/internal/
extract-memory 端点(ExtractIn{question, answer}→LLM 提取,温度 0.1,
max_tokens 300)。

## Risks / Trade-offs

- [隐式提取误存(把无关内容当偏好)] → confidence=0.5 低置信+自管理
  页可删;提取 prompt 明示「无值得记忆则空」。
- [向量缺失记忆不可召回(embedding 失败形态)] → 编辑时重算;召回
  对 NULL embedding 天然排除;登记挂账(后台补偿任务随运营)。
- [召回注入增加 prompt 体积] → top-3+单条截断,体积可控。
- [「记住」意图误触发(用户问句含记住二字)] → 正则锚定句首意图词
  +冒号可选;误触发时用户可删;观察挂账。

## Migration Plan

1. 0011 建表(零存量);2. 滚动重启任意序(memory_hint 缺省零回归);
3. 回滚=不注入即老行为,表留库无害。

## Open Questions

- O1:embedding 缺失记忆的后台补偿(定时补算)——随运营诉求。
- O2:隐式记忆质量(误存率)观察——自管理页删除率为信号,随
  personalization 域演化。
