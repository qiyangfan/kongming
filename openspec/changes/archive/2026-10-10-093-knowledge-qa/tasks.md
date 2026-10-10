# Tasks

## 1. 三态意图路由(agent 面)

- [x] 1.1 route_intent 节点:检索前 LLM 三态判定(数据/知识/融合)+置信,
  超时 3s/失败/低置信(<0.7)回退既有规则词表;路由结论与判定依据入
  trace,回退计数入可观测;节点单测(三态判定、回退路径、trace 落痕)过
- [x] 1.2 路由 LLM 接线:经 llm.py 走 multi-model-routing 小模型档;
  每次图执行路由一次(「沉淀缓存命中跳过路由」前提勘误:km:ragctx 是
  retrieve 节点内检索上下文缓存,无整答短路层,见 design 风险表勘误);
  配置缺省值单测过
- [x] 1.3 路由 prompt:零样本指令约束(三态定义+示例问句),误判率验收
  =路由用例集(含长尾知识问句、融合问句、纯数据问句)判定准确率 ≥90%

## 2. 融合问答路径(agent 面)

- [x] 2.1 fused_answer 节点:SQL 链路收口后取 retrieve 阶段文档父块命中,
  一次生成融合正文(数据结论+口径引用键);节点单测(双命中融合/文档缺
  失退化纯数据答/SQL 非 SUCCESS 按数据态收口不引用)过
- [x] 2.2 引用净化与投影复用:068 净化器对融合正文生效(合法键=fused
  下发父块集),sources 单向投影;净化与投影单测(编造键降级、一一对应)
  过
- [x] 2.3 失败兜底:fused_answer 生成失败回退纯数据答正文(结果面不丢),
  计入可观测;兜底单测过

## 3. 契约与 web

- [x] 3.1 chat 域 sources 语义放宽:纯数据道恒为空、融合道投影可非空;
  OpenAPI 描述同步,`make contracts` 重生成前端类型并提交产物
- [x] 3.2 web 并存渲染验证:融合消息(SQL 结果面 + 正文引用 + sources)
  三态×亮暗两态走查不破版;现有组件零新增

## 4. 零回归与门禁

- [x] 4.1 数据态零回归:既有 8 节点路径 e2e 断言数据态问句行为逐字不变
  (路由=data 直通);知识态既有 doc_answer 用例全过
- [x] 4.2 全量验证:`make check` 全绿(agent/api ruff+mypy+pytest、web
  tsc+test+eslint、contracts-check);knowledge-qa/document-rag/chat delta
  经 `openspec validate --strict` 过
- [x] 4.3 真实环境冒烟:起栈跑三态各 ≥2 问句(数据/知识/融合),融合问句
  验证「取数+口径引用+sources 并存」;结果记入 HANDOFF

## Workflow follow-up

- 归档走 /opsx:archive:specs delta 同步主 specs(knowledge-qa 新增、
  document-rag「知识分流」与 chat「文档道回答的结构化来源」整块替换)
- roadmap 第四梯队问答行与 ADR-010 载体列更新为已落地;融合答纳入 092
  judge 评审对象(answer 类)的口径在 092 实施期核对
