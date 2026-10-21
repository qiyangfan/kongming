"""新增报告、不可变版本和章节树表。删除报告只标记 DELETED 并保留行供审计；版本历史通过新增版本保存。"""

from sqlalchemy import text

from alembic import op

revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None

DDL = """
CREATE TABLE public.report (
    id             UUID PRIMARY KEY,
    tenant_id      UUID NOT NULL,
    user_id        UUID NOT NULL,
    session_id     UUID,
    title          VARCHAR(200) NOT NULL,
    status         VARCHAR(16) NOT NULL
                   CHECK (status IN ('GENERATING','READY','PARTIAL','FAILED','DELETED')),
    phase          VARCHAR(16) NOT NULL DEFAULT 'PLANNING'
                   CHECK (phase IN ('PLANNING','FETCHING','WRITING','ASSEMBLING','DONE')),
    datasource_key VARCHAR(64) NOT NULL DEFAULT 'default',
    version        INTEGER NOT NULL DEFAULT 0,
    instruction    TEXT NOT NULL DEFAULT '',
    fail_reason    TEXT,
    usage_summary  JSONB,
    trace_id       VARCHAR(64),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    deleted_at     TIMESTAMPTZ
);
CREATE INDEX idx_report_owner ON public.report (tenant_id, user_id, created_at DESC);

CREATE TABLE public.report_version (
    id              UUID PRIMARY KEY,
    report_id       UUID NOT NULL REFERENCES public.report(id),
    version         INTEGER NOT NULL,
    meta            JSONB NOT NULL DEFAULT '{}',
    data_time_point TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      UUID NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (report_id, version)
);

CREATE TABLE public.report_section (
    id                 UUID PRIMARY KEY,
    report_version_id  UUID NOT NULL REFERENCES public.report_version(id),
    parent_id          UUID REFERENCES public.report_section(id),
    title              VARCHAR(200) NOT NULL,
    narrative_md       TEXT NOT NULL DEFAULT '',
    bindings           JSONB NOT NULL DEFAULT '[]',
    chart              JSONB,
    ord                INTEGER NOT NULL DEFAULT 0,
    data_status        VARCHAR(8) NOT NULL DEFAULT 'PENDING'
                       CHECK (data_status IN ('OK','MISSING','PENDING')),
    fail_reason        TEXT
);
CREATE INDEX idx_report_section_version ON public.report_section (report_version_id, ord);
CREATE INDEX idx_report_section_parent ON public.report_section (parent_id);
"""


def _statements(ddl: str) -> list[str]:
    """拆分前剥离 -- 行注释再按分号切(同 baseline/0035 约定)。"""
    no_comments = "\n".join(line.split("--", 1)[0] for line in ddl.splitlines())
    return [s.strip() for s in no_comments.split(";") if s.strip()]


def upgrade() -> None:
    for stmt in _statements(DDL):
        op.get_bind().execute(text(stmt))


def downgrade() -> None:
    for stmt in _statements(
        """
        DROP TABLE IF EXISTS public.report_section;
        DROP TABLE IF EXISTS public.report_version;
        DROP TABLE IF EXISTS public.report;
        """
    ):
        op.get_bind().execute(text(stmt))
