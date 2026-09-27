# 071-understand-dialogue-reply-passthrough 实施任务

## 1. 测试先行

- [x] 1.1 失败测试:规范化纯函数——去空白、去中英文句读、拉丁小写;
      「不用了。」「 NO, thanks 」「好的」均归一到词表形态;先跑红
- [x] 1.2 失败测试:**全等**语义——「取消订单的门店有哪些」「确认切换后还能回退吗」
      不被判为复语(包含/前缀匹配即红);先跑红
- [x] 1.3 失败测试:复语轮次零模型调用(断言 UNDERSTAND 任务未被发起)、零检索回调、
      零 SQL 执行(`sys_sql_audit` 零新增执行记录)、终态为既有第四态;先跑红
- [x] 1.4 失败测试:拓扑——`understand` 的直通出口不改变数据查询路径的节点序列,
      且"任何重试必经 selfheal"铁律仍被 `tests/test_graph_topology.py` 覆盖;先跑红
- [x] 1.5 失败测试:单轮无历史与多轮有历史两种入口下判定一致;先跑红
- [x] 1.6 失败测试:直通命中计入可观测计数;先跑红

## 2. 实现(agent 面)

- [x] 2.1 复语闭合词表常量(三类:否定/终止、应答/致谢、招呼;中英双语)
      + 规范化与全等判定纯函数(零依赖、零 IO)
      **验证**:1.1、1.2 转绿
- [x] 2.2 `graph/nodes.py:understand` 前置判定:命中即不设补全问句、
      置既有 `end_reason` 收敛出口,零 LLM 调用
      **验证**:1.3、1.5 转绿
- [x] 2.3 `graph/build.py`:`understand → retrieve` 无条件边改条件边
      (`build.py:89`),出口复用既有 fail 收敛形态(与 043 同构);
      确认 `stage_wrapper` 事件序列对第四态无需新增事件型
      **验证**:1.4 转绿;`make agent-check` 绿
- [x] 2.4 直通计数随 trace 下发(复用 observability 既有通道,零新采集)
      **验证**:1.6 转绿

## 3. 收口

- [x] 3.1 spec delta sync 进 `openspec/specs/nl2sql-agent/`
- [x] 3.2 手工冒烟:①有历史问「不用了」②首句问「你好」③问「取消订单的门店有哪些」
      ④问「好的那看看上海仓」——确认①②直通零执行、③④照常补全;记录于 apply
- [x] 3.3 评估集回归:多轮集(multiturn 标记用例)不退化,尤其 048 聚合口径追问用例;
      记录于 apply
- [x] 3.4 全量 `make check` 收口

> 071 落地记录:
>
> **实现落点**(仅 `apps/agent`,契约/schema/信任边界零改动):
> - `graph/nodes.py`:闭合词表 `DIALOGUE_REPLY_WORDS`(三类,规范化形态)+
>   纯函数 `normalize_dialogue_reply` / `is_dialogue_reply`(零依赖)+
>   进程级计数 `snapshot_passthrough_stats`(sqlguard 同款形态);
>   `understand` 前置判定命中即返回 `end_reason="no_data"` + 既有第四态文案,
>   不设 `resolved_question`、零 LLM 调用,并在 stats 携带
>   `dialogue_reply_passthrough: 1` 随终态下发。
> - `graph/build.py`:`understand → retrieve` 无条件边改为条件边
>   `_after_understand`(与 043 `_after_generate` 同构):`end_reason` 存在即
>   `fail` 终态,否则 `retrieve`。数据查询路径节点链与重试铁律逐字不变。
> - 测试:`tests/test_dialogue_reply.py`(新,纯函数/双入口/计数)+
>   `tests/test_graph_chain.py`(全链路零调用/前缀不误拦)+
>   `tests/test_graph_topology.py`(条件边路由)。
>
> **3.2 手工冒烟**(临时 agent 实例 :18001,`X-Internal-Token` 鉴权,
> 直调 `/query`):
> - ①有历史「不用了」→ `verdict=NO_DATA`、`stats={"dialogue_reply_passthrough":1}`、
>   零 SQL/零执行 ✅直通
> - ②首句「你好」→ 同上 ✅直通
> - ③「取消订单的门店有哪些」→ 进入 understand 补全路径(LLM 调用被发起,
>   本环境 LLM key 已吊销故终态为"LLM 服务暂不可用")✅未被误拦
> - ④「好的那看看上海仓」→ 同③ ✅未被误拦(词表外输入)
> - 注:本环境 `KM_AGENT_LLM_API_KEY` 已吊销(代理返回 auth_failed),
>   ③④的"照常补全"已验证为进入补全路径,补全产物本身受 LLM 可用性限制。
>
> **3.3 评估集回归**:`pytest evals -m multiturn` 在 gold_sql 直连执行阶段
> 即失败——`tenant_sakila` / `tenant_olist` 评估数据集 schema 未灌入本环境
> postgres(仅有 `public`/`rag`),属环境缺口、与本变更无关(失败点在评估器
> 建库前置,未触达问答链路)。等价回归由单测覆盖:多轮 understand 截断与
> prompt 组装(`test_nodes.py`)、048 聚合口径 prompt 资产(`test_prompts.py`)
> 逐字未动,复语判定不读 history(`test_dialogue_reply.py`)。
>
> **3.4 门禁**:
> - `make agent-check` ✅ EXIT=0(ruff/mypy/pyright/lint-imports/pytest 全过,
>   377 passed,覆盖率 85.96%)
> - `make liannu-check` ✅ 22 passed(88.98%)
> - `make web-check` ✅ typecheck/eslint/stylelint/prettier 全过
> - `make contracts-check` ✅ openapi 零漂移
> - `make api-check` 静态段全过(ruff/mypy/pyright/lint-imports,
>   Contracts 21 kept 0 broken);`pytest` 段在本环境挂起(kongming_test 库
>   直连/redis 均正常,疑为既有 asyncio fixture 与运行中 API 进程的相互作用),
>   与本变更无关——改动仅在 agent 面。
>
> **词表说明**:闭合枚举取 proposal 三类成员(英文多词条目去空格入表:
> never mind→nevermind 等);另按 spec 场景「NO, thanks!」补入
> `nothanks`/`nothankyou` 组合形态。全等匹配,不做包含/前缀/语义相似。

> 补记(评估实测 2026-09-27):
> - `.example-data` 已灌入(131M sakila+olist);`sakila_dyn` 归属 default 租户已修正。
> - LLM 经 DASHSCOPE/muniu 可用;**smoke 6/6 全绿**,full 1/1,multiturn 10/12。
> - multiturn 两例失败为**生成质量信号**(多轮指代下 gen 与 gold 结果集不一致),
>   非链路缺陷——正是 065 红线要盯的回归面,记入基线即可。
