"""0007: query_history 回写治理(016 eval 域搭车,009③/014 O4 清偿)

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-21

writeback 由裸 INSERT 改幂等 UPSERT(同 (tenant_id, datasource_key, question)
覆盖保留最新成功 SQL)——消 014 O4(反馈纠错原 SQL 重复沉淀)与 009③(无去重)
挂账;同题多行会重复占据 few-shot 召回位。本迁移:
- 存量去重:同键多行保留 created_at 最新(幂等语义落地前提,否则唯一索引
  建不起来;生产环境该表在评估基线建立前写入量小,去重代价可忽略);
- 唯一索引 uq_query_history_tdq(tenant_id, datasource_key, md5(question))——
  UPSERT 的 ON CONFLICT 目标。**md5 表达式索引**:question 传输层上限
  2000 字符,中文 UTF-8 可达 6000 字节,超 btree 行上限(~2704 字节)会使
  超长问句回写炸索引(/goal R1 核验);md5 定长 32 字符彻底消除长度边界,
  去重语义不变(碰撞概率 2^128 忽略),UPSERT 用同表达式 ON CONFLICT。
列零新增(baseline 既有列)。手写迁移(与 0006 同因:autogenerate 不识别
vector/hnsw,且去重 DDL 非模式 diff)。
**downgrade**:仅删唯一索引,保留去重后的行(数据不回填重复行);重复行
  已永久消失,这是治理语义的既定方向,downgrade 仅在回退代码版本时使用。
"""

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
