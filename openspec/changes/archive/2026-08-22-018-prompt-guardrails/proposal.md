# 018 prompt-guardrails — prompt 质量护栏(生成/理解/摘要三层规则加固)

## Why

016 评估体系落地后首次有了量化质量基线:宽松 37/44(84%)、严格 26/44(59%)、
多轮 8/10、表命中 35/44,失败归因五类——①统计题误加 LIMIT(GROUP BY 聚合
被判成"明细"截断 100 行);②`::numeric` 舍入口径不一致(gen 原生精度 vs gold
ROUND 两位);③投影丢列(问题点名的信息项未进 SELECT);④指代消解错位(多轮
"他们/这些"补全时丢失上轮限定条件);⑤漏 DISTINCT(一对多 JOIN 后计数膨胀)。
另有 005/006 两次冒烟实证的两类既有观察:摘要幻觉(summarize 只喂前 5 行,
聚合结果以偏概全;诱导写操作时摘要谎称"删除成功")与规避写操作(LLM 生成
WHERE FALSE 假完成)。这些全是 prompt 层质量问题,016 评估集提供了前后对比
的量化验证手段——现在修,可度量。

## What Changes

- **生成护栏(SQL_GEN_SYSTEM 规则加固)**:统计/明细判定边界显式化(含
  GROUP BY/聚合函数即统计查询,不加 LIMIT,用户明确 top-N 除外);平均值/比率
  等除法结果统一 `ROUND(…, 2)`;投影完备性(问题点名的每个信息项必须进
  SELECT);一对多 JOIN 统计实体数量用 `COUNT(DISTINCT 主键)`;用户请求写
  操作时只生成查看现状的只读 SELECT,禁止 WHERE FALSE 规避形态。反例区
  同步补充。
- **理解护栏(UNDERSTAND_MULTI_SYSTEM 加固)**:指代消解保真规则——补全
  时保留上轮全部限定条件(筛选/排序/数量),"这些/他们"解析为对上轮结果集
  的追问,只补全语义不新增条件。
- **摘要护栏(SUMMARIZE_SYSTEM + summarize 节点接地)**:摘要只陈述结果集
  与所给统计可见的事实;数值结论以本地确定性统计为准;用户请求写操作而
  实际执行的是只读查询时,必须如实说明未执行写操作。summarize 节点喂给
  LLM 的上下文从前 5 行扩为「行数+本地统计+前 5 行」,消除以偏概全的
  信息根源。
- **评估验证**:评估集补针对五类失败归因的用例(统计题 LIMIT/ROUND 口径/
  投影完备/DISTINCT/多轮指代);清理同题 query_history 与 ragctx 缓存后
  (prompt 类改动验证纪律,011 教训)重跑 full+multiturn 前后对比,新基线
  入 README;017 流式同步等价复验(流式回归红线);真 LLM 冒烟直证摘要
  幻觉与写操作诱导两场景修复。
- **prompt 资产纪律**:prompts.py 文件头注记更新(规则例外清单随本变更
  扩展);改动经脚本对拍防全角标点手滑。

## Non-goals

- **安全向 prompt 护栏**(上游 030 形态:prompt injection 检测/PII 脱敏/
  输出审核/sys_guardrail_audit 表/护栏规则管理端点):上游自身将其标注为
  tool-calling(022)开放后的伴生需求,Kongming 无 tool-calling 攻击面;
  本变更只做质量护栏,安全护栏随 tool-calling 域另立变更。
- **评估匹配规则变更**:宽松/严格双路径与容差口径不动(016 spec 既定);
  失败靠 prompt 修复,不放宽匹配器迁就。
- **agent 图拓扑/端点/流式协议变更**:节点结构与 017 事件协议零变化;
  同步 /query 与 /query_stream 行为一致性为回归红线。
- **web/api 变更**:零前端改动、零契约变化、零新端点、零表结构变更。
- **LLM 输出兜底解析改造**:clean_sql/parse_summary_json 既有容错不动。

## Capabilities

### New Capabilities

(无——全部落在既有能力域的规则加固。)

### Modified Capabilities

- `nl2sql-agent`: 「SQL 生成质量规则」扩展(统计题 LIMIT 纪律/ROUND 口径/
  投影完备/DISTINCT/写操作请求诚实处理);「多轮上下文链路打通」补指代
  消解保真要求;「结果智能分析」补摘要事实性约束与统计接地要求。

## Impact

- **apps/agent**(唯一代码面):graph/prompts.py 三模板规则加固(prompt
  资产纪律:脚本对拍+文件头例外清单更新);graph/nodes.py summarize
  上下文构造(行数+stats 接地,user 模板拼接处);单测补强(新规则
  关键标识断言+stats 入上下文断言)。
- **apps/api**:零代码变更;evals/ 评估集 yaml 新增用例 + README 基线
  记录更新(评估资产,不进产品)。
- **验证面**:evals full+multiturn 前后对比(清缓存纪律);evals smoke
  复验;017 流式等价冒烟;真 LLM 场景冒烟(摘要幻觉/写操作诱导/统计题
  LIMIT/指代消解)。
- **契约/依赖/表结构**:零变化。
