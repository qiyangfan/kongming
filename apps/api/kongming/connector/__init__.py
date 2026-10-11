"""connector 域包(099 http-connector-domain):租户级 HTTP 连接器。

分层(AGENTS.md 域包纪律):router(适配)→ service(用例)→ models(领域)
→ repository(基础设施);领域代码零框架依赖,持久化经 ports.Protocol 接口
反转,组合根在 router。
"""
