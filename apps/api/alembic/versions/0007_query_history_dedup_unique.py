"""清理重复查询历史，并以 MD5 问句表达式建立幂等唯一索引，避免长问句超过索引键限制。降级只删除索引，不恢复已清理的重复行。"""

from sqlalchemy import text

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 存量去重:同键多行保留 created_at 最新(id 作并列 tiebreak 保确定性)
    op.get_bind().execute(
        text(
            """
            DELETE FROM rag.query_history a
            USING rag.query_history b
            WHERE a.tenant_id = b.tenant_id
              AND a.datasource_key = b.datasource_key
              AND a.question = b.question
              AND (a.created_at, a.id) < (b.created_at, b.id)
            """
        )
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_query_history_tdq"
        " ON rag.query_history (tenant_id, datasource_key, md5(question))"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS rag.uq_query_history_tdq")
