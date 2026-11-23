# Tasks

## 1. 测试先行

- [x] 1.1 意图解析:knowledge/fused 保留 subqueries、data 丢弃、上限 3、
  去重与非字符串项过滤;旧 JSON(无 subqueries)照常解析。
- [x] 1.2 retrieve:有子查询时原问句优先、并行补检、按引用键合并去重、
  上限 8;无子查询时只调用一次且 doc_hints 不变;子查询 None/异常时降级。

## 2. 实现

- [x] 2.1 意图路由提示与解析增加可选 `subqueries`;最大输出 token 200→400。
- [x] 2.2 state 增加 `doc_subqueries`;retrieve 读取并执行并行补检与合并。
- [x] 2.3 doc_answer/fused_answer 文档段注入上限 5→8(单查询路径仍为 5)。

## 3. 验收

- [x] 3.1 agent 定向测试与全量测试通过。
- [x] 3.2 实验室判别复跑:同文档多跳 ≥5/6、单跳 1.000、跨文档组只报告。
- [x] 3.3 链路等价:无子查询路径的调用次数与 doc_hints 逐项一致。

## 4. 收口

- [x] 4.1 `openspec validate --all --strict` 通过;HANDOFF/评测文档回写。
