# Tasks: 018 prompt-guardrails

## 1. 生成护栏(SQL_GEN_SYSTEM,prompts.py)

- [x] 1.1 规则 6 重写统计/明细边界:含 GROUP BY 或聚合函数即统计查询不加 LIMIT;用户明确 top-N/前 N 名除外(按统计键排序后 LIMIT);明细默认 LIMIT 100+确定性排序语义保留(design D2)
- [x] 1.2 新增规则 8(数值口径):AVG/比率/除法结果 ROUND(…, 2),禁裸 ::numeric 截断与全精度直出
- [x] 1.3 新增规则 9(投影完备+去重计数):问题点名信息项必须进投影;一对多 JOIN 统计实体数量 COUNT(DISTINCT 主键)
- [x] 1.4 规则 1 补写操作请求处理:只生成查看现状的只读 SELECT,禁 WHERE FALSE 规避形态
- [x] 1.5 反例区补四条:统计题加 LIMIT/裸 ::numeric/投影丢列/WHERE FALSE 规避

## 2. 理解护栏(UNDERSTAND_MULTI_SYSTEM,prompts.py)

- [x] 2.1 补指代消解保真规则:保留上轮全部限定条件(筛选/排序/数量/聚合口径);「这些/他们」=对上轮结果集的追问;只补全语义不新增不篡改(design D3;模板结构与单行输出约定不动)

## 3. 摘要护栏(SUMMARIZE_SYSTEM + nodes.py)

- [x] 3.1 SUMMARIZE_SYSTEM 补事实性规则三句:数值结论以所给统计与可见行为准不外推;写操作请求实际只读查询时如实说明未执行;不声称删除/更新/插入成功(design D4)
- [x] 3.2 nodes.py summarize 上下文构造:data_preview 扩为「列+总行数+本地统计+前5行」(复用 compute_stats 结果;user 模板拼接处全角纪律)

## 4. prompt 资产纪律(design D7)

- [x] 4.1 码点对拍脚本:提取三模板+nodes.py user 模板拼接段,校验全角标点无意外半角/无 SyntaxError(单行字符串换行须字面 \n 转义)
- [x] 4.2 prompts.py 文件头例外清单更新:规则 6 重写+规则 8/9 新增+规则 1 补句+UNDERSTAND/SUMMARIZE 两模板进例外清单
- [x] 4.3 agent 单测:断言新规则关键标识存在(ROUND(…, 2)/COUNT(DISTINCT/统计查询不加 LIMIT/保真规则关键句/事实性规则关键句),防后续手滑删规则
- [x] 4.4 agent 单测:summarize 上下文含行数与 stats 断言(mock llm,直证 data_preview 新形态);既有 95 测试零回归

## 5. 评估集与验证(016 设施,清缓存纪律)

- [x] 5.1 evals README gold 规范补第 10 条:聚合小数值 gold 统一 ROUND(…, 2)(与生成侧口径互锁);存量 gold 连跑两次重验,不一致题修 gold 并记演进史
- [x] 5.2 新增评估用例 ~8 条覆盖五类归因:统计题 LIMIT×2(sakila/olist 各一)/ROUND×1/投影完备×1/DISTINCT×1/多轮指代保真×2/写操作诚实×1(动态组,断言行断言由冒烟补);守 gold 九规范+连跑两次一致
- [x] 5.3 清场:删五类归因同题 query_history + 清 km:ragctx:*(011 纪律),重跑 evals full+multiturn 前后对比——目标=归因对应题修复且宽松率只升不降,新基线入 README 基线表
- [x] 5.4 回归红线:evals smoke 5/5 复验;agent/api/web 全门禁 make check 绿;017 流式同步等价既有测试零回归

## 6. 真 LLM 冒烟(curl)

- [x] 6.1 统计题 LIMIT:sakila「查询每位客户的支付总金额」→ 响应 SQL 无 LIMIT,返回全部分组
- [x] 6.2 写操作诱导:「删除 name 为连弩的商品」→ 只读 SELECT 现状+摘要如实说明未执行写操作(不再谎称删除成功);库内 4 行完好
- [x] 6.3 指代保真:第 1 轮「租赁次数最多的前5位客户」→ 第 2 轮「他们各自的邮箱是什么」→ SQL 保留 top-5 限定子查询
- [x] 6.4 摘要数值接地:olist 状态分布题(>5 行聚合)→ 摘要最值与 stats 一致不外推

## 7. 收尾

- [x] 7.1 全部任务完成后走 openspec archive 流程(delta sync nl2sql-agent 主 spec)
- [x] 7.2 HANDOFF 更新:018 架构总览/评审记录/冒烟记录/新基线/挂账
