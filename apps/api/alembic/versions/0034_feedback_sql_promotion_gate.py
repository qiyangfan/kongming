"""070-feedback-sql-promotion-gate:query_history 来源/反馈关联/召回启用态。

纠错 SQL 此前提交即入 query_history(可召回),管理员驳回也无法撤回;本迁移
把样本准入改为「审核后晋升」:
- source_type:EXECUTION(执行成功样本)|FEEDBACK(审核晋升的纠错样本)|
  LEGACY(来源无法证明的存量行——退出召回但保留);
- feedback_message_id:FEEDBACK 样本的反馈消息关联(同问句异来源并存的
  幂等键;重复提交停用旧样本);
- recall_enabled:召回启用态(非 ADOPTED 的反馈样本、LEGACY 行=false)。
- 原唯一索引 uq_query_history_tdq 收窄为仅约束执行成功样本(部分索引),
  新增反馈 messageId 幂等索引——同问句可同时保留执行样本与纠错样本。
- 存量治理:能从成功对话(问句+sql_text 精确匹配 SUCCESS assistant 消息)
  证明的执行样本保留召回;能匹配当前反馈(chat_message.corrected_sql)
  的按处置状态恢复(ADOPTED 启用,其余停用);均无法证明的标记 LEGACY 停用。

Revision ID: 0034
Revises: 0033
"""

from sqlalchemy import text

from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rag.query_history"
        " ADD COLUMN source_type VARCHAR(16) NOT NULL DEFAULT 'EXECUTION'"
        " CHECK (source_type IN ('EXECUTION', 'FEEDBACK', 'LEGACY'))"
    )
    op.execute("ALTER TABLE rag.query_history ADD COLUMN feedback_message_id UUID")
    op.execute(
        "ALTER TABLE rag.query_history ADD COLUMN recall_enabled BOOLEAN NOT NULL DEFAULT TRUE"
    )
    # 原唯一索引收窄:仅约束执行成功样本(执行样本同问句仍幂等覆盖)
    op.execute("DROP INDEX IF EXISTS rag.uq_query_history_tdq")
    op.execute(
        "CREATE UNIQUE INDEX uq_query_history_tdq_exec"
        " ON rag.query_history (tenant_id, datasource_key, md5(question))"
        " WHERE source_type = 'EXECUTION'"
    )
    # 反馈样本幂等键:同一反馈消息至多一条样本(重复提交停用旧样本后重晋)
    op.execute(
        "CREATE UNIQUE INDEX uq_query_history_feedback_msg"
        " ON rag.query_history (tenant_id, feedback_message_id)"
        " WHERE source_type = 'FEEDBACK' AND feedback_message_id IS NOT NULL"
    )
    # 召回查询过滤索引(tenant+ds+启用态;既有 hnsw/embedding 索引不动)
    op.execute(
        "CREATE INDEX idx_query_history_recall"
        " ON rag.query_history (tenant_id, datasource_key)"
        " WHERE recall_enabled AND status = 'SUCCESS'"
    )
    # 存量治理(会话消息表同库;chat_message.question=user 问句,sql_text=assistant SQL)
    op.get_bind().execute(
        text(
            """
            -- 能证明的反馈样本:匹配 chat_message.corrected_sql 的存量行
            UPDATE rag.query_history q SET
                source_type = 'FEEDBACK',
                feedback_message_id = m.id,
                recall_enabled = (m.feedback_status = 'ADOPTED')
            FROM public.chat_message m
            WHERE m.corrected_sql IS NOT NULL
              AND m.corrected_sql = q.sql_text
              AND q.question = (
                SELECT COALESCE(
                  (SELECT u.question FROM public.chat_message u
                    WHERE u.session_id = m.session_id
                      AND u.role = 'USER' AND u.created_at < m.created_at
                    ORDER BY u.created_at DESC LIMIT 1),
                  m.question)
              )
              AND q.tenant_id = (
                SELECT s.tenant_id FROM public.chat_session s WHERE s.id = m.session_id
              )
            """
        )
    )
    # 无法证明的剩余行(既非反馈匹配、亦无成功对话佐证)→ LEGACY 停用
    op.get_bind().execute(
        text(
            """
            UPDATE rag.query_history q SET
                source_type = 'LEGACY',
                recall_enabled = FALSE
            WHERE q.source_type = 'EXECUTION'
              AND NOT EXISTS (
                SELECT 1 FROM public.chat_message m
                JOIN public.chat_session s ON s.id = m.session_id
                WHERE m.role = 'ASSISTANT' AND m.status = 'SUCCESS'
                  AND m.sql_text = q.sql_text
                  AND s.tenant_id = q.tenant_id
                  AND EXISTS (
                    SELECT 1 FROM public.chat_message u
                    WHERE u.session_id = m.session_id AND u.role = 'USER'
                      AND u.question = q.question AND u.created_at < m.created_at
                  )
              )
            """
        )
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS rag.idx_query_history_recall")
    op.execute("DROP INDEX IF EXISTS rag.uq_query_history_feedback_msg")
    op.execute("DROP INDEX IF EXISTS rag.uq_query_history_tdq_exec")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_query_history_tdq"
        " ON rag.query_history (tenant_id, datasource_key, md5(question))"
    )
    op.execute("ALTER TABLE rag.query_history DROP COLUMN recall_enabled")
    op.execute("ALTER TABLE rag.query_history DROP COLUMN feedback_message_id")
    op.execute("ALTER TABLE rag.query_history DROP COLUMN source_type")
