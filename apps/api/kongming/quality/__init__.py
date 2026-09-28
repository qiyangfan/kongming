"""quality 域(069-chat-quality-observability):轮次级问答质量观测面。

定位:线上真实质量今日不可观测——observability 域只看成本与调用,feedback
计数滞后且覆盖率低,ai-eval 只覆盖评估集,audit 域 NO_DATA 轮无执行无审计
行任何形态不可见。本域把「轮次终态的四个质量信号」(trace_id / retry_count /
resolved_question / prompt_version,随 069 迁移落 chat_message)聚合为两个
只读管理端点:总览(四态占比 + 趋势 + 数据源切片 + prompt 版本切片)与轮次
明细分页(NO_DATA 轮首次拥有可见形态)。

纪律(与 observability 域一致):管理面按租户/数据源聚合,不做行级按用户
拆分;非 data_scope=ALL 的查询方按请求租户收敛(越权不可见)。
"""
