# Design: add-fallback-taxonomy-clarify

## Context

复语直通现态:`understand` 节点头部零模型直通判定(`nodes.py` 闭合词表
全等),命中返回 `end_reason=no_data` + `_NO_DATA_ERROR`——api 侧
`_finalize_reply` 把 verdict=NO_DATA 的 `error_raw` 提为 summary,于是
招呼语得到数据缺失文案,且计入 no_data 率。意图路由:`route_intent`
节点对每个正常问句发一次小模型 JSON 调用(`{intent, confidence,
reason}`,已有解析纯函数 + 低置信回退词表),位于 understand 之后、
retrieve 之前——是零成本注入澄清判定的位置。诊断字段先例:
`AgentReply.resolved_question / prompt_version`(仅落库不进响应),
请求载荷已有可选键先例(memory_hint)。

## Goals / Non-Goals

- Goal:招呼语得到得体的零模型应答;高置信歧义轮在烧检索/生成/执行
  预算前被澄清;兜底轮在观测面与真实未覆盖轮分离;判定失效零回归。
- Non-Goals:见 proposal(ReAct、二次澄清、语义拒答检测、话术池、
  新状态值、词表扩充)。

## Decisions

### D1 兜底轮复用 NO_DATA 中性通道,不新增 verdict/status 取值

呈现语义恰好一致:中性说明形态(非错误横幅、非结果卡)、无产物卡、
反馈可用——正是澄清问句与招呼应答想要的用户面。新增 verdict/status
取值要涟漪三条契约(nl2sql 映射场景、chat 四态 requirement、web 徽标),
而收益(语义纯正)由 fallback_type 独立承载完全覆盖——观测面剔除兜底
轮后,指标语义已修正;wire 上的 NO_DATA 用户永不可见。**与 ai-chatbot
的收口先例同构**(追问轮保持 success、分类由独立 fallbackType 承载,
避免 done 语义涟漪)。

### D2 澄清判定注入 route_intent 帧,SHALL NOT 动 understand 模板

- 零新增每轮模型调用(澄清判定搭意图路由的既有调用),延迟零增加;
- route_intent 已是 JSON 输出+解析纯函数+低置信回退,扩展
  `{intent(含第四值 chitchat), ambiguous, clarify_question}` 是
  同款工程;
- understand 模板不动:`prompts.py` 明示任一模板变更即改 prompt 指纹、
  历史评估基线不可比——INTENT_ROUTE 指纹同样会变,但该帧输出只影响
  答法不影响 SQL 生成,apply 期跑 eval 全层确认并以新指纹重建基线
  (tasks 列明)。
- 判定输入=resolved_question(指代消解后自包含),历史可补全的问句
  天然不命中——「历史可补全不澄清」是输入口径保证,不是额外规则。

### D3 防循环:请求载荷带 last_fallback,判定侧硬门

state/history 只有问句文本,图内无法可靠得知「上一轮是澄清轮」;
api 侧落库时已知 `fallback_type`,组装 history 时顺手携带
`last_fallback`(可选诊断键,缺省不带=零兼容风险)。route_intent
判定命中前检查:`last_fallback == CLARIFY` → 跳过澄清判定。确定性
硬门,不依赖模型自觉;非澄清轮(含 CHITCHAT)不设门(复语直通在
understand 头部先于路由,两者不打架)。

### D4 CHITCHAT 文案:双路径,全静态零 LLM

直通路径:否定终止/应答致谢/招呼三族各一条静态文案,置于词表旁
(同文件同评审视线),词表成员标注族别(frozenset → 族别映射)。
语义路径:统一中性文案一条(不要求模型输出族别——漏网闲聊的族别
区分没有观测与体验价值,少一个帧字段就少一分解析歧义)。两条路径
命中时 `error` 承载文案(复用 NO_DATA 组装通道 summary=error_raw),
state 加 `fallback_type` 随 QueryOut 回传;直通计数照旧,语义命中
另计一条计数(词表宽严与判定阈值的独立调参依据)。

### D5 存储:一列 + CHECK,无索引

`ALTER TABLE chat_message ADD COLUMN fallback_type VARCHAR(16)`
+ `CHECK (fallback_type IN ('CHITCHAT','CLARIFY'))`,nullable(非
兜底轮为 NULL,与「诊断字段不进用户契约」同款)。不建索引:筛选
发生在租户+时间窗收敛后的明细页(百行级扫描),索引是过早优化。
迁移遵循 ADR-004(状态列 VARCHAR+CHECK 纪律;非状态列亦从同款)。

### D6 观测口径:no_data 率剔除、计数单列

`overview` 增 `chitchat_count` / `clarify_count`;
`no_data_rate` 分子改为 `status='NO_DATA' AND fallback_type IS NULL`,
分母同步剔除(兜底轮不是「没找到数据」,不该出现在未覆盖率的任何
一侧);`count(no_data)` 原始口径保留(如实)。page 行加
`fallback_type` 与筛选参数(`fallback=chitchat|clarify|none`)。
summary/page 响应模型扩字段 → `make contracts`(ADR-005)。

### D7 闲聊语义层:搭 route_intent 帧,不建独立预检(备选比较)

词表全等的长尾缺口(「太感谢了」「在吗」漏网→烧全链+数据缺失文案)
需要一个语义层。备选与裁决:

- **独立小模型/编码器分类器预检(否决)**:新运行时依赖(部署/版本/
  评估集全要养)+ 中文闲聊标注语料负担 + 串行预检延迟(每轮 +50~
  200ms)+ 误拦真实问句的最坏风险。而 route_intent 本就对每个非词表
  问句运行——搭帧的边际成本是零。存在性证明:ai-chatbot 的 v12
  理解帧即单帧多任务(意图+情绪+话题切换+复合分解)且生产验证。
- **embedding 相似度匹配(否决)**:词表立约禁语义相似的原因
  (「取消订单」类短真实问法误拦)embedding 同病,还要求 agent 面新增
  出站依赖——每一项都更差。
- **扩大词表(否决)**:长尾无穷,补丁式追词表不消除根因。
- **结论=两层**:直通词表(精确层:零成本短路保留,「取消」这类
  确定性应答必须即时且永不误判)+ 意图路由帧第四意图值(语义层:
  漏网兜底)。互斥与优先级:解析收口时 `intent==chitchat` 优先于
  ambiguous(无取数意图的输入无从澄清口径),确定性规则不依赖模型
  自觉;误拦面收敛到「高置信且完全无取数意图」,低置信 fail-open
  照常链路。

## Risks / Trade-offs

- [澄清判定误命中打断可回答问题] 三重收敛:命中口径要求「任一解读
  都实质改变结果」+ 高置信阈值 + fail-open 兜底;误打断可点踩,
  分解可见(fallback_type 筛选即得全部澄清轮,调阈值有数据依据)。
- [闲聊语义判定误拦真实问句] 判定口径要求「完全无取数意图」+ 高置信
  阈值 + fail-open;误判表现是中性文案回应(用户重述即恢复),代价
  远小于误跑 SQL 链的静默错答;语义命中单列计数,阈值调优有数据依据。
- [INTENT_ROUTE 指纹变化致基线断裂] apply 期重跑 eval 并重建基线;
  该帧为小模型短输出,回归风险集中于路由回退率(直通计数/路由计数
  可对照)。
- [直通轮仍有 status=NO_DATA 的 wire 形态] 有意的最小涟漪选择(D1);
  观测语义已由分类字段修正,web 零改动。

## Migration Plan

一列迁移(nullable,down 精确逆);零开关——fail-open 结构保证判定
失效时行为与现态一致,不加临时开关(根因消除优先于开关兜底)。
存量行 fallback_type 为 NULL,观测口径按「无分类」参与。

## Open Questions

(无——注入点/防循环/存储/口径均已收口;文案具体措辞属实现期,
spec 只锚定族别与「零模型、非数据缺失」语义。)
