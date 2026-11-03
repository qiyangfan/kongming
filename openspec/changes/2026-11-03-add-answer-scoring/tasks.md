# Tasks: add-answer-scoring

> apply 记录(2026-11-03):api ruff+mypy 全绿;api pytest 1597 通过
> (1591 存量 + 6 评分管线;含 0059 迁移对测试库应用);agent ruff+mypy
> 全绿、pytest 572 通过;web tsc+eslint 全绿;make contracts 产物已再生。
> 环境注:本机统一环境栈(~/workspace/env core+pg)为本轮拉起。

## 1. 评分公式纯函数(测试先行)

- [x] 1.1 `kongming/chat/scoring.py`:`ScoreDecomposition` 域模型
      (主张:重要度/支撑分级/原文摘录;要点覆盖标记;模型输出中的
      分数字段一律不建模不透传)+ `compute_score(decomposition)`
      确定性重算(重要度加权支撑率、覆盖占比、0.6/0.4 加权、平方
      校准、critical 无支撑硬伤封顶 40、单要点轮 C 取 G)——测试:
      同分解同分、封顶边界、空要点/单要点、全支撑=100、全无支撑
      含 critical 低分收敛、校准单调性(tests/test_scoring.py 15 例)
- [x] 1.2 主张净化纯函数:非事实主张(minor+无支撑+无数字实体)过滤后
      才参与依据分(judge prompt 与净化规则口径一致,规则单源在
      纯函数模块)

## 2. 存储与迁移(ADR-004)

- [x] 2.1 新迁移 0059:`chat_turn_score`(UUID v7 PK;message_id UNIQUE
      NOT NULL FK ON DELETE CASCADE;score/grounding/coverage
      SMALLINT CHECK(0-100);decomposition JSONB NOT NULL;
      score_model VARCHAR(64);scored_at TIMESTAMPTZ)——down 精确逆
- [x] 2.2 chat 域仓储 `chat/scoring_repo.py`:insert_if_absent
      (ON CONFLICT DO NOTHING 幂等)/get_by_message

## 3. api 侧管线

- [x] 3.1 `AgentClient.score_answer`:POST /internal/score-answer
      (降级契约同 rewrite_title——不可达/非 200/坏报文/decomposed
      一律 None,永不抛;独立超时 answer_scoring_timeout_sec)
- [x] 3.2 证据装配纯函数:数据轮=result_json 行列(≤50 行/8KiB 截断);
      文档/融合轮=sources 按 source_ref 取 rag 父块摘录(每来源
      ≤500 字、≤8 来源,新增 SqlDocumentRepo.fetch_contents_by_refs);
      两者皆空→不评分
- [x] 3.3 `scoring_tasks.py` 后台任务(守卫读→回调→重算→条件写,
      形态同 title_tasks;judge 失败/证据缺失/非 SUCCESS/非 assistant
      轮各短路);`_finalize_reply` 成功落库后挂触发(仅 SUCCESS 且
      persist=true 且 is_react=false;同步/流式两路径经同一出口,
      react 路径传 is_react=true 排除)
- [x] 3.4 config:`answer_scoring_enabled`(缺省 True)/
      `answer_scoring_timeout_sec`(90s);.env.example 注释段

## 4. agent 侧评审端点

- [x] 4.1 `/internal/score-answer`(X-Internal-Token 鉴权,防线与
      rewrite-title 同构):装配 prompt→LLM(task="ANSWER_SCORING")
      →结构化分解 JSON 解析(复用 parse_summary_json)→净化剥离多余
      键(含违规分数字段)→返回分解+score_model
      (LlmClient.routed_model 新增只读归因访问器);trace_id/tenant_key
      透传使评审用量归因到本轮
- [x] 4.2 judge prompt 资产(`scorer.py` 独立模块,同 titler 形态,
      不进 prompt_fingerprint 资产集——主图指纹零变化):主张口径
      (可验证事实断言;主观/建议句不纳入)、重要度三档、支撑三级、
      要点拆解纪律(服从真实要点数,禁止原问复述凑数)、输出严格
      JSON(不含分数)、主张数上限 ≤12

## 5. 质量台查询面(ADR-005)

- [x] 5.1 quality 仓储/服务/路由:page 行扩 score/grounding/coverage
      (LEFT JOIN 未评分 null);summary 扩 avg_score(零评分 None)/
      low_score_count(阈值 60 常量)/阈值回显;新增
      `GET /system/chat-quality/score/{message_id}` 评分详情(权限点
      同既有;租户不可见/未评分统一 404 防探测)
- [x] 5.2 `make contracts` 同步 openapi.json 与前端类型,产物已再生

## 6. 前端质量台

- [x] 6.1 质量台列表「AI 评分」列(未评分显「未评分」;低于阈值红色,
      点击开详情);汇总卡增「AI 平均分」(未评分窗显「未评分」);
      详情抽屉评分块:总分/依据/覆盖三卡 + 要点覆盖 + 评审模型 +
      主张分解列表(重要度/支撑徽标+证据摘录)+ 净化剔除区;
      既有用户评分列更名「反馈」避免歧义
- [x] 6.2 样式全部走语义 token(destructive/foreground/muted/border),
      无新增硬编码色与 dark: 前缀,亮暗两态随 token 自动成立;
      `pnpm typecheck && pnpm lint` 全绿(人工亮暗走查随交付验收,
      无独立视觉资产)

## 7. 测试与验收

- [x] 7.1 单测:公式 15 例(tests/test_scoring.py)/幂等仓储与任务
      管线 6 例(tests/test_scoring_task.py:幂等跳过/材料装配守卫
      (非 SUCCESS/不存在/证据不可装配)/happy path 落库含审计分解/
      降级与异常静默不落分)
- [x] 7.2 eval 实跑(2026-11-03,本地 live 栈,新指纹 0b54ecda):
      smoke 7/7 全绿(含 judge 抽检层);full(动态组)1/1;
      multiturn 12/12 全绿。首轮 11/12 暴露 sakila_multiturn_001
      turn2 [EXEC] 同因连败(LLM 违反 DISTINCT+ORDER BY 投影纪律,
      selfheal 泛化喂回修不动)——随本批修复 agent 侧
      `_selfheal_guidance` 补 DISTINCT 错误形态分类(逐字正确形态
      模板引导,测试先行),实跑验证 12/12;该链路属 generate/selfheal,
      与评分管线无关,修复搭车本批提交。scorer.py 不在
      prompt_fingerprint 资产集,评分管线对主图指纹零影响
- [x] 7.3 spec scenarios ↔ 测试对照表自查:ADDED 三 Requirement
      ——分解与确定性重算(确定性/封顶/非目标轮=test_scoring +
      test_scoring_task;异步零阻塞=触发 create_task 非阻塞+任务
      静默契约);证据装配(数据轮/文档轮/不可装配=test_scoring
      装配函数+task 守卫);降级幂等与开关(降级/幂等=test_scoring_task;
      开关=组合根条件注入,关闭即 None 不触发);MODIFIED 新增四
      Scenario(明细评分列/详情分解/汇总观测/未评分非 0=quality
      路由字段与前端呈现)
