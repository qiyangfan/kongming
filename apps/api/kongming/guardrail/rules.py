"""护栏规则注册表(020):代码声明式,随代码发布的契约数据(与权限点/工具
注册表同哲学);配置表(sys_guardrail_rule)只存覆盖项。

零框架纪律:本模块不 import fastapi/sqlalchemy/redis。
注意:输出护栏的系统提示词特征片段(SYS_PROMPT_SIGNATURES)与 agent 侧
prompts.py/react/prompts.py 的文本内容耦合——prompt 资产变更时须同步
本表(与 prompts.py 文件头例外清单互相登记)。
"""

from kongming.guardrail.models import GuardrailRule

# 输出护栏:系统提示词特征片段(≥12 连续字符,高辨识度,正常答案不会出现)。
# 与 apps/agent prompts 文本耦合,演进时同步(见本模块头注)。
SYS_PROMPT_SIGNATURES: tuple[str, ...] = (
    "你是 PostgreSQL SQL 生成专家",
    "你是 NL2SQL 助手",
    "你是孔明数据助手的自主决策内核",
    "你是数据分析助手。根据 SQL 查询结果生成结构化洞察",
    "排序键可能并列时必须追加主键等唯一列作第二排序键",
    "统计/明细边界",
)

REGISTRY: list[GuardrailRule] = [
    # ── 输入:prompt injection(blocked) ──
    GuardrailRule(
        name="inj_ignore_instructions",
        guardrail_type="input",
        action="blocked",
        pattern=r"忽略.{0,8}(指令|指示|要求)|ignore\s+(all\s+)?(previous\s+)?instructions",
        description="忽略/覆盖既有指令的 injection 模式",
    ),
    GuardrailRule(
        name="inj_jailbreak",
        guardrail_type="input",
        action="blocked",
        pattern=r"扮演.{0,4}DAN|jailbreak|越狱模式|进入开发者模式",
        description="越狱扮演类 injection 模式",
    ),
    GuardrailRule(
        name="inj_prompt_leak",
        guardrail_type="input",
        action="blocked",
        # R1-M3 窄化:套取动词+目标共现(「统计输出指令的执行次数」「你的指令集表
        # 有哪些字段」「列出系统提示词表的字段」等正常业务问句不命中)
        pattern=(
            r"(输出|告诉|展示|打印|复述).{0,4}(你的|系统|以上|全部).{0,2}(提示词|指令)"
            r"|你的系统提示词"
            r"|(output|show|print|repeat|reveal).{0,20}system\s*prompt"
        ),
        description="套取系统提示词的 injection 模式(动词+目标共现)",
    ),
    # ── 输入/输出:PII 与凭证(masked) ──
    GuardrailRule(
        name="pii_phone",
        guardrail_type="input",
        action="masked",
        pattern=r"(?<!\d)1[3-9]\d{9}(?!\d)",  # R1-M4:边界断言(14 位串不误伤)
        description="手机号脱敏",
    ),
    GuardrailRule(
        name="pii_idcard",
        guardrail_type="input",
        action="masked",
        pattern=r"(?<!\d)\d{17}[\dXx](?!\d)",  # R1-M4:边界断言
        description="身份证号脱敏",
    ),
    GuardrailRule(
        name="pii_bankcard",
        guardrail_type="input",
        action="masked",
        # R1-M4:边界断言 + 排除 19/20 开头(时间戳形态长编号静默误伤会改
        # SQL 字面量→错误答案;残余误伤面登记挂账)
        pattern=r"(?<!\d)(?!19\d|20\d)\d{16,19}(?!\d)",
        description="银行卡号脱敏(边界+排除时间戳前缀)",
    ),
    GuardrailRule(
        name="pii_email",
        guardrail_type="input",
        action="masked",
        # 109:local-part 有界量词({1,64},RFC 5321 上限)——无界 `+` 在
        # 不含 @ 的大文本(资源内容等外部数据进检测面)上是 O(n²) 灾难性
        # 回溯(实测 1MB 挂死);有界后 O(64·n) 线性,正常邮箱语义不变
        pattern=r"[\w.+-]{1,64}@[\w-]+\.[\w.]+",
        description="邮箱脱敏(保留域名)",
    ),
    GuardrailRule(
        name="cred_apikey",
        guardrail_type="input",
        action="masked",
        pattern=r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}",
        description="API key 形态脱敏",
    ),
    # ── 输出:凭证泄漏(masked)+ 系统提示词泄漏(blocked) ──
    GuardrailRule(
        name="out_cred_apikey",
        guardrail_type="output",
        action="masked",
        pattern=r"sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}",
        description="输出凭证形态脱敏(报错回显等)",
    ),
    GuardrailRule(
        name="out_sysprompt_leak",
        guardrail_type="output",
        action="blocked",
        substrings=SYS_PROMPT_SIGNATURES,  # 特征片段集判定(任一命中)
        description="系统提示词特征片段泄漏(被诱导输出)",
    ),
    # ── 工具意图(tool_intent) ──
    GuardrailRule(
        name="write_burst",
        guardrail_type="tool_intent",
        action="blocked",
        threshold=1,  # 同 trace 写工具调用超过 1 次即拦(第 2 次起)
        description="同 trace 写工具调用频次上限(防诱导批量写)",
    ),
]
