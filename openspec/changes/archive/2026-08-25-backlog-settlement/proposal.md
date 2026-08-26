# 提案:backlog-settlement(挂账沉淀清偿包)

## Why

001–038 全部归档后,沉淀了两层挂账:038 变更九项 + 019–037 全量审查循环十七项,
外加各域历史挂账。本变更是「挂账沉淀评估」的产物:对全部挂账逐项代码级核实
(含是否已被后续变更顺带清偿)后分拣裁决,把其中**实锤缺陷 + 低成本高价值**的
15 项集中清偿;其余项归位(随域/接受/远期),裁决结果沉淀进 HANDOFF。

本变更与 036 cross-domain-debt-clearance 同模式(跨域清偿包),但来源不同:
036 清偿的是 029–033 各域 R1 评审挂账,本次清偿的是 038 九项+全量审查循环
挂账+历史域挂账中经核实仍存活的缺陷项,共 18 项;其余项终态处置(明确
关闭/条件触发登记/移入产品登记),目标是**开放挂账清零**。

## What Changes

经两路代码级核查(逐项文件:行号定位),清偿项分三组:

### A 组:安全/正确性实锤(7 项)

1. **/mcp 入站 body 无闸**(审查 B1-B11):`POST /mcp` 未认证即可灌任意大
   JSON(FastAPI 全量缓冲后才进处理;notifications 短路 202 在鉴权之前)。
   照 trigger webhook 范式补 Content-Length 声明值+实测双闸(1MB),
   **前移到鉴权与 202 短路之前**。
2. **mark_dormant 跨租户全表 UPDATE**(审查 B1-B2):任一用户走一次召回
   即全局翻牌 dormant,与 memory repository 文件头「所有读写强制
   user_id+tenant_id 双过滤」铁律自相矛盾。UPDATE 补双过滤,语义收窄为
   「本用户本租户惰性休眠」。
3. **workflow 引擎超时来源混同**(审查 B2-B5):`except TimeoutError` 无法
   区分「wait_for 全局预算到期」与「节点内源超时(socket/asyncio)」,
   内源超时被误归 TIMEOUT「超全局预算中断」,误导排障。按 task.cancelled()
   区分:真预算超时=TIMEOUT,内源超时=FAILED 带明确文案。
4. **_should_skip 条件门过宽**(审查 B2-B6):判据是「依赖 outputs 有
   bool result 键」而非「依赖是 condition 节点」(on 值定义期校验系
   029 R1-J4 既有行为,非本次缺口)。收紧为仅 condition 类型依赖应用
   条件门;spec 补立约 on 校验条款(既有行为入约)。
5. **ReAct 轮次触顶丢部分结果**(审查 A-B6):轮次上限触顶时 finalize
   只返回 error,早前轮次已成功的 execution/rows 全丢。触顶终态保留
   last_execution(部分结果)+触顶提示;tool-calling spec 补立约。
6. **doc_name 不过 PII**(审查 doc_name·PII 项):上传文档名原样入库/
   回显/进 LLM 提示词,含身份证号的文件名(「王五_110101…_简历.docx」)
   全程在护栏之外。上传期 doc_name 命中 PII 规则即 400 拒绝
   (拒绝优于脱敏:脱敏后名可能撞 uq_documents_tdq 唯一键)。
7. **bot usage sink 复用请求会话**(审查 B1-B9):请求级会话横跨 agent
   分钟级长外呼持有连接池配额,sink 自 commit 又会提前提交会话上挂起写
   (当前侥幸无其他挂起写)。改独立短会话(同文件 _BotGuardrailAdapter
   已是正确范式)。

### B 组:资源/一致性/测试(5 项)

8. **LLM 注册表池每请求全量重建且不 aclose**(021①):单次 /query 图执行
   重建 4–5 套完整 client 池(每套 N+2 个 AsyncOpenAI),旧池不 aclose
   连接关闭时机交给 GC。按 settings 指纹+事件循环维度缓存,指纹变更时
   逐 client aclose 后重建。
9. **_tenant_id 五份复制**(审查 B1-B10,登记四份实际五份):同一
   「X-Tenant-Id 白名单校验+default 优先/最早授予/t.id tiebreak」SQL
   逐字复制在 chat/feedback repository 与 memory/semantic/bot router。
   收口单一实现(委托 ChatRepository 既有范式),5 处替换。
10. **收集器早返丢记录三处**(035⑧+038⑨ 同族):agent /query_react 的
    AttachmentError 早返与 /query_react/stream 的 LlmError/AttachmentError
    分支均不携带 usage_records,已收集记录整丢(当前 bot 链路不传附件/
    流式 collect_usage 恒 False,属潜伏缺陷,bot 附件/流式 bot 落地即
    显性化)。三处统一携带 + agent 图内真实收集端到端测试补齐
    (审查 A-B9:fake LLM 带 usage 跑穿 ReAct 图,断言 usage_records
    非空+seq 递增,现有测试替身无 usage 属性恒空列表)。
11. **语义层单表自引用 FK 不注入**(023①):introspection SQL 本身支持
    自引用,但 enrich 侧双 guard(tables>=2)拦截,单表召回时
    `goods.parent_id = goods.id` 形态永不进提示。guard 放宽为非空
    (SQL 的 ANY(:ts) 天然收窄,多表行为不变)。
12. **billing by_model calls 含 ERROR 行**(038⑥):month_usage_by_model
    的 count(*) 不滤 status,账单 usage_json 里 by_model.calls 口径虚高。
    改 `count(*) FILTER (WHERE status='SUCCESS')`(tokens 同口径对齐)。

### C 组:可观测/审计体验补全(3 项)

13. **用量页租户筛选**(038③):api page 端点+repo+web 三面补租户筛选
    (经 u.tenant_id 直滤,该租户全部行含 unknown 展示行均返回);
    契约重生成。
14. **审计页结束日期 off-by-one**(019⑦/015⑨):结束日当天记录全被排除
    (end_time 取当日 T00:00:00Z,后端排他语义)。ToolsPage 已修形态
    (endExclusive)抽共享 util,AuditPage 两处替换。
15. **审计页租户筛选下拉+防抖公共 hook**(019⑥ 部分/038⑦):api
    /audit/sql/page 的 tenant_id 参数已备(spec 已立约),web 补
    tenant Select;useDebounced 从 UsagePage 局部实现抽公共 hook
    (此时出现跨页消费语义,触发登记条件)。

### D 组:深挖清偿(收敛指令追加,原判「随域延后」三项收编)

16. **循环子节点归因粒度**(审查 B2-B4):forEach 循环子节点执行状态
    从不进 run 快照,失败只归因到 loop 节点——快照 JSONB 逐次记入
    带`<loop>.<i>.<sub>` 迭代标记的 NodeRun 行,失败子节点按自身
    id 归因;零 schema/契约变更(快照 JSONB 内结构)。
17. **workflow run 详情 outputs 端点**(029⑤):列表剥 outputs 防膨胀
    后无处查看完整节点输出(排障刚需)。补 GET run 详情端点(完整
    快照含 outputs,可见性与列表同口径)+web 详情 Dialog。
18. **全站查询错误态统一渲染**(038⑧):15+ 存量页面查询失败静默
    空白/伪装空态。抽 QueryErrorAlert 共享组件(前端铁律:≥2 处必
    抽组件)批量统一全站,新页面消费同一组件。

## 不做的事与终态处置(Non-goals)

本变更的挂账治理目标:**完成后开放挂账=零**。全部原挂账按四类终态
处置,不再存在无触发条件的「随域延后」形态:

**明确关闭(结案,不再挂)**:
- **A-B10 confirm_required 硬熔断**:api write_burst 写闸已实证兜底
  (每 trace 最多 1 行 PENDING_CONFIRM,第 2 次起 REJECTED),残留仅
  LLM 不听指令空烧轮次至 8 轮上限的 token 成本,无状态堆积无越权写;
  加硬熔断状态位的复杂度不值。结案。
- **B2-B9 http 响应头上限**:已实证 h11 默认 16KB 头块上限兜底
  (DEFAULT_MAX_INCOMPLETE_EVENT_SIZE,超限抛协议异常收敛 FAILED);
  本包在 http_runner 补注释固化该依赖声明与 HTTP/2 禁用前提。结案。
- **审计页 user 筛选 UI**:API user_id 参数已可用(按 uuid 精确匹配),
  按人排查场景低频;补挂 audit:view 的轻量 user 简列端点引入用户名
  枚举面,收益不值。结案(能力已备,UI 不做)。
- **D-B7 进度条动态 style**:无静态等价,原审查已裁「接受」。结案。

**条件触发登记(不再是开放债务,触发即评估)**:
- B1-B3 注入拆字/全角变体绕过(020③/022⑤ 同族):触发=出现真实
  绕过事件或规模化信号;护栏非唯一防线(SQL 防火墙+工具六层在后)。
- B2-B1 支付回调先查单后验签探测面:触发=接入真实公网支付通道。
- 028① 远端错误文案反射面收紧:触发=MCP 生态出现实际滥用信号。
- 多 worker 部署三项(008② 池失效广播/015⑧ 归档 advisory lock/
  031② 调度互斥):触发=实际多 worker 部署。

**移入 roadmap 产品登记(是产品待办不是债务)**:
- STT/OCR 选型(030①②)、真实支付通道 Channel 实现(035①)、
  可视化拖拽画布(029①)、MCP resources/prompts 原语(028⑤)、
  Bot 版本化上架(033②)、个性化推荐(033③)、用户/TEAM 维度
  配额(034③)、失败重试策略(031①)、更多事件源(031③)、
  zhparser 中文全文(009⑫/024①)等——随产品路线评估,不再以
  挂账形态存在。

**已销账(被后续变更清偿,确认关闭)**:
仓库根截图残留(gitignore 已覆盖)、021②(已提交)、021⑤(038
看板)、034①②④⑥⑦⑧(035 清偿)、030④⑤(036)、031⑤⑥⑦(036)、
032⑥⑧(036)、033⑥(审查循环)、005 摘要幻觉(018)、009③(016)、
009⑦⑪(014)、009⑭⑮(013)、010③⑤⑰(013)、011⑤⑨(013)、
035⑥(prod 密钥 fail-closed 拒启,审查循环)。

## 涉约与影响面

- **ADR-005 契约**:llm-usage page 端点纯增可选参数 tenant_id →
  make contracts 重生成。
- **零表结构变更、零新依赖、零新权限点**。
- spec delta:tool-calling(轮次触顶终态携带部分结果)、mcp-interop
  (入站 body 闸)、agent-memory(dormant 收窄语义)、workflow-orchestration
  (超时分类+条件门收紧)、rag/knowledge(doc_name PII 拒绝)、
  observability(page 租户筛选)、multi-model-routing(池缓存为实现
  细节不入约)、custom-agent(bot sink 会话语义为实现细节不入约)。
- 受控行为变更处同步对齐既有测试断言(038 教训:billing FILTER 会改
  calls 断言、doc_name 拒绝需防误伤既有上传测试文件名)。
