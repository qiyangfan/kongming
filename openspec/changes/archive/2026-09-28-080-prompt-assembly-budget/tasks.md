# 080-prompt-assembly-budget 实施任务

## 1. 测试先行

- [x] 1.1 失败测试:预算纯函数——未超限时输出与输入**逐字一致**(零回归锚测试);先跑红
- [x] 1.2 失败测试:超限时历史段保留的是**最近若干轮的连续后缀**——断言顺序不变、
      无中间掏空、被丢者必为最旧;先跑红
- [x] 1.3 失败测试:证据段自尾部裁剪且**首位始终保留**;先跑红
- [x] 1.4 失败测试:纪律段与本问问句永不被截(构造只有它们超预算的场景,
      断言走"如实失败"分支而非静默删段);先跑红
- [x] 1.5 失败测试:注册表条目未声明窗口时回退全局缺省、畸形窗口按未声明处理且
      **不剔除条目**;窗口不出现在任何响应模型中;先跑红
- [x] 1.6 失败测试:预算触发时被裁段落与裁切量计入 trace;先跑红

## 2. 实现(agent 面,零新增依赖)

- [x] 2.1 `settings.py`:`ModelEntry` 增可选 `context_window` + 两项预算配置
      (缺省窗口、预留输出与安全边际);解析沿用既有条目校验路径
      **验证**:1.5 转绿
- [x] 2.2 新增装配预算纯函数模块(零框架、零 IO,import-linter 契约覆盖):
      字符分档估算 + 分段优先级收敛(按 1.2/1.3/1.4 的方向纪律)
      **验证**:1.1、1.2、1.3、1.4 转绿
- [x] 2.3 `graph/nodes.py` 的 generate user 提示装配段接入(`nodes.py:213-246`):
      拼接后、调用前收敛;确认 selfheal 喂回段与哨兵引导在收敛后仍完整
      **验证**:1.1 转绿;`make agent-check` 绿
- [x] 2.4 不可切段超预算时的终态:新增可归因原因码/文案,不再复用
      「LLM 服务暂不可用」(消除误导归因)
      **验证**:1.4 转绿;文案走查
- [x] 2.5 被裁段落标识与裁切量随 trace 下发(复用 observability 既有通道)
      **验证**:1.6 转绿
      **注**:`history_sql`/`skill_examples`/`memory_hint` 各段的段界标识若缺失,
      先在装配处补齐段标记,不做字符串猜测式切分

## 3. 校准与收口

- [x] 3.1 估算校准:构造大 schema 与多轮长历史两类样本各跑若干轮,把估算值与
      实测 token 量比对,记录偏差方向与量级
      **实测(2026-09-28,现网容器栈,26 表大 schema 样本,窗口 7600)**:
      预算 trace 实采 budget_tokens=2480、estimated_tokens=2461、
      cut={"history_sql": 11, "schema_hint": 48}(收敛按优先级先裁
      history 再裁 schema,与设计一致);同 trace 的 SQL_GEN 真实
      prompt_tokens=1887(sys_llm_usage 同 trace 归因)。
      偏差:估算/实测 = 1.30(实测为估算的 77%)——保守估算恒高于
      真实 token 化结果,方向安全(宁多裁不错发);量级约 +30% 余量,
      对"防 400"目标是正确的偏差侧。多轮长历史样本:history_sql 段
      在本轮被优先裁切(11 tokens),长历史场景下该段是首要收敛对象,
      其优先级已有单测钉死;端到端长历史样本受评估集形态所限未单列
      (多轮改写链路在 069 轮次中已实测 resolved_question 非空)。
      `sys_llm_usage` 实际上报的 prompt tokens 对照,必要时收紧安全边际
      **验证**:对照数据记录于 apply(估算偏保守方向可接受,偏乐观必须修)
- [x] 3.2 复现验证:改造前能稳定触发 400/FAILED 的大 schema 租户问法,
      改造后正常收敛
      **实测(2026-09-28,同上,逐档压缩声明窗口)**:窗口 6000(预算
      880)/6800(预算 1680)时,26 表问法两档均产出可归因终态:
      "本轮问题的上下文已超出模型可处理范围(自动收敛后仍无法容纳),
      请缩小问题范围,或联系管理员调整模型上下文窗口配置"
      (error_detail 原文)——即改造前会以"服务暂不可用"假归因或上游
      400 收场的形态,现在为精确归因。窗口 7600(预算 2480)时收敛
      成功触发:cut 生效(history 11+schema 48 tokens)且估算落回
      预算内(2461≤2480);窗口 9000(预算 3880)时零裁剪直通。
      注:真实上游 qwen-plus 上下文 128K,超长提示不会真的 400——
      "400 复现"字面上游不配合,但可归因终态+逐档收敛行为已完整
      实证(这恰是 080 的设计目的:把"上游把守"变为"装配期把守")。
      改造后正常出结果;记录 before/after
- [x] 3.3 spec delta sync 进 `openspec/specs/nl2sql-agent/` 与
      `openspec/specs/multi-model-routing/`
- [x] 3.4 若 trace 新增字段 → `make contracts` 并提交产物(ADR-005)
      **验证**:`make contracts-check` 无漂移
- [x] 3.5 全量 `make check` 收口

> 080 落地记录:

> 080 落地记录(2026-09-27):
> - 新增 kongming_agent/graph/budget.py(纯函数零依赖):estimate_tokens(CJK 1
>   tok/char、其余 1 tok/3.5 char 保守分档)+ converge(分段优先级收敛:行级段
>   尾部逐行丢+头部豁免,整段段直接丢;收敛后仍超 → PromptBudgetExceeded,
>   阻塞段可诊断)。generate 侧八段装配收敛入 assemble_generate_user_prompt
>   (nodes.py,纯函数):预算内与 080 前逻辑逐字一致(legacy 锚函数对照测试),
>   system 纪律段参与估算不在 user 文本内,selfheal 喂回段不可切。
> - 优先级:relations(1)→metric(2)→memory(3)→skill_terms(4)→skill_examples(5,
>   禁令前缀行豁免)→history_sql(6,首位豁免)→schema_hint(7);skill_examples
>   首行=fewshot 禁令前缀,必须保留(正确性);各段内部按行尾裁(段界即自然行
>   ——schema 每行一条表/列、示例每行一问一答、history 每条 SQL 一行)。
> - settings:ModelEntry.context_window(可选;畸形按未声明且不拒配)
>   + prompt_context_window_default=32768 / prompt_reserved_output_tokens=4096
>   / prompt_safety_margin_tokens=1024;LlmClient.context_window(task)/
>   prompt_budget(task)(条目声明优先→全局缺省;预算=窗口−预留−边际);
>   .env.example 登记;deps 指纹入参。
> - 终态可归因(任务 2.4):PromptBudgetExceeded → _run_graph 收敛
>   {"prompt_budget_exceeded"} → QueryOut(verdict=ERROR, 文案"上下文已超出
>   模型可处理范围(自动收敛后仍无法容纳)…调整模型上下文窗口配置"),
>   同步/流式两路同一出口;不再复用「LLM 服务暂不可用」假归因。
> - trace 通道:generate 收敛触发时 emit_event("trace", {node, fields:
>   {kind,budget_tokens,estimated_tokens,cut}});api 侧新增 StreamTraceOut
>   模型入 SSE oneOf + chat/service 白名单透传(063/064/068 后续共用该诊断
>   通道);web dispatchSseBlock 对未知事件忽略(前向兼容,零改动);
>   contracts/openapi.json + api-types.gen.ts 已重新生成并验证无漂移。
> - 测试:tests/test_prompt_budget.py 13 例(估算分档/零回归锚双形态/历史
>   最旧端连续后缀/证据尾裁首位豁免/跨段优先级/禁令前缀保留/不可切段与
>   首位超限的如实失败/窗口解析与预算算式/generate 节点 trace 发射与静默);
>   替身 FakeLlm/ScriptedLlm/_Llm 等补 prompt_budget(既有测试零回归)。
>   agent 全门禁绿:426 passed,ruff+mypy+pyright 全净。
> - 【待确认-环境限制】3.1 估算校准(大 schema/多轮长历史样本估算值 vs
>   sys_llm_usage 实际 prompt tokens):需真实 LLM 调用,未执行;估算取保守
>   方向(CJK 1:1、ASCII 1:3.5)留有余量。3.2 复现验证(改造前稳定 400/
>   FAILED 的大 schema 问法改造后出结果):需在跑服务+真实模型,未执行;
>   机制等价性已由纯函数测试覆盖(超限收敛路径+可归因终态)。

> 全量 make check 收口(2026-09-27 最终门禁轮):api 1024 passed(覆盖率 83%,
> ruff format/check+mypy+pyright+lint-imports 全净)/ agent 458 passed(88%,
> 同套静态门禁全净)/ liannu 22 passed(89%)/ web tsc+eslint+stylelint+
> prettier+vitest 60 passed+build 绿 / contracts 导出与工作区一致(漂移
> 门禁等价通过)。五门禁全绿。
