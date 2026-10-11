"""控制面配置。prod fail-closed:敏感项无默认值,缺失即拒启。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from kongming.toolgateway.models import WEBSEARCH_PROVIDERS, parse_ticket_endpoint

# 098:全文检索分词配置白名单(部署级单值;rag 表说明路与 document-rag 文档面
# 同源)。单点在 config 层(域内只消费注入值):配置名是 SQL 标识符无法绑定
# 参数,内联进 SQL 前必须过白名单——开放任意 ts_config 名是 SQL 注入面与拼错
# 静默失效的双风险(098 design D2);新分词器=白名单加一项+文档。缺省 simple
# 零回归。不落 rag 域包:auth 域经 auth.ports→config 的既有依赖链不得反向
# 触达 kongming.rag(import-linter 契约)。
FULLTEXT_TS_CONFIGS: tuple[str, ...] = ("simple", "zhparser")
DEFAULT_FULLTEXT_TS_CONFIG = "simple"


def validated_fulltext_ts_config(value: str) -> str:
    """全文检索分词配置白名单校验闸(值域与缺省单源于此,全仓唯一)。

    合法值原样返回;白名单外值抛 ValueError。config 启动期拒启与仓储/缓存
    构造期防线(消费方直构未过 settings)共用同一判定,禁另起口径。"""
    if value not in FULLTEXT_TS_CONFIGS:
        valid = "、".join(FULLTEXT_TS_CONFIGS)
        raise ValueError(f"全文检索分词配置非法值 {value}(值域:{valid})")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="KM_", extra="ignore")

    app_name: str = "kongming-api"
    environment: str = "dev"  # dev | prod
    log_level: str = "INFO"  # 061:运行日志级别

    database_url: str = "postgresql+asyncpg://kongming:kongming@localhost:5432/kongming"
    redis_url: str = "redis://localhost:6379/0"

    # 074 连接池弹性:缺省 5/5/关 与历史行为一致;pool_recycle_sec=0 表示不换代
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_recycle_sec: int = Field(default=0, ge=0)  # 0=关闭;>0 为连接最大存活秒

    # JWT(ADR-003:RS256,私钥签发、公钥验签;prod 必须显式提供)
    jwt_private_key_pem: str | None = None
    jwt_public_key_pem: str | None = None
    jwt_issuer: str = "kongming"
    access_token_ttl_sec: int = Field(default=900, ge=1)  # 15min
    refresh_token_ttl_sec: int = Field(default=7 * 24 * 3600, ge=1)

    # 内部服务令牌:api ↔ agent 通道(与用户 JWT 分离,ADR-002)
    internal_token: str | None = None

    # 联邦总开关(默认开;置 false 即临时停用该家,凭证保留——如企微待正式域名就位前关停)
    github_enabled: bool = True
    google_enabled: bool = True
    wecom_enabled: bool = True
    dingtalk_enabled: bool = True
    feishu_enabled: bool = True

    # GitHub OAuth 联邦
    github_client_id: str | None = None
    github_client_secret: str | None = None
    # 须与 GitHub OAuth App 的 Authorization callback URL 精确一致(vite 默认端口 5173)
    github_redirect_uri: str = "http://localhost:5173/auth/github/callback"

    # Google OAuth 联邦(ADR-006 连接器首发第二家)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str = "http://localhost:5173/auth/google/callback"

    # 企业微信联邦(003):扫码即 OAuth(qrConnect),corpid 即 appid
    wecom_corpid: str | None = None
    wecom_secret: str | None = None
    wecom_agentid: str | None = None
    wecom_redirect_uri: str = "http://localhost:5173/auth/wecom/callback"

    # 钉钉联邦(003):新版统一身份认证(token 为 JSON body,非标准 form)
    dingtalk_client_id: str | None = None
    dingtalk_client_secret: str | None = None
    dingtalk_redirect_uri: str = "http://localhost:5173/auth/dingtalk/callback"

    # 飞书联邦(003):v2 端点 app 凭证直换 user token(官方支持 PKCE)
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_redirect_uri: str = "http://localhost:5173/auth/feishu/callback"

    # 认证安全策略(ADR-003;锁定语义见 auth spec;ge=1 防 0 值导致 Redis EX 报错)
    auth_login_max_fails: int = Field(default=5, ge=1)  # 连续失败达此次数锁定
    auth_login_lock_min: int = Field(default=15, ge=1)  # 锁定时长(分钟)
    auth_state_ttl_sec: int = Field(default=600, ge=1)  # 联邦 state 一次性凭证时效

    # 启动种子(rbac spec「种子数据」):超管账号;缺省密码时强制首登改密
    admin_username: str = "admin"
    admin_initial_password: str | None = None

    # 动态数据源密码加密密钥
    datasource_encrypt_key: str | None = None

    # Agent 面回调地址
    agent_base_url: str = "http://localhost:8001"
    agent_timeout_sec: float = 120.0  # 覆盖 understand+generate+≤2 次 selfheal+summarize
    # 088:报告生成=多轮 LLM+多次取数的长任务,独立超时预算(非单轮问答口径)
    agent_report_timeout_sec: float = Field(default=600.0, gt=0)
    # 090:任务编排=多步长任务;须 ≥ 任务默认墙钟预算(900s)+ 余量,
    # 否则合法运行中的任务会被传输层超时误收口
    agent_task_timeout_sec: float = Field(default=960.0, gt=0)
    # 090/091:任务预算与协作部署级上限(任务级配置/步骤拓扑声明的硬天花板,
    # 创建与声明时钳制;缺省=090 预算缺省与 091 编排缺省,零行为漂移)
    task_budget_max_steps: int = Field(default=12, ge=1)
    task_budget_max_tokens: int = Field(default=200_000, gt=0)
    task_budget_max_seconds: int = Field(default=900, gt=0)
    collab_max_workers: int = Field(default=3, ge=1)

    # 104 Bot 商店版本化:版本历史上限(部署级可配,缺省 10);封顶淘汰
    # 只淘汰最旧的「非当前」版本,当前上架版本恒受保护不淘汰
    bot_store_version_cap: int = Field(default=10, ge=1)

    # 内部元数据通道:租户 schema 内省表数上限(防 prompt 膨胀)
    meta_schema_max_tables: int = Field(default=50, ge=1)

    # 064 召回相关度闸门:恒生效裁剪(2026-10-11 owner 裁决收编三态——
    # 无生产流量,off/shadow/primary 的标定前提不存在;零误拒对拍已证即
    # 直接切,不保留关闭/影子形态)。阈值按路独立(向量=余弦相似度 /
    # 全文=ts_rank 量纲不同,不共用)。向量档已按评估集标定(2026-10-10
    # 对拍:知识在位可答题 top1 最低 0.683,0.30 有 2.3× 余量、零误拒;
    # 成对写入,防「mean=0 判据恒真」的半开脚枪);全文档量纲未标定,
    # 保持 0(=该路不裁,非恒真放行——is_relevant 的 mean 恒真只在
    # top1 档>0 时有语义,两档均为 0 即整路不裁)。启用前置已代码化:
    # 零知识数据源闸门自动旁路(zero-knowledge ≠ low-relevance)。
    rag_gate_vector_top1: float = Field(default=0.30, ge=0.0)
    rag_gate_vector_mean: float = Field(default=0.28, ge=0.0)
    rag_gate_fulltext_top1: float = Field(default=0.0, ge=0.0)
    rag_gate_fulltext_mean: float = Field(default=0.0, ge=0.0)
    rag_gate_min_count: int = Field(default=1, ge=1)
    rag_gate_mean_window: int = Field(default=3, ge=1)

    # rag 上传上限(009):文本类/PDF·Word 分档字节上限
    rag_text_upload_max_bytes: int = Field(default=1024 * 1024, ge=1024)
    rag_binary_upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    # embedding 维度(009):缓存/回调向量的程序防线——换模型未 bump 缓存版本
    # 时,旧维度向量命中会被拦为 EmbeddingError(降级),不再 500(R2-R1)
    rag_embedding_dim: int = Field(default=1536, ge=1)

    # 098:全文检索分词配置(部署级单值;rag 表说明路与 document-rag 文档面
    # 写读同源)。缺省 simple 零回归;白名单(simple/zhparser,值域单源见本
    # 模块 FULLTEXT_TS_CONFIGS)外值一律拒启(dev/prod 同——降级=换词法,与
    # 存量 tsv 错配,不存在安全降级形态;启用 zhparser 另需 PG 扩展,安装配方
    # 见 docs/HANDOFF.md「zhparser 安装配方」)。切换后须重扫/重建重算存量 tsv
    # 并复标全文路阈值(064 全文路 ts_rank 量纲随分词变化)。
    rag_fulltext_ts_config: str = DEFAULT_FULLTEXT_TS_CONFIG

    # 095 关系感知召回扩展:缺省开启;整项 kill switch=置 false 即回到无扩展
    # 形态(逐字一致,零回归);rag_expand_max 为单轮扩展表上限(0=不扩展)。
    # 判据是二值的(关系行在不在),无需 064 式灰度标定,故只做开关+上限。
    rag_expand_enabled: bool = True
    rag_expand_max: int = Field(default=2, ge=0)

    # 结果导出行数上限(010):截断保护防服务端内存失控(chat spec 默认 10000)
    export_max_rows: int = Field(default=10000, ge=1)

    # 审计留存与归档(015,audit spec):库内热数据窗口(天)+归档目录
    # (落盘即随部署备份链;异地加密上传为部署运维面)
    audit_archive_days: int = Field(default=180, ge=1)
    audit_archive_dir: str = "var/audit-archive"

    # mock 支付通道(035,payment-gateway spec):默认开启——mock 是 v1 唯一
    # 收款路径;密钥 dev 默认占位(生产必须显式替换),支付单在途时长(分钟)
    billing_mock_enabled: bool = True
    billing_mock_secret: str = "dev-mock-payment-secret"  # noqa: S105  # dev 占位刻意(生产必须替换,上文注释)
    billing_payment_ttl_minutes: int = Field(default=30, ge=1)

    # tool-calling(019):工具执行固定窗口限流(次/分钟,租户+用户维度)+
    # 写工具确认窗口(分钟)
    tool_rate_limit_per_minute: int = Field(default=60, ge=1)
    tool_confirm_ttl_minutes: int = Field(default=15, ge=1)

    # 触发器失败重试(101):退避为部署级固定间隔(design 裁决 3——重试次数
    # 上限 max_retries 属触发器 owner 决策(0–5,表级),部署只配间隔)
    trigger_retry_backoff_sec: int = Field(default=60, ge=0)

    # 问答成本控制(062):登录用户问答面分钟级速率((租户,用户) 维度)+ 租户
    # 日/月按次配额(已用量=sys_llm_usage 行数,含失败尝试——038 既有口径);
    # 公开嵌入面(助手标识, 客户端 IP) 维度速率。0=不限(关闭该闸)
    chat_rate_per_minute: int = Field(default=20, ge=0)
    chat_quota_daily_calls: int = Field(default=500, ge=0)
    chat_quota_monthly_calls: int = Field(default=10000, ge=0)
    bot_chat_rate_per_minute: int = Field(default=30, ge=0)

    # 问答结果行数上限(062):chat 面 SQL 执行行数统一配置(取代 inline 魔数,
    # 与导出路径 export_max_rows 同语义不同阀:结果表渲染上限远低于导出)
    chat_result_max_rows: int = Field(default=1000, ge=1)

    # cloud-code-sandbox(027):默认关闭——开启且宿主 podman 可用才接 runner;
    # 镜像名/超时/内存/CPU 限额进容器参数(常量拼装,design D1)
    sandbox_enabled: bool = False
    sandbox_image: str = "kongming-sandbox:dev"
    sandbox_timeout_sec: int = Field(default=30, ge=1, le=300)
    sandbox_mem_mb: int = Field(default=256, ge=64, le=4096)
    sandbox_cpus: float = Field(default=0.5, gt=0, le=8)

    # mcp-interop(028):出站调用超时(秒;响应 1MB 闸在 client 常量)
    mcp_outbound_timeout_sec: int = Field(default=15, ge=1, le=120)

    # 081 external-knowledge:个人本地知识库(siku)检索端点。空=knowledge_search
    # 工具不注册(零回归);非空须过 localhost 白名单(registry.is_local_base_url)
    # 才生效(端点+localhost 白名单两道注册条件;多租户仅告警,082 降闸)
    siku_base_url: str = ""
    siku_timeout_ms: int = Field(default=5000, ge=1, le=60000)
    # 089 工具网关:knowledge 域限流第二维度(租户+用户固定窗口,叠加在
    # 工具总配额之上;部署级可调)
    toolgateway_knowledge_quota: int = Field(default=30, ge=1, le=600)

    # 094 web-search-domain:联网检索域(供应商+API key 齐备才注册 web_search;
    # 均未配置=域零感知,合法缺省)。凭证只存控制面环境变量(ADR-002)
    websearch_provider: str = ""  # tavily | bocha | zhipu | alibaba_ai_search
    websearch_api_key: str = ""
    websearch_timeout_ms: int = Field(default=8000, ge=1, le=30000)
    toolgateway_websearch_quota: int = Field(default=20, ge=1, le=600)

    # 100 internal-connector-domain:内部工单域(部署级条件注册:端点+服务凭证
    # 齐备才注册 ticket 三工具;均缺省=域零感知,合法缺省)。内部系统凭证属
    # 企业部署事实(服务账号),与 websearch 同谱系,只存控制面环境变量(ADR-002);
    # 对接面=「最小工单 API 约定」(单源:scripts/mock_ticket_server.py 文件头)
    ticket_base_url: str = ""
    ticket_api_key: str = ""
    ticket_timeout_ms: int = Field(default=5000, ge=1, le=30000)
    toolgateway_ticket_quota: int = Field(default=20, ge=1, le=600)

    # 099 http-connector-domain:租户级 HTTP 连接器(声明存库行,注入集按请求
    # 动态装配;凭证加密落库,复用 008 cipher 单点)。注册超时受部署级上限封顶
    # (spec「超时按注册声明,部署级上限封顶」);配额独立分桶(与既有域互不挤占)
    toolgateway_http_quota: int = Field(default=20, ge=1, le=600)
    connector_timeout_max_ms: int = Field(default=15000, ge=100, le=60000)

    # SQL 安全引擎(053):builtin | shadow | primary;默认影子(内置权威,
    # 引擎只对拍观测)。切换期行为契约零变更;引擎缺失/异常自动降级不阻断。
    sqlguard_mode: str = "shadow"

    # 成本对账旁路(055):muniu 管理面只读访问,凭证环境变量注入,
    # 不落库不入日志;token 空 = 对账端点报告「未配置」。
    muniu_admin_base_url: str = "http://127.0.0.1:8787"
    muniu_admin_token: str = ""
    cost_reconcile_threshold: float = Field(default=0.2, ge=0.0, le=1.0)

    def websearch_misconfiguration(self) -> str | None:
        """094:websearch 组合配置校验(残缺返回描述,否则 None)。

        prod 由 fail_closed_checks 拒启;dev 由 lifespan 告警降级(域不注册)。
        均未配置=合法缺省(零感知),不视为残缺。
        """
        provider = (self.websearch_provider or "").strip().lower()
        key = (self.websearch_api_key or "").strip()
        if not provider:
            # 只配 key 缺供应商同属残缺(评审循环 D3:排障时「配了 key 却没生效」
            # 不能无信号)
            if key:
                return "KM_WEBSEARCH_API_KEY 已配置但 KM_WEBSEARCH_PROVIDER 缺失,websearch 域不注册"
            return None
        if provider not in WEBSEARCH_PROVIDERS:
            valid = "、".join(sorted(WEBSEARCH_PROVIDERS))
            return f"KM_WEBSEARCH_PROVIDER 非法值 {provider}(值域:{valid})"
        if not key:
            return (
                f"KM_WEBSEARCH_PROVIDER={provider} 已配置但 KM_WEBSEARCH_API_KEY 缺失,"
                "websearch 域不注册"
            )
        return None

    def ticket_misconfiguration(self) -> str | None:
        """100:ticket 组合配置校验(残缺返回描述,否则 None)。

        prod 由 fail_closed_checks 拒启;dev 由 lifespan 告警降级(域不注册)。
        均未配置=合法缺省(零感知),不视为残缺。
        """
        base = (self.ticket_base_url or "").strip()
        key = (self.ticket_api_key or "").strip()
        if not base:
            if key:
                return "KM_TICKET_API_KEY 已配置但 KM_TICKET_BASE_URL 缺失,ticket 域不注册"
            return None
        if not key:
            return "KM_TICKET_BASE_URL 已配置但 KM_TICKET_API_KEY 缺失,ticket 域不注册"
        if parse_ticket_endpoint(base) is None:
            return f"KM_TICKET_BASE_URL 非法(须为可解析的 http(s) 地址),ticket 域不注册:{base}"
        return None

    def require_fulltext_ts_config(self) -> str:
        """098:全文检索分词配置白名单校验,白名单外值 RuntimeError 拒启。

        dev/prod 同(098 design D3:降级=换词法,不存在安全降级形态,不照抄
        websearch 的 dev 告警降级);get_settings 启动期调用。"""
        try:
            return validated_fulltext_ts_config(self.rag_fulltext_ts_config)
        except ValueError as e:
            raise RuntimeError(str(e)) from None

    def fail_closed_checks(self) -> None:
        """prod 环境缺敏感配置直接拒启(dev 宽松以便本地起步)。"""
        if self.environment != "prod":
            return
        required = (
            ("KM_JWT_PRIVATE_KEY_PEM", self.jwt_private_key_pem),
            ("KM_JWT_PUBLIC_KEY_PEM", self.jwt_public_key_pem),
            ("KM_INTERNAL_TOKEN", self.internal_token),
            ("KM_DATASOURCE_ENCRYPT_KEY", self.datasource_encrypt_key),
        )
        missing = [env for env, val in required if not val]
        if missing:
            raise RuntimeError(f"prod fail-closed: 缺少必需配置 {missing}")
        # 审查循环 S1(mock 回调验签可伪造):mock 通道开启时密钥必须显式配置
        # 且不得为仓库公开的 dev 占位值——否则任何人可伪造 PAID 回调核销账单
        if self.billing_mock_enabled and self.billing_mock_secret in (
            "",
            "dev-mock-payment-secret",
        ):
            raise RuntimeError(
                "prod fail-closed: KM_BILLING_MOCK_ENABLED 开启时"
                " KM_BILLING_MOCK_SECRET 必须显式配置且不得为 dev 占位值"
            )
        # 094:websearch 组合残缺(供应商已配缺 key / 供应商非法值)在 prod
        # 拒启而非静默降级——配置错误必须显式暴露(dev 由 lifespan 告警降级)
        ws_misconfig = self.websearch_misconfiguration()
        if ws_misconfig:
            raise RuntimeError(f"prod fail-closed: {ws_misconfig}")
        # 100:ticket 组合残缺(端点缺凭证/凭证缺端点/端点非法)同口径拒启
        tk_misconfig = self.ticket_misconfiguration()
        if tk_misconfig:
            raise RuntimeError(f"prod fail-closed: {tk_misconfig}")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.fail_closed_checks()
    s.require_fulltext_ts_config()  # 098:非法分词配置一律拒启(dev/prod 同)
    return s
