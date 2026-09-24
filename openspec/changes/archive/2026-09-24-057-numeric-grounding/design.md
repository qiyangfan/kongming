# 057-numeric-grounding 设计

## Context

- `compute_stats`(nodes.py)已本地确定性产出 rows/列 max/min/avg,
  summarize 上下文含「行数+stats+前5行」(018)。
- SUMMARIZE_SYSTEM 已有「数值结论以所给统计与可见数据行为准,不得外推」,
  但**约束的是数据范围,不是计算正确性**。
- ReAct 内核已有工具表(toolcall/registry 由控制面过滤注入);派生量
  工具应与 validate_sql/execute_sql 同层。

## Goals / Non-Goals

**Goals:** 结论性数字可复算;派生量要么来自确定性计算,要么不存在。

**Non-Goals:** 见 proposal。

## Decisions

1. **白名单 = 数值多重集,不是字符串匹配**  
   允许集合由结果集全部数值单元格 + stats 数值字段 + compute 返回值
   构成;比较用「数值等价」(1 与 1.0 等价,百分数 0.25 与 25% 按
   容差映射后判定)。审计抽取 summary/insights 中的数字 token 时,
   百分数/千分位/科学计数先归一化再查白名单。

2. **审计是「剥离」不是「拒绝」**  
   不在白名单的数值所在句整体剥离(或替换为「详见结果表」),保留其余
   句;不触发 LLM 重试(避免流式尾延迟)。stats 与 columns/rows 字段
   零改动——审计只影响自然语言 summary/insights 文本。

3. **compute 工具入参显式、无隐式上下文**  
   `compute_delta(base, target)` → pct_change;`compute_share(part, total)`
   → ratio。入参必须是模型从结果/stats 取出的数字;工具内再断言入参 ∈
   本轮白名单(防编造基数二次污染)。返回值附带入参快照,便于复核。

4. **固定图路径不强制上工具**  
   主链路 summarize 无工具循环:stats 预计算提供「同列相邻行比率」
   (ordered by 时间列启发式失败则不产出)+ 已有 max/min/avg;不足则
   模型省略派生句。**宁省略,不编造** 是本变更唯一价值判据。

5. **trend/anomaly 收紧**  
   无 ≥3 期有序序列时禁止 trend;无与 avg 偏离的白名单值时禁止 anomaly
   定量表述(可保留 quality 类非定量提示)。

Schema 核对:无新表;无 baseline 列(ADR-004 无涉)。

## Risks / Trade-offs

- [审计误杀合法整数(如「前 5 行」的 5)] → 白名单补「结构性常量」
  (行数、前 N 预览行数、ROUND 位数);仍误杀则整句剥离的代价可接受
  (少说一句 > 说错一个数)。
- [百分数容差判定复杂] → 归一化到小数比率后 abs diff ≤1e-6 视为命中;
  未命中宁杀。
- [预计算环比启发式选错时间列] → 失败不产出该派生字段,不猜。
- [ReAct 场景工具往返增加延迟] → 仅模型主动调用;不强制,缺失即省略表述。

## Migration Plan

纯行为增强,无迁移、无开关。发布后抽样核对 summary 是否仍出现无白名单
数字(审计日志计剥离次数)。回滚 = 还原 summarize 后处理与 prompt 增量。

## Open Questions

(无)
