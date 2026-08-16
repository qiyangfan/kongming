-- 只读角色(纵深防御第 6 层):业务库查询走只读账号。
-- 注意:rag schema 由 Alembic baseline 创建,其授权在 baseline DDL 内(时序依赖)。
CREATE ROLE km_readonly NOLOGIN;
GRANT CONNECT ON DATABASE kongming TO km_readonly;
GRANT USAGE ON SCHEMA public TO km_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO km_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO km_readonly;
