"""datasource 域包(008 动态数据源):注册/管理/凭证加密/动态路由支撑。

宪法分层:models(零框架领域)→ ports(Protocol 反转)→ service(用例)
→ repository/cipher/tester(SQLAlchemy/cryptography/asyncpg 基础设施)
→ router(/system/datasource/* 薄适配)。
"""

