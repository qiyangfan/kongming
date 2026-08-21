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
```

环境变量:`KM_EVAL_API_BASE`(缺省 127.0.0.1:8000)/`KM_EVAL_ADMIN_USERNAME`
(缺省 admin)/`KM_EVAL_ADMIN_PASSWORD`(**必填**,缺失整层 skip)/
`KM_EVAL_PG_DSN`(缺省自控制面 DSN 派生,gold 直连执行)。

默认门禁(`pytest -q`/make check/CI)只收 tests/,评估须显式指定 `evals`
目录+分层运行(eval spec「评估运行形态」);匹配器纯函数单测在
`tests/test_eval_matchers.py` 进默认门禁。

**评估重复性口径**:评估以 persist=false 运行——不落会话/消息,且不触发
query_history 回写(回写挂 persist 条件,测试通道不沉淀),dev 库零污染。
但历史冒烟/人工问答的沉淀仍在:prompt 类改动后重跑评估前,先清同题沉淀
与 ctx 缓存(011 教训——否则验证的是缓存不是 prompt):

```bash
podman exec kongming-postgres-1 psql -U kongming -d kongming \
  -c "DELETE FROM rag.query_history WHERE question LIKE '%影片%'"
podman exec kongming-redis-1 redis-cli --scan --pattern 'km:ragctx:*' | xargs -r -n1 podman exec -i kongming-redis-1 redis-cli DEL
```

## 评估集分层

| 文件 | 内容 |
|---|---|
| `sakila.yaml` | sakila 单轮 20 题(smoke ×2) |
| `olist.yaml` | olist 单轮 16 题(smoke ×2) |
| `sakila_multi_turn.yaml` / `olist_multi_turn.yaml` | 多轮 turns[](6+4 case) |
| `datasource.yaml` | 动态数据源(sakila_dyn 显式 key ×3 + 跨租户拒绝 ×1) |

用例字段:`id`(tenant 前缀推断租户)/`question`/`gold_sql`/
`expected_tables`/`category`;可选 `tenant`/`datasource_key`(动态路径)/
`expect_reject`(预期拒绝,无 gold_sql)/`smoke`。

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

## 辅助指标

- **表命中(Recall 间接口径)**:生成 SQL 涉及表(sqlglot 抽表,CTE 排除)
  覆盖 expected_tables——表选对是召回生效的链路可见结果。
- **Valid Execution**:链路 success(生成 SQL 在链路内真实执行成功)。
- **Retry Rate**:响应 retry_count(selfheal 触发情况)。

## 基线记录(016 起)

| 日期 | 变更 | 严格 | 宽松 | 多轮 | 动态数据源 | 表命中 |
|---|---|---|---|---|---|---|
| 2026-08-21 | 016 首基线(评估集落地,问句排序对齐修订后) | 26/44 (59%) | 37/44 (84%) | 7/10 (70%) | 4/4 | 35/44 (80%) |
| 2026-08-21 | /goal 修复轮:多轮裸 LIMIT 口径补齐 3 case(olist_mt_002/sakila_mt_002/006,单轮题同款口径遗漏) | — | — | 8/10 (80%) | — | — |

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
>
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
> 基线纪律(eval spec):评估方法或提示词变更后跑全量记录四组分数并标注
> 原因;宽松(黄金指标)劣化 ≥5pp 必须逐题溯源(区分评估集改动影响与
> LLM 采样波动)后才可合入。
