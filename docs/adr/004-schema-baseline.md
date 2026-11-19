# ADR-004: Schema 一次性整合为 Alembic Baseline

日期: 2026-08-16
状态: 已接受

## 背景:前系统迁移考古

前系统 50 个 Flyway 迁移中,**15 个(约 1/3)是纯补丁**,典型补丁链:

| 表                   | 补丁链(迁移号 → 动作)                                                       |
| -------------------- | --------------------------------------------------------------------------- |
| `sys_user`           | V1 建 → V6 +dept_id → V18 +external_id/source/email                         |
| `table_descriptions` | V3 建 → V13 补序列 → V16 +source_type/tsv → V20 +datasource_key、**删约束** |
| `chat_message`       | V19 建 → V23 +反馈4列 → V25 +状态机4列 → V43 +artifacts                     |
| `sys_tool_audit`     | V28 建 → V29 +result_json → V31 +trace_id → V49 +server_key                 |
| guardrail 规则       | V38 建 → V39 调 pattern → V40 改 charset → V44 hardening(**同一域补3次**)   |
| `sys_bot_channel`    | V34 加索引 → V35 改唯一约束                                                 |

这是迭代节奏下的正常熵增,但新项目不应继承中间态:建了又改、加了又删的列,
在 baseline 里应当以**最终形态**一次建全。

## 决策

1. **Alembic 单一 baseline**(`0001_baseline`):按最终形态整合核心域,
   不复刻 50 个迁移的历史。
2. 新表设计约定(吸取补丁史教训):
   - **主键 UUID v7**(应用侧生成,时间有序、无 JS 精度坑、免雪花 ID 基础设施);
   - **状态列 VARCHAR + CHECK**,禁止 SMALLINT 魔数(补丁史上最难审计的就是
     `status SMALLINT -- 1=正常 0=禁用` 这类注释依赖),也不用 PG ENUM
     (改枚举值要 ALTER TYPE,比加 CHECK 更痛);
   - 时间列统一 `created_at/updated_at TIMESTAMPTZ`;
   - 预见性冗余允许:反馈闭环、审计上下文等**已规划能力**(有 spec 的)
     在建表时一次到位,不留 V23/V25 式追补。
3. **schema 布局**:`public`(identity / chat / audit)+ `rag`(知识域),
   沿用前系统"业务域分 schema、向量隔离"的有效实践。
4. 前系统数据**不迁移**(全新平台);若未来要导,走独立 ETL 脚本,
   不进迁移链。

## 与前系统的明确差异

| 维度   | 前系统                                  | Kongming                                             |
| ------ | --------------------------------------- | ---------------------------------------------------- |
| 主键   | 雪花 BIGINT(前端 String 序列化防精度丢) | UUID v7                                              |
| 状态   | SMALLINT + 注释                         | VARCHAR + CHECK                                      |
| 审计列 | create_by/update_by/… 8 列模板          | created_at/updated_at(操作者在审计日志表,不在业务表) |
| 软删   | del_flag SMALLINT                       | `deleted_at TIMESTAMPTZ NULL`(仅 identity 域)        |
