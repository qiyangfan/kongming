# NL2SQL 评估集(eval,016)

黄金指标:**Execution Match**(gold_sql 直连执行 vs 链路响应结果集,严格/宽松
双分)。评估器以进程外工具形态对真实链路运行(登录→/chat/query persist=false,
与真实用户同路径;不落业务数据)。

## 运行

```bash
make up                                        # pg+redis(基础设施)
# 起 api(8000)与 agent(8001);agent .env 已配真 LLM key
cd apps/api
KM_EVAL_ADMIN_PASSWORD=Kongming#2026 uv run pytest evals -m smoke      # ~5 题
KM_EVAL_ADMIN_PASSWORD=Kongming#2026 uv run pytest evals -m full       # 全量单轮(含动态组)
KM_EVAL_ADMIN_PASSWORD=Kongming#2026 uv run pytest evals -m multiturn  # 多轮上下文
KM_EVAL_ADMIN_PASSWORD=Kongming#2026 uv run pytest evals/retrieval -m retrieval  # 文档检索质量(语料幂等上传+multi-hop Recall@5)
```

环境变量:`KM_EVAL_API_BASE`(缺省 127.0.0.1:8000)/`KM_EVAL_ACCESS_TOKEN`
(Keycloak 形态下显式 access JWT)或 `KM_EVAL_ADMIN_USERNAME`(缺省 admin)/
`KM_EVAL_ADMIN_PASSWORD`(旧账密形态,二者至少一项,缺失整层 skip)/
`KM_EVAL_PG_DSN`(缺省自控制面 DSN 派生,gold 直连执行)/
`KM_EVAL_AGENT_BASE`(缺省 127.0.0.1:8001,取 prompt 指纹)/
`KM_EVAL_INTERNAL_TOKEN` 或 `KM_INTERNAL_TOKEN`(指纹端点令牌;缺省从
环境继承)/`KM_EVAL_BASELINE_FINGERPRINT`(显式上次基线指纹,供 CI 注入)。

默认门禁(`pytest -q`/make check/CI)只收 tests/,评估须显式指定 `evals`
目录+分层运行(eval spec「评估运行形态」);匹配器纯函数单测在
`tests/test_eval_matchers.py` 进默认门禁。

**双运行形态(065)**:
- **显式触发**:本地/预发手动跑 smoke/full/multiturn,全过才视为该环境基线可信;
- **定时门禁**:`eval-nightly` workflow 每日自动跑 smoke 层,通过率落 artifacts
  并与上一次运行可比;劣化 ≥5pp 逐题溯源后判失败(首次无基线只记录)。
  LLM 凭证走 repository secret(`KM_AGENT_LLM_API_KEY` 等),缺失时 job
  **显式失败**,不静默跳过。job 内置清理 query_history 同题沉淀与 ragctx
  缓存(011 教训)。
- **judge 抽检层(092)**:smoke 层同时运行 `test_judge.py`(标记
  `smoke+judge`)——对非确定性产出(报告/任务结论/协作裁决/文本回答)按
  rubric 抽样评审(每类型 ≥3,存量不足全量,类型空缺如实记录不计失败);
  通过率落 `judge_run/judge_result` 表并打印进 artifacts,与上次基线
  (同类型同 rubric 版本最近一次 COMPLETED run)对比,劣化 ≥5pp 判门禁
  失败(逐对象溯源信息随失败输出);跨 rubric 版本显式标注不可比。
  judge LLM 凭证=smoke 同款 env(`KM_EVAL_LLM_API_KEY`,缺省回退
  `KM_AGENT_LLM_API_KEY`),缺失显式失败。rubric 随代码演进
  (`evals/judge/rubrics.py`,任何维度/权重/模板变更 MUST 递增 VERSION)。

## 评测集自动构造(092)

三段闸:**LLM 生成 → 自洽验证 → 人审并入**;候选落
`datasets/candidates/<tenant>/`(文件态,不落库),人审是唯一并入路径。

```bash
cd apps/api
# ① 生成+自洽验证(gold 直连业务库执行+README 规范校验+语义去重 0.92)
KM_EVAL_LLM_API_KEY=… KM_DB_PASSWORD=… uv run python -m evals.construct generate \
  --tenant default --datasource sakila --per-category 2
# ② 人审:编辑候选 yaml,按 id 圈定通过项(validation.ok=true 才可并入)
# ③ 并入收口一条命令:移动并入正式评估集 → 全量评估重跑 → 基线行登记
uv run python -m evals.construct merge --tenant default --ids <id1,id2> \
  [--no-full-eval] [--reason "…"]
```

自洽验证四闸:gold 可执行+行数 ≤1000 / 规范校验(tiebreaker、schema 限定、
非裸 ID 投影、非 SELECT *——README 编写规范操作化,`construct/validators.py`
与手工新题同一套判定)/ 语义去重(embedding 余弦 ≥0.92 标记重复)/
id 冲突拒绝。并入后基线行自动登记进本文件基线表(原因=自动构造扩题)。

**评估重复性口径**:评估以 persist=false 运行——不落会话/消息,且不触发
query_history 回写(回写挂 persist 条件,测试通道不沉淀),dev 库零污染。
但历史冒烟/人工问答的沉淀仍在:prompt 类改动后重跑评估前,先清同题沉淀
与 ctx 缓存(011 教训——否则验证的是缓存不是 prompt):

```bash
podman exec kongming-postgres-1 psql -U kongming -d kongming \
  -c "DELETE FROM rag.query_history WHERE question LIKE '%影片%'"
podman exec kongming-redis-1 redis-cli --scan --pattern 'km:ragctx:*' | xargs -r -n1 podman exec -i kongming-redis-1 redis-cli DEL
```

## 正式对照记录(2026-11-23)

- BM25 对照:题集 `retrieval/formal_bm25.yaml`(80 题;2026-11-23 补品牌型号/
  标准编号及扩语料题),报告 `retrieval/reports/bm25-formal-2026-11-23.md`。
  两组 Recall@5:overall 0.994、precise/paraphrase 1.000、multihop 0.977,
  逐题无差异(唯一失配 f_mhop_03 两路同失),P95 19ms;结论 keep——精确词
  无改善(阈值 5pp),维持 ts_rank,判据=真实/更大语料后按同一登记条件复测。
  新索引文本(文档名+正文)下同条件复跑:overall 0.981、两路逐题一致
  (边界失配 03/10/17 两路同失)、P95 11ms,结论仍 keep;新旧行因索引口径
  不同不可直接比较。
- Wiki 问答对照:题集 `wiki/formal_wiki.yaml`(26 题);主题页补至七页覆盖
  题集涉及的全部 9 份文档后按登记条件重跑(两轮结论一致),报告
  `wiki/reports/wiki-formal-2026-11-23.md`。结论 keep:wiki_first/parallel
  跨资料 90% 与 raw 持平(要求至少 +5pp)、引用支持 <95%;单资料 10/10 无
  回归。首轮两页原型的 keep 是覆盖率假象,已由本轮取代。
- 文档多查询分解(agent 链路,2026-11-23):K8s 30 题真实链路
  `route_intent→retrieve`,单跳组全跳 1.000;同文档多跳 0.667→0.833(5/6);
  跨文档 0→1/6(仅报告);总体 Recall 0.833→0.883、全跳 0.733→0.800。
  该判别为一次性实验室复跑,不是 CI 题集;实现与边界见
  `openspec/changes/archive/2026-11-23-add-doc-query-decomposition/design.md`。
- 运行前置:api/agent 在本机启动;BM25 对照 LLM/embedding 经 muniu(`:8787`)
  转发,Wiki 重跑直连 maas 镜像端点(`qwen-plus` 别名);评测登录用
  `KM_EVAL_ACCESS_TOKEN`(Keycloak 形态)。

## 评估集分层

| 文件 | 内容 |
|---|---|
| `sakila.yaml` | sakila 单轮 28 题(smoke ×2;092 并入 3 题) |
| `olist.yaml` | olist 单轮 19 题(smoke ×2) |
| `sakila_multi_turn.yaml` / `olist_multi_turn.yaml` | 多轮 turns[](6+4 case) |
| `datasource.yaml` | 动态数据源(sakila_dyn 显式 key ×3 + 跨租户拒绝 ×1) |
| `default.yaml` | default 数据源负例(实体领域错配,expect_no_data ×2) |
| `retrieval/retrieval_eval.yaml` | 文档检索质量(retrieval-eval;corpus/ 语料幂等上传,28 题=single_hop 13+multi_hop_doc 6+multi_hop_cross 9,gold=文档名+必含片段) |
| `retrieval/retrieval_scale.yaml` | 实验室规模语料(生成器 `generate_scale_corpus.py` 产出:corpus_scale/ 45 文档 234 父块,74 题,含 36 店近似干扰条款;`KM_EVAL_RETRIEVAL_DATASET` 切换) |
| `retrieval/retrieval_scale_v1.yaml` | 判别语料(同一生成器 `--variant stripped`:corpus_scale_v1/ 正文剥离店名,店名仅存于文档名;与正版同题 74 道,块索引上下文判别器) |
| `retrieval/k8s_eval.yaml` | 真实公开文档评测集(K8s 中文 concepts 73 篇/1400 父块,CC BY 4.0,归属与改动见 corpus_k8s/NOTICE.txt;30 题=单跳 18+同文档多跳 6+跨文档 6;题面与 gold 由模型按原文事实编写并经程序校验与 AI 复核,未经人工领域评审) |
| `retrieval/negative_queries.yaml` | 语料外负例 12 条(全文/向量门槛标定用,见 `calibrate_fulltext_gate.py`) |

用例字段:`id`(tenant 前缀推断租户)/`question`/`gold_sql`/
`expected_tables`/`category`;可选 `tenant`/`datasource_key`(动态路径)/
`gold_database`(gold 直连独立业务库,动态源题用)/
`expect_reject`(预期拒绝,无 gold_sql)/
`expect_no_data`(预期哨兵短路 NO_DATA:success=false+verdict=NO_DATA+
SQL 空=零执行,与 expect_reject 的 HTTP 拒绝不同形;不计严格分)/`smoke`。

## gold_sql 编写规范(新用例必守)

1. **tiebreaker 必加**:`ORDER BY ... LIMIT N` 必须追加唯一列(主键)作第二
   排序键(如 `ORDER BY COUNT(*) DESC, customer_id LIMIT 5`),保证结果集
   行序确定(gold 连跑两次完全一致)。反例教训(上游 011 实测):sakila 租赁
   top5 全并列 3 次;top-N 题固有退化(gen 无 tiebreaker 时仍可能 fail,可接受)。
2. **投影业务可读列**:问业务实体(客户/影片/演员)时 JOIN 出名称列投影,
   不裸投影 ID——「gen 投影姓名 vs gold 投影 ID」在数值子集路径必挂
   (gold 数值型 id 列 gen 缺失),靠本条规避。名称可能重名时
   `GROUP BY 主键, 名称列`。
3. **结果集 ≤1000 行**:生成侧链路有 1000 行截断上限,超限题双侧截断口径
   不一致会假阴性(Kongming 新增);大基数聚合改 top-N(带 tiebreaker)。
4. **明细题带 LIMIT 100**:与 prompt 明细 LIMIT 惯例对齐(行数口径可比)。
5. **外层 ORDER BY 才算保序**:匹配器按子串识别保序;子查询里的 ORDER BY
   也命中——gold 若任何位置含 ORDER BY,外层必须保序,否则 PG 不保证外层
   行序会误判。
6. **避免投影时间戳列**:gold 直连(asyncpg 原生类型)与 gen 链路响应
   (JSON ISO 字符串)类型异源,匹配器 canon_cell 已归一化兜底,但评估题
   尽量以业务数值/名称为主(少一层归一化依赖)。
7. **gold 显式 schema 限定**(`tenant_sakila.film`):评估器直连不设
   search_path。
8. **连带同步**:join 新增表必须同步 `expected_tables`;同题多路径副本
   (老路径 yaml + `datasource.yaml`)必须同步改。改动 gold 后连跑两次
   确认一致(脚本见 tasks 4.5),再跑全量基线。
9. **明细题问句显式指定排序键**(016 冒烟实证,重要):prompt 规则 6 已
   立约 gen 明细必带 ORDER BY(标识列或主键)——gold 裸 LIMIT(物理序
   取 N 行)与 gen 排序后取 N 行**集合天然错位**,必挂。问句写「按 X
   排序」(gen 规则「用户指定排序则从之」→ 与 gold 同键),gold 同键
   ORDER BY+LIMIT;DISTINCT 题排序键必须进投影(PG 语义)。top-N 类
   问句点名值列(「…及其总金额/数量」),消「只投影名称」歧义。
10. **聚合小数值 gold 统一 `ROUND(…, 2)`**(018 口径互锁):prompt 规则 8
    已立约 gen 平均值/比率/除法结果 ROUND 两位——gold 全精度直出
    (如 `AVG(rental_rate)`=2.9846… vs gen 2.98)在 1e-6 容差下必挂。
    存量 4 题(sakila_011/olist_008/olist_014/olist_016)已于 018 对齐。

## 匹配规则(严格/宽松双分)

- **严格**:归一化后 multiset 精确等值(排序无关)。
- **宽松**(黄金指标)双路径:
  1. **列等价+投影行近似**:列名集合相等或 gold ⊆ gen(大小写不敏感),
     gen 按 gold 列名投影(兼容列序/SELECT *)后行集合近似等值
     (数值容差 1e-6;gold 含 ORDER BY 保序比较,否则 multiset);
  2. **数值子集**:gold 数值 multiset ⊆ gen 数值 multiset——救聚合别名
     差异/列名不兼容但数值全对的形态。**天然无序**(保序仅约束路径 1);
     纯文本结果集(无数值)回退严格集合等值兜底。
- 已知边界(既定语义,非遗漏):宽松分不惩罚序错(值集对时路径 2/回退
  兜底放行);gold 投影数值 id 而 gen 改投影名称且数值对不上时判 fail
  ——属评估集编写问题,规范第 2 条规避。匹配器逻辑单测
  `tests/test_eval_matchers.py` 进默认门禁。

## 失败归因口径(096)

full/multiturn 的每条失败明细在既有 SQL/结果集信息之外,带**确定性一级分类**
与命中判据原文;full 统计块在四组分数之后增列一级分类计数与 SELECT 层拆分。
分类是纯函数(`evals/attribution.py`,零外部调用、零 IO),单测
`tests/test_eval_attribution.py` 进默认门禁;SHALL NOT 进任何运行时代码路径。

**一级分类与判定序**(先匹配先归类,一题一类):

| 序 | 类 | 判据 | 修复指向 |
|---|---|---|---|
| 1 | `NEG_GUARD` | expect_reject / expect_no_data 用例失败(不再按正例判据细分) | 哨兵/绑定校验 |
| 2 | `REJECT` | verdict=BLOCKED(防火墙拦截终止) | 防火墙规则 |
| 3 | `ABSTAIN` | 正例题 verdict=NO_DATA(误弃答) | 召回/哨兵误触发 |
| 4 | `EXEC` | success=false(verdict=ERROR/selfheal 耗尽/链路异常)或无生成 SQL | 执行/selfheal |
| 5 | `SELECT` | 选表缺失(附层拆分,见下) | 召回或生成 |
| 6 | `JOIN` | gen 表集已覆盖 gold 表集,但 JOIN 数少于 gold | 关系配置/生成 |
| 7 | `METRIC` | 表与 JOIN 数一致,两侧聚合函数集合不一致 | 指标口径/prompt |
| 8 | `FILTER` | 聚合一致,WHERE 过滤面不一致 | 谓词/时间窗理解 |
| 9 | `OTHER` | 无判据命中(显式未归因) | —— |

**判定序的两点说明**:①REJECT/ABSTAIN 与 EXEC 的判据在真实数据中互斥
(BLOCKED/NO_DATA 题的 success 恒为 false),verdict 携带时终态先行,EXEC
兜「非终态形态」的执行失败——否则 REJECT/ABSTAIN 永不命中成死类;②
SELECT 的层拆分:`expected_tables` 未被生成 SQL 覆盖 = **召回层**(期望表
没进提示词);expected 已覆盖但 gold 表集未覆盖 = **生成层**(表在提示词
里没用上)。抽表复用 `kongming.feedback.models.extract_table_names`(末段名
小写折叠,CTE 排除),expected_tables 全名/末段名混传自动折叠对齐。

**结构判据的词法口径**:JOIN 数=词边界 `\bjoin\b` 计数(隐式逗号连接计 0);
聚合集合=词边界 `(count|sum|avg|min|max)\s*\(` 提取(防 `sum_` 前缀标识符
误命中);WHERE 面=正则截取 where 段(至 group/having/order/limit/offset)
后小写+空白压缩比较——子查询 where 会并入,两侧对称处理,证据面用途可接受。

**OTHER 桶纪律**:OTHER SHALL 显式报告数量与题目 id;占比 ≥20% 时统计块
输出「归因规则不足,需人工扩充」提示,SHALL NOT 静默吸收——启发式判据
覆盖不了所有失败形态,安静比诚实更贵。gold 执行失败/链路不可达等评估
环境与资产错误同样落 OTHER(signals 注明非链路质量失败)。

**用途边界(硬约束)**:归因结果 SHALL 用作提案立项与知识治理排序的
**证据面**;SHALL NOT 作为回归红线——红线仍为黄金指标劣化 ≥5pp 与提示词
指纹可比性(eval 域基线管理 + 073)。启发式判据敌不过 LLM 采样波动,拿它
当闸会误伤;新增用例/改判据先读本节并同步 `tests/test_eval_attribution.py`。

## 辅助指标

- **表命中(Recall 间接口径)**:生成 SQL 涉及表(sqlglot 抽表,CTE 排除)
  覆盖 expected_tables——表选对是召回生效的链路可见结果。
- **Valid Execution**:链路 success(生成 SQL 在链路内真实执行成功)。
- **Retry Rate**:响应 retry_count(selfheal 触发情况)。

## 跨指纹比对(073)

评估运行**首行**打印 prompt 资产指纹(agent `GET /internal/prompt-fingerprint`,
`X-Internal-Token` 守门;不可达/无令牌时显式打印「指纹不可得」,不静默跳过):

```
=== prompt 指纹: a3f9c1d2 ===
```

full 层四组分数同屏携带指纹;与上次基线指纹**不同**时,分数后输出
**不可比标注**(标注不可比而非判劣化,SHALL NOT 静默套用劣化红线):

```
=== 评估统计: 宽松 48/50 (96%) | 严格 35/50 (70%) | 表命中 48/50 (96%) | 动态数据源 4/4 | prompt 指纹 b4e17a09 ===
跨 prompt 版本,通过率不可直接比较(本次指纹 b4e17a09 vs 基线指纹 a3f9c1d2)
```

此时两次数字的差异**不能**直接解释为质量回归或提升——prompt 资产已变,
对比基线失效。处置:同指纹基线上重跑(或新建本指纹基线),需要溯源时
用 git log 对照 `graph/prompts.py` 等四份资产(指纹只标识「哪一版」,
不说明「改了什么」)。

上次基线指纹来源(优先级):`KM_EVAL_BASELINE_FINGERPRINT`(CI 注入)→
`evals/.baseline_fingerprint`(本地上次运行记录,gitignore);每次运行结束
把当次指纹写回该文件。与 065 红线的关系:红线判定**先读指纹**,跨指纹
时不比数值(可比性由代码输出结构化事实,不靠人记得)。

## 检索质量评测基线(retrieval-eval,2026-11-07 起)

文档检索质量独立口径:语料十五文档 36 父块挂 default 租户 default 数据源
(恒可用形态,CI 空库亦可跑;2026-11-23 扩入 6 份文档:门店设备与设施规范、
联名卡与渠道规则、门店促销与券码管理、影片调拨与仓储规范、设备租借与损坏
赔偿细则、投诉与回访流程,承载标识近似与改写型题型),逐题走
`/system/document/retrieval-probe`
(与主检索同源),指标=父块级 macro Recall@5 + 全跳覆盖率,逐题另附
`paths=` 逐路 Recall(向量/全文/实体)用于归因。被索引文本自 2026-11-23 起为
「文档名+块正文」(embedding 与 tsv 同源,返回正文仍为原文)。
`KM_EVAL_RETRIEVAL_DATASET=<文件名>` 可切到其他题集(如实验室规模语料
`retrieval_scale.yaml`);真实租户语料按同形态自建 dataset + corpus 目录即可,
无需改评测代码。全文/向量门槛标定用 `calibrate_fulltext_gate.py`(正例取
题集、负例取 `negative_queries.yaml`,输出两群分布与候选阈值,不写配置)。**红线只在
同形态行之间比较**(劣化 ≥5pp 逐题溯源);「实体开」「精排开」形态行仅作
对照分析,不作红线。坏档闸:macro <0.5 判检索链路异常(向量路
死亡/语料失配)。

| 日期 | 形态 | macro Recall@5 | 全跳覆盖率 | n | 备注 |
|---|---|---|---|---|---|
| 2026-11-07 | 实体关(基线,add-retrieval-quality-eval 首采;sakila 数据源形态) | 0.969 | 0.938 | 16 | 唯一漏跳=retr_sakila_012 漏「基础租期」父块(泛化问句词面不匹配) |
| 2026-11-07 | 实体关(定版形态=default/default 恒可用,进 nightly) | 1.000 | 1.000 | 16 | 与上行差异全在 012 边界题采样翻转(泛化问句「基础租期」父块一漏一中);语料偏小、embedding 强,基线近天花板属已知形态,红线判定时 012 类边界题逐题溯源豁免 |
| 2026-11-07 | 实体关(同日 A/B 复采,add-doc-event-entity-recall 对照基线) | 1.000 | 1.000 | 16 | 与定版行一致(A/B 同环境同条件) |
| 2026-11-07 | **实体开**(entity=on,LLM 抽取同 agent 面) | 0.938 | 0.938 | 16 | 净 -6.2pp,首版实体路在此语料为负收益:泛频实体「逾期费」join 出 6 子块挤占融合池(retr_sakila_002 正确父块被挤出);012 边界题转命中。机制由 probe 逐路数据归因(vector 8/fulltext 0/entity 6)。调优方向=实体路入池门槛(共享实体数≥2)/实体路降权或仅两路候选不足时补缺——**对照行不作红线**,调优挂后续,不改闸门 |
| 2026-11-07 | 实体开(入池门槛调优后,共享实体数≥2;tune-entity-recall-pool) | 1.000 | 1.000 | 16 | 与实体关持平,-6.2pp 回归消除:002 泛频单实体路整路短路(probe 实体路 0 候选,调优前同题为 6),010 桥接题实体路以共享实体数 2 精准入池(score=2)。门槛后扩展路仅在真桥接形态出力,单实体共现不再入池;部署仍缺省关(对照不劣化,开关语义不变) |
| 2026-11-23 | 实体关(十文档语料重取基线) | 1.000 | 1.000 | 16 | 补入《门店设备与设施规范》后重跑,与九文档定版行一致(16/16 全跳) |
| 2026-11-23 | 精排开(bailian gte-rerank-v2;旧索引文本;对照行,未启用) | 0.969 | 0.938 | 16 | 精排 16/16 成功,p50≈0.5s/p95≈1.3s;唯一退化=retr_sakila_012 边界题(登记豁免),无非负收益;结论=当前语料证据不支持启用,配置在 `.env` 注释段就绪 |
| 2026-11-23 | 精排开(bailian qwen3-rerank;旧索引文本;对照行,未启用) | 0.969 | 0.938 | 16 | 与 gte 行逐题一致(同退化 012),p50≈0.47s/p95≈1.35s |
| 2026-11-23 | 实体关(扩语料基线,十五文档 36 父块) | 1.000 | 1.000 | 28 | 新增 12 题(近似标识/改写/长文档定位)后融合召回仍全跳,基线仍近天花板 |
| 2026-11-23 | 精排开(bailian gte-rerank-v2;旧索引文本;扩语料对照行,未启用) | 0.982 | 0.964 | 28 | 唯一退化仍是 012 边界题(登记豁免),无正收益;p50≈0.3s/p95≈0.8s;维持不启用 |
| 2026-11-23 | 实体关(zhparser 分词,旧索引文本;ts_rank;对照行,未启用) | 0.929 | 0.857 | 28 | 专用实例本地 A/B(simple 行 1.000):4 题丢跳,常用词泛化命中稀释融合池;zhparser+BM25 在 80 题上两路无差异;配方与复标步骤见 docs/zhparser-runbook.md |
| 2026-11-23 | 实体关(块索引上下文=文档名+正文;新形态基线) | 0.964 | 0.929 | 28 | 对比旧形态 1.000 为 -3.6pp(红线 5pp 内):retr_sakila_009 漏「租赁规则」父块#2、012 漏#0(012 为登记豁免边界题);向量路 0.964、词面路 0.071;同配置复跑逐题一致(确定性形态) |
| 2026-11-23 | 精排开(新索引文本;bailian gte-rerank-v2;对照行,未启用) | 0.893 | 0.857 | 28 | 无精排同口径 0.964 → -7.1pp;失配 009/012/015/ext_006(012 两父块全失);p50≈0.24s/p95≈0.46s;维持不启用 |
| 2026-11-23 | 精排开(新索引文本;bailian qwen3-rerank;对照行,未启用) | 0.893 | 0.857 | 28 | 与 gte 同 macro,失配题略异(ext_005 替代 ext_006);p50≈0.27s/p95≈0.34s;维持不启用 |
| 2026-11-23 | 实体关(zhparser 分词,新索引文本;对照行,未启用) | 0.946 | 0.893 | 28 | 专用实例复跑:词面路 0.929,融合较 simple 同口径 0.964 低 1.8pp;仍不切换 |
| 2026-11-23 | 实体关(全文兜底零分剔除;当前形态) | 0.964 | 0.929 | 28 | 与上一基线逐题一致(009/012 不变);融合只接收 AND 轮正分词面证据 |

## 实验室规模语料基线(retrieval-scale,2026-11-23)

口径:corpus_scale/(45 文档 234 父块,36 店连锁手册,生成器
`generate_scale_corpus.py` 产出)与 retrieval_scale.yaml(74 题);
`KM_EVAL_RETRIEVAL_DATASET=retrieval_scale.yaml` 切换。逐题附逐路
Recall(向量/全文/实体),融合列=macro Recall@5。

| 日期 | 形态 | 融合 Recall@5 | 全跳率 | 词面路 Recall | n | 备注 |
|---|---|---|---|---|---|---|
| 2026-11-23 | 基线(simple,ts_rank,精排关,实体关) | 1.000 | 1.000 | 0.162 | 74 | 向量路 1.000 独自兜底,词面路几乎不贡献 |
| 2026-11-23 | 精排开(gte-rerank-v2) | 1.000 | 1.000 | — | 74 | 74/74 成功,p50 260ms/p95 398ms;无增量 |
| 2026-11-23 | 实体开 | 1.000 | 1.000 | 0.135(实体路) | 74 | 实体路自身 Recall 0.135,融合无增量 |
| 2026-11-23 | zhparser 分词 | 1.000 | 1.000 | 0.851 | 74 | 词面路自身 0.162→0.851,融合仍 1.000(向量已满分) |
| 2026-11-23 | 块索引上下文(文档名+正文) | 1.000 | 1.000 | 0.162 | 74 | 正版语料回归:与改造前逐字持平,融合仍饱和 |
| 2026-11-23 | 全文兜底零分剔除 | 1.000 | 1.000 | 0.162 | 74 | 与基线逐题一致;判别语料(剥离店名)同口径复跑仍 1.000 |

说明:

- 构造教训(初版→定版):条款若不含店名(只存在于文档标题),按店名提问的
  题对**所有路**都无解;chunk 缺文档/实体上下文时,换排序器或分词器都救不回来。
  该缺口已由块索引上下文改动落地:被索引文本=文档名+块正文(embedding 与 tsv
  同源,返回正文原文不变),存量经 `scripts/rebuild_embeddings.py --table
  document_chunks` 重建。
- 判别语料 A/B(`retrieval_scale_v1.yaml`,正文剥离店名,同题 74 道;旧代码
  在本语料低于坏档闸 0.5,只作对照口径):旧代码 融合 0.291/全跳 0.243
  (向量路 0.291、词面路 0.162);重建索引后 融合 1.000/全跳 1.000(向量路
  1.000)。提升 +70.9pp,判别语料达成预登记条件(≥5pp)。
- 门槛标定(改造后复测,`calibrate_fulltext_gate.py` 已按 gate.py 口径把
  探针向量分从余弦距离换算为相似度;正例 74/负例 12):文档通道探针上向量
  相似度可分——正例 top1 min 0.678/p10 0.714、负例 max 0.630,候选阈值
  0.6722(正例过闸 100%、负例拒绝 100%);mean3 候选 0.6428 同样双 100%。
  但 `KM_RAG_GATE_*` 作用于 schema 召回通道且负例为语料外主题,阈值仅作
  候选、未写入配置,接入前须在目标通道复测。全文路负例仍零命中但正例仅
  12/74,样本不足。
- 融合 Recall 在本语料仍饱和;词面路差异(0.162/0.851)只能在逐路口径观察,
  印证「排序对照被融合洗掉」——排序器/分词器评估必须看逐路指标。

## 真实公开文档基线(K8s 中文,2026-11-23)

口径:corpus_k8s/(Kubernetes 中文文档 concepts 73 篇/1400 父块,源仓库与
CC BY 4.0 归属见 corpus_k8s/NOTICE.txt)与 k8s_eval.yaml(30 题:单跳 18、
同文档多跳 6、跨文档 6);`KM_EVAL_RETRIEVAL_DATASET=k8s_eval.yaml` 切换。
题面与 gold 由模型按原文事实编写;校验=gold 片段唯一落根、片段跨文档零重复、
多跳证据跨父块(均有单测),并由 AI 逐题复核——**未经人工领域评审**,结论按
此边界使用。

| 日期 | 形态 | 融合 Recall@5 | 全跳率 | 逐路 Recall | n | 备注 |
|---|---|---|---|---|---|---|
| 2026-11-23 | 基线(simple,ts_rank,精排关,实体关) | 0.783 | 0.667 | 向量 0.817 / 词面 0.350 | 30 | 失配:Pod#0 融合稀释(向量路命中被挤出)、3 道同文档多跳各缺一父块、6 道跨文档全跳失败(4 道命中一半);真实语料非饱和,作为融合/多跳优化的测量基线 |
| 2026-11-23 | 全文兜底零分剔除(当前基线) | 0.833 | 0.733 | 向量 0.817 / 词面 0.150 | 30 | +5.0pp:恢复 k8s_s_01、k8s_m_02、k8s_m_04;k8s_m_05 失去一跳(原命中为兜底零分无序候选);跨文档 6 题仍全跳 0。词面路 0.150 仅计 AND 轮证据(旧行 0.350 含不可排序的零分兜底命中) |
| 2026-11-23 | zhparser 分词(K8s 语料;对照行,未启用) | 0.817 | 0.733 | 词面 0.700 | 30 | 词面路 0.150→0.700(zhparser 真实中文分词生效),融合仍低于 simple 同口径 0.833;维持不切换 |

说明:

- 兜底零分剔除的依据与验收见变更 `exclude-zero-score-doc-fallback`:兜底谓词
  只要求任一内容词命中,但按全问句 ts_rank 计分恒为 1e-20 量级,名次任意;
  剔除后融合向向量路靠拢,真实语料 +5.0pp,合成/判别语料零回归。
- 跨文档多跳仍是最大缺口:6 题全跳率 0(4 题命中一半);诊断显示多数题 gold
  父块不在任一路的候选前 8(非融合丢失),现有 top-5 契约内无低风险修法,
  登记为已知边界;若未来扩容候选池/多跳策略,再立项。

## 基线记录(016 起)

指纹列(073 起):prompt 资产内容指纹(agent `prompt_fingerprint` 纯函数,
SHA-256 短十六进制前 8 位);历史行未采集记 `—`。两次运行指纹不同时
**通过率不可直接比较**(见下方「跨指纹比对」),SHALL NOT 静默套用劣化红线。

| 日期 | 变更 | 严格 | 宽松 | 多轮 | 动态数据源 | 表命中 | 指纹 |
|---|---|---|---|---|---|---|---|
| 2026-08-21 | 016 首基线(评估集落地,问句排序对齐修订后) | 26/44 (59%) | 37/44 (84%) | 7/10 (70%) | 4/4 | 35/44 (80%) | — |
| 2026-08-21 | /goal 修复轮:多轮裸 LIMIT 口径补齐 3 case(olist_mt_002/sakila_mt_002/006,单轮题同款口径遗漏) | — | — | 8/10 (80%) | — | — | — |
| 2026-08-22 | 018 prompt 护栏(规则 6 统计/明细边界+top-N tiebreaker/规则 8 ROUND 口径/规则 9 投影完备+去重/规则 3 ID 不替名/understand 保真/summarize 统计接地;评估集对齐修订 14 处+新增 6 case) | 37/48 (77%) | 48/48 (100%) | 12/12 (100%) | 4/4 | 46/48 (96%) | — |
| 2026-08-22 | 019 tool-calling 实施期复验:recipe(老链路)零劣化红线 | — | recipe smoke 5/5 + full 零 FAILED | — | — | — | — |
| 2026-08-22 | 019 ReAct 内核等价性(KM_EVAL_AGENT_MODE=react;rag_retrieve 工具内回退内省修复后) | — | react smoke 5/5 | — | — | — | — |
| 2026-08-30 | 047 检索治理(视图不入知识+多轮召回历史继承+文档分流多轮门槛+自愈分类喂回+建议缓存级联失效+文案用户化);伴随知识 AI 再生成与存量视图/保留表条目对账清偿(sakila_mt_001 t2 gold 投影对齐规则 3/9 明细附名) | 34/48 (71%) | 47/48 (98%) | 11/12 (92%) | 4/4 | 45/48 (94%) | — |
| 2026-08-30 | 048 聚合追问与去重护栏(understand 聚合口径显式化+构造条件复述、规则 5 聚合追问单行汇总、规则 9 对应关系组合去重+反例、olist_mt_003 t2 题面消歧「客户记录数」);三靶点收口:018 漏 DISTINCT/olist_mt_006 聚合口径丢失(3/3)/olist_mt_003 | 35/48 (73%) | 48/48 (100%) | 12/12 (100%) | 4/4 | 46/48 (96%) | — |
| 2026-08-30 | 051 实体核对判据(规则 10 表级哨兵操作化+对位反例)+负例集 default.yaml ×2(expect_no_data,001 进 smoke;负例不计严格分,分母 +2)——正例零误伤,哨兵短路 ×3 轮稳定 | 34/50 (68%)* | 50/50 (100%) | 12/12 (100%) | 4/4 | 49/50 (98%) | — |
| 2026-08-30 | 051 收尾 sakila_ext 中文注释补齐+重扫(知识变更,基线纪律复跑,同日二采;多轮未复跑与知识变更无涉)——宽松/动态持平零劣化,严格/表命中 ±1 属采样波动 | 35/50 (70%)* | 50/50 (100%) | — | 4/4 | 48/50 (96%) | — |
| 2026-10-05 | 095 关系感知召回扩展落地(同指纹 f05c8028 开/关两轮对照;096 归因口径同批上线):**扩展关**=宽松 50/50、严格 35/50、表命中 48/50,与 08-30 基线逐项一致(关停形态零回归实证);**扩展开**=宽松 50/50、严格 37/50、表命中 49/50(严格 +4pp、表命中 +1,095 首轮收益证据) | 35→37/50 | 50/50 (100%) | — | 4/4 | 48→49/50 | f05c8028 |
| 2026-10-10 | 自动构造扩题(092 三段闸人审;5 候选入审、3 条并入后复跑——并入数与剔除理由见 sakila.yaml 头注) | 38/54 (70%) | 53/54 (98%) | — | 4/4 | 46/54 | 340dbcea |
| 2026-10-10 | 审查循环数据修复后真实复跑(空 gold a91aca63 剔除、schema 限定 expected_tables 裸名化、6183fe4b 补唯一键 tiebreaker;分母 54→53)——上行的表命中/分母被 4 条 qualified gold 污染(判据恒 False),本行为首条真实口径;olist_016 单题宽松落败属 LLM 采样波动 | 34/53 (64%) | 50/53 (94%) | — | 4/4 | 47/53 (89%) | 340dbcea |
| 2026-10-12 | localenv 环境重建后基线重灌(env 栈 10-11 退役→演示数据 load.sh 重灌+sakila/olist/sakila_dyn 知识 reindex 全量重建[自动润色单轮,非历史人工迭代形态];098 simple 缺省档)——较 10-10 行回落属知识重建差异与采样波动,本行为 localenv 新基线 | 32/53 (60%) | 48/53 (91%) | — | 4/4 | 44/53 (83%) | 340dbcea |
| 2026-10-12 | 098 zhparser 对比(同指纹同环境同数据,唯一变量=分词配置;localenv-postgres 源码编译 zhparser+SCWS,重扫重算 tsv 后复跑;首跑两题撞 chat 每分钟限流 403 作废,提限流后本行为净对比)——宽松 +1、表命中 +2、严格持平,零回归;中文问句全文路增益受 plainto_tsquery AND 语义压制(虚词 token 打断匹配),向量路仍是主载;全文路命中稀缺,gate 对 no_hits 不裁,阈值无需复标(配方与量纲事实见 HANDOFF 分词节) | 32/53 (60%) | 49/53 (92%) | — | 4/4 | 46/53 (87%) | 340dbcea |
| 2026-10-12 | 111 全文路「AND 优先、OR 兜底」回退(**本行结论已勘误**,见下行 112 勘误注记):当时判读=宽松/严格/表命中各 -4/53 系统性劣化(单字虚词词条噪声挤占 RRF 位次),据此回退并在 rag spec 落规范性排除 | 28/53 (53%) | 45/53 (85%) | — | 4/4 | 42/53 (79%) | 340dbcea |
| 2026-10-12 | **111 勘误+112 内容词兜底采纳**(同指纹 340dbcea;变更 112 design D6 全链条):①111 的 -4 系**日配额耗尽事故**——`chat_quota_daily_calls` 500 按租户日累计,A 轮耗大头后 111B 轮中段撞线,逐题日志 8 败中 7 题为 429/403 异常,兜底机制从未被有效评估;②四轮基线(含 zhparser A 轮 49/32/46)另含 2 道 hex id 题恒 403(evals 租户推断对无下划线 id 失效,sakila.yaml 已补显式 tenant)——**历史基线均含此 2 题异常**;③112 内容词兜底(length≥2)修复后 B''=**50/34/47 创新高**(失败 3 题全确定性 FILTER 归因、异常清零),对齐口径(hex 两题按 A 轮恒失败计)49/33/46 三项 ≥A 轮,验收线全过采纳;评估环境常备配方补 `KM_CHAT_QUOTA_DAILY_CALLS=0` | 34/53 (64%) | 50/53 (94%) | — | 4/4 | 47/53 (89%) | 340dbcea |

> **051 注记**(2026-08-30):*严格分母含 2 题负例(expect_no_data 不计
> 严格分);按正例口径对照为 34/48,与 048 基线 35 的 ±1 属 top-N 并列
> 采样波动(宽松门禁两轮均满分)。

> **095/096 注记**(2026-10-05,关系感知召回扩展 + 失败归因口径):
> - **运行形态**:源码评估栈(api 18000 + agent 18001,与 8000/8001 常驻旧
>   进程隔离;agent LLM 出口=muniu 网关),一次性超管账号 `eval089` 绑
>   default/sakila/olist 三租户(冒烟后可删);两轮之间清 `km:ragctx:*`
>   缓存,query_history 沉淀两轮共享同条件。
> - **同指纹可比**:两轮 prompt 指纹同为 `f05c8028`,分数差异可直接归因于
>   扩展开关(`KM_RAG_EXPAND_ENABLED`),非 prompt 变更。095 关停形态与
>   08-30 基线逐项一致——「关停即现状,逐字一致」的零回归铁律在评估集
>   上得到量化实证。
> - **归因分布(096 3.1)**:两轮宽松均 50/50,**零失败题 → SELECT/JOIN
>   归因计数两侧皆空**(归因只对失败题,宽松全过即无样本)——095 的收益
>   由严格分(+4pp)与表命中(+1)承载:扩展表进提示词后 LLM 结果集形态
>   更贴近 gold。「要不要继续投入关系配置」的后续读数依赖宽松口径出现
>   失败题时的 SELECT(召回层)/JOIN 计数,当前评估集上暂无该样本。
> - **冒烟闭环**(095 4.2,sakila 租户):手工配 `customer→rental` 关系 →
>   `recall-probe` 透出扩展明细(问句「每个顾客租了多少次租赁」主召回被
>   payment 分区占满,customer 经关系一跳补入;staff 经 payment→staff 同理)
>   → 真实问答生成 `customer INNER JOIN rental`,verdict=PASS、结果正确。
>   「问句命中 A、答案度量落在关联表 B」的确定性失败形态被 095 闭合。

> **019 注记**(2026-08-22):`KM_EVAL_AGENT_MODE=react` 环境变量使同一
> 评估集经 ReAct 内核跑(请求携带 agent_mode=react;缺省 recipe 口径不变)。
> ReAct 首跑 smoke 曾挂 olist_smoke_002:知识库未抓取租户召回双空,LLM
> 只能用 execute_sql 探测 information_schema/pg_tables 摸表结构,打满
> 轮次上限——rag_retrieve 工具内回退实时内省(与老链路 retrieve 节点同
> 语义)后 5/5。react 每题多次 LLM 调用,耗时约为 recipe 的 2-3 倍。

> **018 演进注记**(2026-08-22):五类失败归因全部闭环——①统计题误加
> LIMIT(规则 6 统计/明细边界重写,GROUP BY/聚合即统计不加 LIMIT);
> ②::numeric 舍入(规则 8 ROUND(…, 2) 口径+规范第 10 条 gold 互锁,
> 存量 4 题 gold 对齐);③投影丢列(规则 9 投影完备+规则 3「ID/编号列不
> 能替代名称列」);④指代消解错位(understand 保真规则:保留上轮全部
> 限定条件);⑤漏 DISTINCT(规则 9 COUNT(DISTINCT)+明细完全重复行
> DISTINCT)。实施期新发现:top-N 排序键并列是 gen/gold 集合错位首因
> (gold 规范第 1 条 tiebreaker 的 gen 侧镜像)——规则 6 补「排序键可能
> 并列必须追加主键等唯一列作第二排序键」后,多轮 5 挂中 4 个直接消解。
> 评估集对齐修订 14 处(全部为 gold/问句与 gen 立约行为的语义对齐,非迁就):
> sakila_013/014/015/018/019 问句显式排序键+点名投影列(规范第 9 条同款)、
> sakila_014/019 gold「客户姓名」=全名单列+去 DISTINCT(重复行=同人同片
> 真实租赁记录,投影不可区分;连跑两次 md5 一致)、sakila_mt_001/005 t2
> gold 外层排序对齐多轮追问保序语义(gen 保真规则保留上轮排名序)、
> sakila_mt_002/006 t1「所有」与 LIMIT 100 冲突问句修订、olist_016/
> olist_mt_004 t1 类目原名 vs 英文翻译消歧(×2 措辞:gen 会把英文名
> 别名为 product_category_name 规避初版)。
> 新增 6 case:sakila_021(ROUND+投影)/sakila_022(投影完备)/
> sakila_023(DISTINCT)/olist_017(统计题 LIMIT,611 分组)/
> olist_mt_005/006(指代保真;olist_mt_005 t2 问句经×2 消歧——
> 「位客户」被解读为 DISTINCT customer_unique_id(33801)vs 客户表
> 行数(35042),「记录/行数」措辞显式对齐 COUNT(*))。残留 2 挂(sakila_014/019 曾挂)已随
> gold 对齐清零——严格口径 37/48 的 11 挂主要为聚合别名/投影形态差异
> (宽松判过的合理形态),非新质量信号。
> **运行注记**:评估连跑曾遇成片失败(api 日志「agent 回调异常:
> Server disconnected」+gen 空 SQL,R4–R6 窗口)——事后定位为**本地
> 网络/VPN 切换的环境事件**(TUN 模式切换窗口路由重建会波及回环
> 连接;当日实证),非配额限流(账号配额 250 万 tokens/30s,评估负载
> 不到 50 RPM 差四个数量级)、非链路回归。异常成片时先查本地网络
> 状态,待网络稳定后重跑即可,无需长冷却。

> **/goal 修复注记**(2026-08-21 同日):①多轮 3 处 gold 裸 LIMIT 为首轮
> 排序对齐修订的遗漏(单轮已改),补齐后复验 3/3 过——多轮口径 8/10,其余
> 两挂(sakila_mt_003 统计题 gen 误加 LIMIT/sakila_mt_005 t2 指代消解)为
> 真实质量信号不变;②query_history 唯一索引改 md5(question) 表达式
> (超长中文问句 2000 字=6000 字节超 btree 行上限会使回写炸索引——0007
> 未提交前修正,语义等价);③expect_reject 分支传输层故障不再按"拒绝"
> 通过(服务未起时 deny 题假绿)。

> **统计口径脚注**:full 44 题含 1 题预期拒绝用例(expect_reject)——该题
> strict 恒 False、recall 恒 True(占位语义),严格率与表命中率各含 ±1 题
> 偏置;动态数据源组 4/4 中 1 题为拒绝验证(组语义=「动态路径+fail-closed」)。

> **047 注记**(2026-08-30):多轮 -25pp 触发逐题溯源,五挂归因闭环并修复
> (047 变更):①便捷视图入知识——行扇出使 AVG 失真(sakila_021)、样例 SQL
> 挤占维度表 top-K、列缺失形态(customer_list 无 email),收录收窄 BASE TABLE
> 后对账清偿,017/021 双双转绿;②多轮追问召回不继承上轮依赖表(mt_005 缺
> rental)→ recall 增历史继承路;③追问被文档通道劫持(泛系系词「是什么」+
> 文档命中即分流)→ 多轮门槛;④自愈喂回无分类(017 别名错同因连败)→
> 分类指令;⑤olist 聚合追问方差与 sakila_018 漏 DISTINCT 登记为靶点,
> **048 收口**(见下行,宽松/多轮双双回满)。**环境混杂教训**:08-23 知识
> AI 再生成与文档灌入均晚于 08-22 基线,其间提示词零变更亦漂移(宽松
> -4pp/多轮 -25pp)——基线纪律的触发面据此扩至知识/文档变更;两轮评估
> 之间亦须清 ctx 缓存(否则验证的是缓存不是 prompt)。
> **048 注记**:聚合追问护栏双层设防(understand 改写显式汇总口径+完整
> 复述集合构造条件;generate 规则 5 单行汇总)——首版仅单层时 mt_006
> 仍 1/3 过(集合被「最近的订单」替换),补构造条件复述后 3/3;规则 9
> 「完全重复」扩为组合重复对应关系后 sakila_018 转绿。
> **016 首基线说明**:评估集首轮跑出宽松 70% 后做了一轮口径修订(明细题
> 问句显式指定排序键——prompt 规则 6 已立约 gen 确定性排序,gold 裸 LIMIT
> 与 gen 排序集合天然错位属评估集自相矛盾;top-N 类问句点名值列消歧),
> 修订后宽松 84% 为首基线。smoke 5/5(含动态路径 1 题)。
> **失败归因抽样(全部为真实链路质量信号,未迁就)**:gen 统计题误加
> LIMIT 100(sakila_multiturn_003 t1,599 行全量 vs gen 截断 100)、gen 擅自
> `::numeric(3,2)` 舍入(olist_014)、gen 投影丢业务列/多投影 id 列
> (sakila_013/015 等)、指代集合消解错位(olist_multiturn_002 t2
> 「这些订单」)、gen 漏 DISTINCT(上游同款已知边界)。这些是后续
> prompt 护栏/query-streaming 域的量化靶点——基线的意义所在。
> 基线纪律(eval spec):评估方法、提示词、**表结构知识(收录/对账/AI 再
> 生成)或文档增删**变更后跑全量记录四组分数并标注原因;宽松(黄金指标)
> 劣化 ≥5pp 必须逐题溯源(区分评估集改动影响与 LLM 采样波动)后才可合入。

## 专项对照记录(worker 续读,add-worker-context-readthrough)

2026-11-04「派发摘要 vs 摘要+续读入口」对照(独立评估栈 18000/18001 载入
工作区现态代码,不经常驻栈;自建账号 evalread 可删,dev 库遗留评估
数据[触发器 2/任务 2/协作账本若干]可删; sakila 数据源,webhook HMAC
触发,confirm_auto 直派)。**不设通过阈值,只记数据**:

- 长目标臂(goal 2537 字,decomposition 拓扑 w1/w2,filter actions=["probe"]):
  12 步规划,3/12 步完成,227,848 tokens / 300,078ms,终态 FAILED(工具
  轮次上限+token 预算耗尽);协作 worker 结论/证据均落账本。
- 短目标臂(goal 35 字,同模板族):12 步规划,5/12 步完成,205,098
  tokens / 279,930ms,终态 FAILED(同款预算形态)。
- 两臂行为一致:goal 长度未改变规划步数与预算耗尽形态;截断提示按设计
  仅在摘要超限时附加。
- 观测缺口:`task_context` 续读端点无审计落库(B spec 未要求),worker
  是否实际调用无法回溯计数——需可观测时另行立项端点审计行。
- 附带发现:两任务尾部各 1 条 `invalid_transition`(FAILED→FAILED)噪音
  事件,定位为编排循环双重收口缺陷(service.finish 缺同态幂等收敛),
  已修复并补回归测试;本行数据采集于修复前。
