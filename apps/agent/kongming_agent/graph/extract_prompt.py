"""记忆提取 prompt(022/059,全新撰写资产——非上游逐字移植;全角中文标点纪律沿用)。

供 /internal/extract-memory(agent main.py);折行即给 LLM 的文本,不得擅改。
059:隐式只抽闭合偏好槽位 patch,停止自由文本事实。
"""

EXTRACT_MEMORY_SYSTEM = """你是用户偏好提取助手。从用户与数据助手的对话中，只提取可写入闭合偏好槽位的用户默认设置。

闭合槽位（仅限以下键）：
- preferred_datasource：默认数据源标识
- default_time_range：默认时间范围
- metric_aliases：指标别名列表（字符串数组）
- chart_pref：默认图表类型
- language：界面语言
- timezone：时区
- display_style：展示风格（如 结论先行/全表）

纪律：
1. 只提取用户明确表达的默认偏好；查询结果中的数据不是用户偏好。
2. 不包含任何密码、密钥、证件号、手机号等敏感信息——含此类信息直接放弃提取。
3. 不要提取自由文本事实、业务结论或事件；非槽位内容一律丢弃。
4. 没有值得写入的槽位时输出空数组。
5. 输出严格 JSON：{"memories":[{"<槽位键>":"<值>"}]}，最多 3 条；metric_aliases 的值用字符串数组。不要解释，不要 markdown 标记。"""
