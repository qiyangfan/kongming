"""0050: 邮件入站触发器(108-email-inbound-trigger)

纯增三块(design D5:配置列在触发器行、游标独立小表):
- sys_trigger_definition 加七列邮件配置:imap_host/imap_port/imap_tls/
  imap_user/imap_credential/imap_folder/imap_poll_interval_sec——明文
  配置列 + 加密凭证列(AES-GCM,与 webhook secret 同一加密机制);存量行
  缺省空串零影响。
- type CHECK 扩展 'EMAIL'(邮件触发器是触发器的一种形态)。
- sys_trigger_run 加 summary TEXT:邮件触发 run 只留标准化摘要
  ({from,subject,date,text_preview} JSON,白名单+截断),邮件原文与
  完整正文 MUST NOT 落库(隐私最小化);存量行缺省空串。
- sys_trigger_email_cursor:每触发器×文件夹一行已处理 UID 游标
  (trigger_id+folder 唯一;last_uid/uidvalidity 支撑断线续传与
  UIDVALIDITY 变更作废重置)。
**downgrade**:删游标表/summary 列/七配置列,EMAIL 行随形态回退移除后
恢复 CRON/WEBHOOK 双态 CHECK。
"""

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
