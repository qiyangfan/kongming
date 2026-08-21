# 016 任务清单

## 1. query_history 治理(搭车,测试先行)

- [x] 1.1 写失败测试:同题两次回写=一行且 sql_text 为最新;embedding 失败
  保留旧向量;超上限(cap)淘汰 created_at 最旧;不同问句互不影响;
  失败/拦截不回写既有用例回归。
- [x] 1.2 alembic 0007 迁移:存量去重(同 tenant_id+datasource_key+
  question 保留 created_at 最新)→ 唯一索引 uq_query_history_tdq;
  downgrade 删索引(注记 downgrade 前置条件)。
- [x] 1.3 rag repository insert_history 改 UPSERT(ON CONFLICT DO UPDATE,
  embedding=COALESCE 保旧向量,created_at=now())+ 同事务上限淘汰
  DELETE(cap 常量进 rag/models.py=500);service 签名不变。
- [x] 1.4 跑 api 子集门禁红→绿;`uv run alembic upgrade head` 实库验证。

## 2. 评估器骨架

- [x] 2.1 pyproject:testpaths=["tests"]、markers(smoke/full/multiturn
  三枚)、pythonpath=["."]、dev 依赖加 pyyaml(+types-PyYAML 若 mypy 需要)。
- [x] 2.2 evals/matchers.py:类型归一化(_canon_cell/_safe_sort_key)+
  严格 multiset+宽松双路径(列等价/投影/数值子集/纯文本回退/保序)纯函数。
- [x] 2.3 evals/conftest.py:yaml 加载(单轮/多轮分流)、KM_EVAL_* 环境变量
  与 skip 语义、登录+401 重登、/chat/query 调用(persist=false,history/
  datasource_key 可选)、asyncpg gold 直连(DSN 派生)、expect_reject 判定、
  Recall@K 抽表(复用 feedback.models.extract_table_names)。
- [x] 2.4 evals/test_eval.py:pytest_generate_tests 动态参数化+三层 marker
  用例(smoke 每题断言/full 统计+阈值/multiturn 逐轮);full 统计输出
  严格/宽松双分+Recall+四组口径(动态组单列)。

## 3. 匹配器单测(默认门禁)

- [x] 3.1 tests/test_eval_matchers.py:严格相等/宽松列等价/gold⊆gen 投影/
  列序差异/SELECT */数值子集/纯文本回退/保序与 multiset/异源类型
  (Decimal vs float、datetime vs ISO)/None 与 str 混排安全。

## 4. 评估集

- [x] 4.1 evals/sakila.yaml:20 题(题型全覆盖,smoke×2,gold 遵规范:
  tiebreaker/业务可读列/显式 schema/结果集 ≤1000)。
- [x] 4.2 evals/olist.yaml:16 题(9 表结构内同构,smoke×2)。
- [x] 4.3 evals/sakila_multi_turn.yaml(6 case)+ olist_multi_turn.yaml
  (4 case):四类上下文依赖覆盖。
- [x] 4.4 evals/datasource.yaml:sakila_dyn 显式 key(tenant=default)×3+
  跨租户拒绝×1(预期 400 DATASOURCE_INVALID)。
- [x] 4.5 gold 连跑两次结果一致性验证(每份 yaml 抽 tiebreaker 题)。

## 5. README 与基线

- [x] 5.1 evals/README.md:运行方式/前置环境/gold 编写规范八条/匹配规则/
  基线表(首基线待冒烟回填)。

## 6. 收尾验证

- [x] 6.1 `make check` 全绿(api ruff+mypy+lint-imports+pytest / agent / web)。
- [x] 6.2 契约核对:零端点变化,`make contracts` 确认无漂移。
- [x] 6.3 真 LLM 冒烟:smoke 5/5+full(宽松 37/44)+multiturn;四组分数回填
  README 首基线+失败归因注记;清场命令为 README 记录形态(011 实操同款,
  评估 persist=false 零沉淀本次无需触发);评估集两轮口径修订(排序对齐/
  值列点名/NULL 语义)后复验。
- [x] 6.4 R1 独立评审(0 严重/7 建议全修:spec 匹配规则精确化/yaml 畸形
  显式 raise/service 措辞/uuid 归一/tasks+proposal 对齐/死 fixture 清理)
  → 归档 sync 主 spec(eval/ai-eval/rag)+ HANDOFF 更新。
