"""evalj 域(092 评测智能体化):LLM-as-judge 运行记录持久层。

对象类型/verdict 取值域与汇总/基线对比纯函数见 models;judge 编排在评估
harness(apps/api/evals/judge),本包只承载落库结构与读写(D1:judge 不进
业务链路,api 侧仅持久化)。
"""
