"""0048: 知识条目团队归属(102-knowledge-team-scope)

rag.table_descriptions 纯增 team_id(UUID NULL,REFERENCES public.sys_team
ON DELETE SET NULL):NULL=租户公共(存量行全 NULL=零回归),非 NULL=团队专属。
- ON DELETE SET NULL 的裁决:团队删除钩子(team 域 KnowledgePublicizer)
  先行置空+审计;钩子失败不阻塞删除(design D4)——FK 级联 SET NULL 兜底
  公共化,知识资产不因团队解散丢失(级联置空而非级联删除)。
- 唯一键保持四元不变(单一实例,归属转移为原位变更,禁公共/团队双版本)。
**downgrade**:删索引+删列(归属维度随列消失,条目内容不动)。
"""

from alembic import op

revision = "0048"
down_revision = "0047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rag.table_descriptions"
        " ADD COLUMN team_id UUID REFERENCES public.sys_team(id) ON DELETE SET NULL"
    )
    op.execute("CREATE INDEX idx_table_desc_team ON rag.table_descriptions (team_id)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS rag.idx_table_desc_team")
    op.execute("ALTER TABLE rag.table_descriptions DROP COLUMN IF EXISTS team_id")
