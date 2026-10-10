"""0043: judge 运行记录两表(092 LLM-as-judge 评测智能体化)

- judge_run:一次 judge run 的编排汇总(对象类型 × rubric 版本 × 提示词指纹 ×
  通过率汇总 × 状态)——基线红线的对照单位(同类型同 rubric 版本相邻两次 run)。
- judge_result:逐对象结构化结果(verdict/分项维度/依据摘录/原始输出)——
  单对象独立收口,失败对象保留原始输出供排查。
- (object_type, object_id) 二元组不设业务外键:四类评审对象异构(report/task/
  collab/answer 分属不同业务表),外键会为每类各建列且跨域耦合;judge 是
  只读评审,对象删除后 judge 记录保留(评审历史不受业务删除牵连)。
"""

from sqlalchemy import text

from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            """
            CREATE TABLE public.judge_run (
                id UUID PRIMARY KEY,
                object_type VARCHAR(32) NOT NULL CHECK (
                    object_type IN ('report', 'task', 'collab', 'answer')
                ),
                rubric_version VARCHAR(32) NOT NULL,
                prompt_fingerprint VARCHAR(32) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'RUNNING' CHECK (
                    status IN ('RUNNING', 'COMPLETED', 'FAILED')
                ),
                total_objects INTEGER NOT NULL DEFAULT 0,
                pass_count INTEGER NOT NULL DEFAULT 0,
                borderline_count INTEGER NOT NULL DEFAULT 0,
                fail_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                pass_rate DOUBLE PRECISION,
                note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                finished_at TIMESTAMPTZ
            )
            """
        )
    )
    op.get_bind().execute(
        text(
            """
            CREATE TABLE public.judge_result (
                id UUID PRIMARY KEY,
                run_id UUID NOT NULL REFERENCES public.judge_run(id) ON DELETE CASCADE,
                object_id VARCHAR(64) NOT NULL,
                object_ref VARCHAR(200) NOT NULL DEFAULT '',
                verdict VARCHAR(16) NOT NULL CHECK (
                    verdict IN ('PASS', 'BORDERLINE', 'FAIL', 'ERROR')
                ),
                dimensions JSONB NOT NULL DEFAULT '{}'::jsonb,
                quotes JSONB NOT NULL DEFAULT '[]'::jsonb,
                raw_output TEXT,
                error TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
    )
    op.get_bind().execute(text("CREATE INDEX idx_judge_result_run ON public.judge_result(run_id)"))
    op.get_bind().execute(
        text(
            "CREATE INDEX idx_judge_run_type_version"
            " ON public.judge_run(object_type, rubric_version, created_at DESC)"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(text("DROP TABLE IF EXISTS public.judge_result"))
    op.get_bind().execute(text("DROP TABLE IF EXISTS public.judge_run"))
