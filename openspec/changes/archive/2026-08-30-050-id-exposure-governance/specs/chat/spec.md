# chat spec delta(050-id-exposure-governance)

## ADDED Requirements

### Requirement: 结果统计呈现

查询结果的统计徽标 SHALL 面向用户呈现可读文案:控制面在落库与返回前 SHALL 将
统计键映射为用户可读形态——行数键呈现「行数」;「列名_max/_min/_avg」形态键在
列名唯一命中结果列时,SHALL 以该列的呈现层列标签(041 双轨制单源)映射为
「{列标签} 最高/最低/均值」;列名歧义或未命中时 SHALL 保留原键(不劣化回退)。
读档回显路径 SHALL 对存量档再应用一次映射(幂等:已映射键与非统计形态键原样
保留),存量英文键自然升级,SHALL NOT 要求数据回填。统计徽标呈现 SHALL NOT 暴露
内部标识列/期间列的统计(资格过滤在 Agent 面统计生成处完成,见 nl2sql-agent
「结果智能分析」)。

#### Scenario: 统计键经列标签中文化
- GIVEN 结果列 inventory_count 带列标签「库存数量」,stats 含 inventory_count_max=181
- WHEN 结果落库与返回
- THEN 徽标呈现「库存数量 最高 181」,SHALL NOT 出现 inventory_count_max 原键

#### Scenario: 行数键中文化
- GIVEN stats 含 rows 键
- WHEN 结果落库与返回
- THEN 呈现「行数 {n}」

#### Scenario: 列未命中保留原键
- GIVEN stats 含某键而键前缀在结果列中无对应列(异源键/畸形档)
- WHEN 应用键映射
- THEN SHALL 保留原键(不劣化回退)

#### Scenario: 存量档读档归一(幂等)
- GIVEN 历史消息 stats 含英文键 inventory_count_max(落库早于本变更)
- WHEN 回显渲染
- THEN 读档映射后呈现中文形态,无需数据回填;已中文化键 SHALL NOT 被二次改写
