"""0052: query_history 可见性(102-knowledge-team-scope 伴生决策)

团队条目引出的成功 SQL 回写 query_history 后,历史路召回(history_sql
few-shot)不对团队过滤——非成员问相近问题,团队口径的 SQL 会进其提示段
(泄露表名/列用法/业务口径)。本迁移给样本行补来源可见性:

- `visible_teams text[] NOT NULL DEFAULT '{}'`:产生该样本的召回可见集合
  (团队 id 文本数组);空数组=公共可见(公共条目/无归属来源)。
- 存量回填由 DEFAULT 覆盖为 '{}'(=公共):存量行来源不可追溯,按公共
  处理是保守可见方向上的既有行为延续(知识库团队归属启用前无团队来源);
  dev 库知识为空,无实际暴露面。
- 召回过滤(应用层,repository.recall_history):调用者可见集合与行集合
  无交集且行非公共 → 不进 few-shot;无身份调用者只见公共样本——与条目
  三路召回同判据(102「可见集合单点收敛」的第五处)。

**downgrade**:仅删列(样本行保留;可见性语义随列消失,回退代码版本时
历史路回到租户内全量召回的旧行为)。
"""

from alembic import op

revision = "0052"
down_revision = "0051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE rag.query_history ADD COLUMN visible_teams text[] NOT NULL DEFAULT '{}'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE rag.query_history DROP COLUMN IF EXISTS visible_teams")
