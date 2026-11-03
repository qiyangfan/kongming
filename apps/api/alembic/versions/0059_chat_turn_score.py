"""答案异步评分:新增 chat_turn_score 表(消息 1:1,评分三分+分解审计)。

职责分工见 spec「答案异步评分(分解与确定性重算)」:评审模型只产出
主张分解,分数由应用层确定性重算后落库。消息删除级联清评分。"""

from collections.abc import Sequence

from alembic import op

revision: str = "0059"
down_revision: str | Sequence[str] | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE public.chat_turn_score (
            id             UUID PRIMARY KEY,
            message_id     UUID NOT NULL UNIQUE
                           REFERENCES public.chat_message(id) ON DELETE CASCADE,
            score          SMALLINT NOT NULL CHECK (score BETWEEN 0 AND 100),
            grounding      SMALLINT NOT NULL CHECK (grounding BETWEEN 0 AND 100),
            coverage       SMALLINT NOT NULL CHECK (coverage BETWEEN 0 AND 100),
            decomposition  JSONB NOT NULL,              -- 主张分解(含净化剔除项,审计形态)
            score_model    VARCHAR(64) NOT NULL DEFAULT '',
            scored_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE public.chat_turn_score")
