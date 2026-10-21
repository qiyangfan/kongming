"""增加邮件触发器配置和邮件 UID 游标。凭证加密保存，运行记录只保留截断后的摘要，不存邮件原文；降级会删除邮件触发器配置与游标。"""

from sqlalchemy import text

from alembic import op

revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    b = op.get_bind()
    # 邮件配置列(明文配置 + 加密凭证;凭证列语义与 secret_cipher 同源)
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " ADD COLUMN imap_host VARCHAR(255) NOT NULL DEFAULT '',"
            " ADD COLUMN imap_port INTEGER NOT NULL DEFAULT 993,"
            " ADD COLUMN imap_tls BOOLEAN NOT NULL DEFAULT TRUE,"
            " ADD COLUMN imap_user VARCHAR(255) NOT NULL DEFAULT '',"
            " ADD COLUMN imap_credential TEXT NOT NULL DEFAULT '',"
            " ADD COLUMN imap_folder VARCHAR(255) NOT NULL DEFAULT 'INBOX',"
            " ADD COLUMN imap_poll_interval_sec INTEGER NOT NULL DEFAULT 300"
        )
    )
    # 轮询间隔正值域(秒数下限封顶在服务层,DDL 只拒非正)
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " ADD CONSTRAINT ck_sys_trigger_def_poll_interval"
            " CHECK (imap_poll_interval_sec >= 1)"
        )
    )
    # 触发器形态域扩展 EMAIL
    b.execute(
        text("ALTER TABLE public.sys_trigger_definition DROP CONSTRAINT ck_sys_trigger_def_type")
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " ADD CONSTRAINT ck_sys_trigger_def_type"
            " CHECK (type IN ('CRON','WEBHOOK','EMAIL'))"
        )
    )
    # run 行标准化摘要列(邮件原文不落库,只留白名单摘要)
    b.execute(
        text("ALTER TABLE public.sys_trigger_run ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
    )
    # UID 游标小表(触发器 id+文件夹唯一)
    b.execute(
        text(
            "CREATE TABLE public.sys_trigger_email_cursor ("
            "    id          UUID PRIMARY KEY,"
            "    trigger_id  UUID NOT NULL,"
            "    folder      VARCHAR(255) NOT NULL,"
            "    last_uid    BIGINT NOT NULL DEFAULT 0,"
            "    uidvalidity BIGINT NOT NULL DEFAULT 0,"
            "    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),"
            "    CONSTRAINT ux_sys_trigger_email_cursor UNIQUE (trigger_id, folder)"
            ")"
        )
    )


def downgrade() -> None:
    b = op.get_bind()
    b.execute(text("DROP TABLE IF EXISTS public.sys_trigger_email_cursor"))
    b.execute(text("ALTER TABLE public.sys_trigger_run DROP COLUMN IF EXISTS summary"))
    # EMAIL 行随形态回退移除(列同删,行不留悬挂形态)
    b.execute(text("DELETE FROM public.sys_trigger_definition WHERE type='EMAIL'"))
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " DROP CONSTRAINT IF EXISTS ck_sys_trigger_def_type"
        )
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " ADD CONSTRAINT ck_sys_trigger_def_type CHECK (type IN ('CRON','WEBHOOK'))"
        )
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " DROP CONSTRAINT IF EXISTS ck_sys_trigger_def_poll_interval"
        )
    )
    b.execute(
        text(
            "ALTER TABLE public.sys_trigger_definition"
            " DROP COLUMN IF EXISTS imap_host,"
            " DROP COLUMN IF EXISTS imap_port,"
            " DROP COLUMN IF EXISTS imap_tls,"
            " DROP COLUMN IF EXISTS imap_user,"
            " DROP COLUMN IF EXISTS imap_credential,"
            " DROP COLUMN IF EXISTS imap_folder,"
            " DROP COLUMN IF EXISTS imap_poll_interval_sec"
        )
    )
