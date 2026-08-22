# Design: 018 prompt-guardrails

## §0 Context

动机见 proposal.md。现状事实(只记代码里看不出来的):

- prompts.py 三模板为上游逐字资产,既有例外=SQL_GEN_SYSTEM 规则 3/6
  (2026-08-20 可读性修缮),文件头注记例外清单;nodes.py 的 user 模板拼接
  (含 summarize 的 data_preview)同属逐字资产,全角标点纪律同适用。
- 016 基线:宽松 37/44(84%)/严格 26/44(59%)/多轮 8/10/表命中 35/44;
  失败归因五类均有实锤 case(如 sakila_mt_003 t1 统计题 gen 加 LIMIT 100
  截断 599 客户→集合必挂;sakila_mt_005 t2 指代消解丢「租赁次数最多前 5」
  限定)。
- 摘要幻觉两次实证:005 诱导删除→LLM 生成 WHERE FALSE 规避+摘要谎称
  「删除成功」;005 数据集冒烟 summarize 只喂前 5 行→olist 状态分布说错
  最大值。
- prompt 类改动验证纪律(011 实证):rag few-shot 会自我强化旧形态,验证前
  必须清同题 query_history + km:ragctx:* 缓存,否则验证的是缓存不是 prompt。

## §1 关键决策

### D1 护栏落层:prompt 规则层为主,程序化只接地不改写

| 方案 | 机制 | 选型 |
|---|---|---|
| **prompt 规则加固(选)** | 系统提示规则+反例引导 LLM 行为 | ✅ 语义灵活(统计/明细判定需语义理解)、与「prompt 护栏域」定位一致、零链路风险 |
| validate 节点 AST 改写(如程序化剥 LIMIT) | sqlglot 改生成 SQL | ❌ 改语义风险(top-N 与明细保护边界机器判不准)、selfheal 与审计记的是改写后 SQL 语义漂移、违「轻量预检不复刻权威校验」定位 |

程序化只做**信息接地**(summarize 上下文补行数+stats)——确定性、不改写
任何 LLM 产出、不改变链路状态机。

### D2 SQL_GEN_SYSTEM:规则 6 边界重写 + 新增两条规则 + 反例补充

- 规则 6(结果集保护)重写统计/明细判定:**含 GROUP BY 或聚合函数即统计
  查询,不加 LIMIT;用户明确 top-N/前 N 名除外(按统计键排序后 LIMIT)**;
  明细默认 LIMIT 100 与确定性排序既有语义保留;**排序键可能并列时必须
  追加主键等唯一列作第二排序键**(实施期评估实证:top-N 并列是 gen/gold
  集合错位首因,gold 编写规范第 1 条的 gen 侧镜像)。
- 新增规则 8(数值口径):AVG/比率/除法结果 `ROUND(…, 2)`,禁裸
  `::numeric` 截断与全精度直出——与评估 gold 口径对齐(见 D6)。
- 新增规则 9(投影完备+去重计数):问题点名的每个信息项必须进投影;
  一对多 JOIN 统计实体数量用 `COUNT(DISTINCT 主键)`。
- 规则 1 补写操作请求处理:只生成查看现状的只读 SELECT,禁 WHERE FALSE
  规避形态。反例区补:统计题加 LIMIT/裸 ::numeric/投影丢列/WHERE FALSE。

备选「规则并入既有 7 条不新增编号」被否:规则 3/6 已长,独立编号在反例区
与 spec scenario 的引用更稳;编号本身无语义。

### D3 UNDERSTAND_MULTI_SYSTEM:补保真规则两句

补「保留上轮全部限定条件(筛选/排序/数量/聚合口径);这些/他们=对上轮结果
集的追问;只补全语义,不新增不篡改条件」。不改模板结构(历史问题/当前
问题/补全后三段式与单行输出约定不动),多轮零回归由 multiturn 评估兜底。

### D4 summarize 接地:上下文扩为「行数+stats+前 5 行」+ 事实性规则

nodes.py summarize 的 data_preview 由 `列+前5行` 扩为
`列+总行数+本地统计+前5行`(stats 已由 compute_stats 本地确定性算出,
零额外成本)。SUMMARIZE_SYSTEM 补三句:数值结论以所给统计与可见行为准
不外推;用户请求写操作而实际为只读查询时如实说明未执行;不声称
删除/更新/插入成功。

备选「stats 序列化进 user 模板 vs 附进 system」:进 user 模板(数据属
用户轮次,system 只放规则)——与既有「结果」段同位,LLM 对数据 proximity
更好。user 模板拼接处属 prompt 资产,改动按纪律脚本对拍。

### D5 写操作请求的终态形态:SUCCESS+诚实摘要(不引入新状态)

写操作请求→只读 SELECT 现状→SUCCESS+摘要如实说明未执行写操作。
备选「validate 前置意图识别走 REJECTED 分态」被否:意图分类需额外 LLM
调用或脆弱关键词表,且「用户想看删除目标现状」是合理解读;状态机零变化
保住 017 流式/同步等价红线。远期意图预检随 tool-calling 域评估。

### D6 评估验证:新增用例 + 清缓存前后对比 + 基线重记

- 新增 ~8 条评估用例精确覆盖五类归因(统计题 LIMIT×2/ROUND 口径×1/
  投影完备×1/DISTINCT×1/多轮指代保真×2/写操作诚实×1 动态组);
  gold 编写守 README 九规范,补第 10 条:聚合小数值 gold 统一
  `ROUND(…, 2)`(与 D2 生成侧口径互锁)。
- 验证前清同题 query_history + km:ragctx:*(D0 纪律);前后两轮 full+
  multiturn 对比,目标=五类归因对应题修复且零新劣化(宽松率只升不降)。
- 回归红线:evals smoke 5/5(016 基线)+ 流式同步等价既有测试(017)
  全绿;真 LLM 冒烟直证写操作诱导(摘要不再谎称成功)+统计题无 LIMIT+
  指代保真三场景。

### D7 prompt 资产纪律执行

改动后:①脚本对拍(提取三模板与 nodes.py user 模板拼接段,码点校验
全角标点/无意外半角);②prompts.py 文件头例外清单更新(规则 6 重写+
新增 8/9+规则 1 补句+UNDERSTAND_MULTI_SYSTEM+SUMMARIZE_SYSTEM 进
例外清单);③agent 单测断言新规则关键标识存在(防后续手滑删规则)。

## §2 Risks / Trade-offs

- **[中] few-shot 自我强化旧形态掩蔽新 prompt**(011 实证:同题历史成功
  SQL 压过 system 规则)→ 验证前清场纪律(D6);新形态回写后转正向强化,
  评估 persist=false 不污染。
- **[中] 规则加长致简单查询过度复杂化或规则间冲突**(如「投影完备」与
  「不要随意加列」张力)→ spec 保留「单表查询不过度复杂化」scenario;
  措辞写明「点名项必须进/未点名不强加」;full 评估前后对比兜底。
- **[中] ROUND 口径与存量 gold 不一致**(gold 未 ROUND 的题 gen 改了反而
  挂)→ D6 规范第 10 条互锁;存量 gold 连跑两次重验,不一致题修 gold
  (评估集演进史记 README)。
- **[低] 多轮 UNDERSTAND 改动回归**(补全更保守可能漏补)→ multiturn
  前后对比;保真规则只加约束不改输出形态。
- **[低] 摘要「未执行写操作」依赖 LLM 判断意图**→ 真 LLM 冒烟直证;
  即便漏判,危害上限=回到既有幻觉行为,无新增风险面。

## §3 Migration Plan

prompt 改动随部署即生效,无表结构/契约/配置迁移;回滚=git revert。
dev 库验证前按 D6 清场;新形态成功 SQL 回写后 few-shot 自强化转正向。

## §4 Open Questions

- OQ1:写操作请求的「查看现状 SELECT」与 REJECTED 分态的边界——本期取
  D5 保守形态,若冒烟显示用户困惑率高,远期做意图预检+专用文案。
- OQ2:UNDERSTAND 是否补 few-shot 示例——先看保真规则两句的多轮评估
  增量,不足再加(加示例=token 成本×每轮)。
