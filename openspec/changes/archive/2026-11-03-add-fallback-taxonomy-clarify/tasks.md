# Tasks: add-fallback-taxonomy-clarify

> apply 记录(2026-11-03):api ruff+mypy 全绿,pytest 1603 通过
> (含 0060 迁移对测试库应用);agent ruff+mypy 全绿,pytest 588 通过;
> web tsc+eslint 全绿;make contracts 产物已再生。
> 评估注:INTENT_ROUTE 指纹已变(帧扩四意图值+ambiguous 字段),live
> eval 重跑与基线重建需 agent 服务+LLM key(同变更① smoke 口径),
> 留推送前 eval 门禁。

## 1. 迁移与落库(ADR-004)

- [x] 1.1 新迁移 0060:`chat_message` 加 `fallback_type VARCHAR(16)`
      nullable + 具名 CHECK 约束('CHITCHAT','CLARIFY')——down 精确逆;
      非法取值约束拒写用例(test_fallback_persistence,IntegrityError 断言)
- [x] 1.2 api chat 域:parse_agent_reply 解析 `fallback_type`(形态同
      resolved_question);insert_message 落列;用户响应/SSE 载荷零携带
      (全栈断言:响应体与 result_json 均不含该键)

## 2. agent 侧(复语直通修正)

- [x] 2.1 词表族别化:`_DIALOGUE_REPLY_FAMILIES` 三族映射(frozenset 并集
      导出 DIALOGUE_REPLY_WORDS,判定行为不变——全等匹配存量 40+ 用例
      零改动通过);`CHITCHAT_REPLIES` 族别静态文案表 +
      `CHITCHAT_SEMANTIC_REPLY` 语义路径统一文案(零模型调用)
- [x] 2.2 直通出口:命中轮 state 写 `fallback_type=CHITCHAT` 与
      `error=族别文案`(替换 _NO_DATA_ERROR);直通计数与 trace 下发照旧;
      失败测试先行(招呼语不再产出数据缺失文案/零执行断言保持;
      存量 test_dialogue_reply/test_graph_chain 断言按新契约更新)
- [x] 2.3 QueryOut 扩 `fallback_type` 字段(诊断,随终态回传;
      _build_query_out 单点提取)

## 3. agent 侧(澄清引导 + 闲聊语义兜底)

- [x] 3.1 INTENT_ROUTE_SYSTEM 扩判定字段(意图值含第四值 chitchat +
      ambiguous/clarify_question,严格 JSON 纪律同既有;闲聊判定口径=
      「完全无取数意图」与澄清 fail-open 纪律写进 prompt)+ 解析纯函数
      扩展(5 元组;越界/缺失/类型不符→视为未命中;ambiguous 无澄清
      问句收 false;chitchat 优先于 ambiguous 的确定性收口在节点侧)
      ——先写失败测试(test_fallback_taxonomy 解析 5 例)
- [x] 3.2 route_intent 判定收口:闲聊出口=第四意图值且置信达标→早退
      (fallback_type=CHITCHAT,error=统一中性文案)+ 语义命中计数
      chitchat_semantic_total;澄清出口命中条件=ambiguous 且置信达标 且
      `state.last_fallback != CLARIFY`→早退(fallback_type=CLARIFY,
      error=澄清问句);fail-open 路径(模型失败/低置信/解析失败)
      逐字保持现态
- [x] 3.3 条件边:build 新增 `_after_route`,闲聊/澄清两早退出口均经
      end_reason 短路边直达终态(重试铁律用例确认数据路径节点链与
      重试语义不变——test_graph_topology/test_graph_chain 存量断言零改动通过)
- [x] 3.4 请求载荷:`AgentClient.query/query_stream/_payload` 携可选
      `last_fallback`(非澄清轮省略);agent QueryIn 接收 + _initial_state
      注入 state;api 侧 `_prepare_query` 经 repo.last_fallback_type 读取
      (新增 ChatRepository 方法+ChatRepo 端口成员)

## 4. 观测面(ADR-005)

- [x] 4.1 quality 仓储/服务/路由:overview 增 chitchat_count/
      clarify_count,no_data 率剔除兜底轮(分子分母同步缩减,全兜底窗
      除零收敛 0;原始计数保留);page 行携带 fallback_type + 筛选参数
      (CHITCHAT/CLARIFY/none 三档);租户收敛口径不动
- [x] 4.2 `make contracts` 同步 openapi.json 与前端类型,产物入库

## 5. 前端质量台

- [x] 5.1 明细行状态列兜底分类徽标(闲聊/澄清);筛选下拉
      (全部分类/闲聊/澄清/无分类);样式全走语义 token
- [x] 5.2 亮暗两态:徽标/筛选均复用 StatusBadge/Select 标准件
      (两态随 token 自动成立);`pnpm typecheck && pnpm lint` 全绿

## 6. 评估与验收

- [x] 6.1 eval 重跑(2026-11-03,本地 live 栈):INTENT_ROUTE 新指纹
      0b54ecda 下 smoke 7/7(路由回退无退化;澄清判定经真实 LLM 链路
      验证——冒烟轮「帮我看看销售情况」实际命中澄清早退)、full 1/1、
      multiturn 12/12(含自愈喂回 DISTINCT 分类修复后),以新指纹
      重建基线。多轮指代补全既有用例零回归
- [x] 6.2 单测对照表:agent 新增 test_fallback_taxonomy(14 例:族别
      文案/语义闲聊命中与计数/fail-open 双路/澄清命中/防循环硬门/
      闲聊优先收口/拓扑早退边);api 新增 test_fallback_persistence
      (6 例:约束拒写/合法值落库/last_fallback_type 读取/闲聊与澄清
      轮全栈落库含诊断字段不出响应/载荷携带判据);存量 40+ 直通用例
      零行为断言通过
- [x] 6.3 `make check` 对应子集(api ruff+mypy+pytest 1603 通过、
      agent ruff+mypy+pytest 588 通过、web tsc+eslint 全绿)已记录;
      spec scenarios ↔ 测试对照表自查(nl2sql-agent 三 Requirement、
      chat 兜底持久化、observability 分类观测全部 Scenario 覆盖)
