# 实施任务

## 1. 拆分工具职责

- [x] 1.1 新增 `apps/api/kongming/escape_util.py`，实现只负责 LIKE 元字符转义的 `escape_like_literal`。
- [x] 1.2 新增 `apps/api/kongming/sql_util.py`，实现依赖 `escape_util` 的 `prefix_like_pattern`、`suffix_like_pattern` 和 `contains_like_pattern`，分别表达右侧、左侧和两侧通配符。
- [x] 1.3 更新 chat、datasource、store、system 仓储的导入与调用，删除 `apps/api/kongming/like.py`。
- [x] 1.4 确认现有四个仓储域仍调用包含模式，输出与旧函数一致。

## 2. 验证行为保持

- [x] 2.1 为转义函数覆盖反斜杠、`%`、`_` 及普通文本；为三种模式函数覆盖通配符方向、空字符串和转义后模式。
- [x] 2.2 运行 API 相关会话、数据源、用户和存储搜索测试，确认元字符按字面匹配且现有包含搜索不变。

## 3. 索引策略验证

- [x] 3.1 为每个拟优化查询记录选定的匹配模式、大小写/排序规则和候选索引类型；当前 `ILIKE` 查询不得仅因改成右侧通配符就宣称可使用 B-tree。
- [x] 3.2 使用代表性数据与搜索词对候选索引前后执行 `EXPLAIN (ANALYZE, BUFFERS)`；只有计划和访问成本证明有收益时才添加 Alembic 索引迁移。
- [x] 3.3 对短搜索词及无有效 trigram 的输入验证退化路径，确保优化不遗漏结果或改变匹配语义。

## 4. 验收

- [x] 4.1 运行 API 对应的 ruff、mypy 与 pytest 门禁，并通过严格 OpenSpec 校验。
