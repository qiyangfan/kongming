"""0042: 任务派发代际列(090/091 双执行者竞态修复)

- sys_agent_task.dispatch_epoch:每次真实派发(创建/恢复/审批决策)前原子 +1;
  编排回调(begin/step-result/finish)携带派发时所持代际,与任务行代数失配即拦:
  旧循环在 resume 后 begin_step 会拿到 abort 决策立即退出、迟到回写被标记
  stale_epoch、终态收口 409——消除「pause 不杀在途循环 + resume 立即派发」
  叠加出的双执行者窗口(mock 毫秒级步进掩盖、真实 LLM 长步骤必撞,2026-10-10
  e2e 实证:旧循环回写 409 后把任务 FAILED 收口,新循环协作迟到 DONE)。
"""

from sqlalchemy import text

from alembic import op

revision = "0042"
down_revision = "0041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        text(
            "ALTER TABLE public.sys_agent_task ADD COLUMN dispatch_epoch INTEGER NOT NULL DEFAULT 0"
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        text("ALTER TABLE public.sys_agent_task DROP COLUMN IF EXISTS dispatch_epoch")
    )
