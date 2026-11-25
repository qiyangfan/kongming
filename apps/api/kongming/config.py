"""控制面配置。prod fail-closed:敏感项无默认值,缺失即拒启。"""

import socket
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from kongming.toolgateway.models import WEBSEARCH_PROVIDERS

# 全文检索分词配置白名单(部署级单值)。ts_config 是 SQL 标识符无法绑定参数,
# 内联进 SQL 前必须过白名单——开放任意配置名有 SQL 注入与拼错静默失效双风险。
# 新分词器 = 白名单加一项 + 文档。放 config 层不落 rag 域包:auth 域经
# auth.ports 依赖 config,该依赖链不得反向触达 kongming.rag(import-linter 契约)。
FULLTEXT_TS_CONFIGS: tuple[str, ...] = ("simple", "zhparser")
DEFAULT_FULLTEXT_TS_CONFIG = "simple"

# 文档检索重排供应商注册表(条目=(endpoint, exit_host);部署级条件注册,
# 代码单源=出口白名单)。放 config 层的理由同 FULLTEXT_TS_CONFIGS(config
# 不得反向触达 kongming.rag)。新供应商 = 本表加一条 + rag/rerank.py 的
# _ADAPTERS 加同名适配器(归一 results[i].index/relevance_score),两表缺一启动即报错。
RERANK_PROVIDERS: dict[str, tuple[str, str]] = {
    "siliconflow": ("https://api.siliconflow.cn/v1/rerank", "api.siliconflow.cn"),
    "bailian": (
        "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank",
        "dashscope.aliyuncs.com",
    ),
}


def validated_fulltext_ts_config(value: str) -> str:
    """检查全文检索使用的分词配置是否受支持。

    支持的配置原样返回；不支持的配置会抛出 ValueError。
    应用启动、仓储和缓存初始化都会调用这里，确保校验规则一致。"""
    if value not in FULLTEXT_TS_CONFIGS:
        valid = ",".join(FULLTEXT_TS_CONFIGS)
        raise ValueError(f"全文检索分词配置非法值 {value}(值域:{valid})")
    return value


# 文档全文检索关键词排序白名单。ts_rank 为现有缺省;bm25 为评测实验排序,
# 只在隔离评测环境的 API 进程启用(KM_RAG_FULLTEXT_RANKER=bm25),生产保持
# ts_rank。BM25 依赖入库 tsv 的词频与段长统计,统计范围限定在所选租户与
# 数据源(cross-tenant 对照见评测 change)。
FULLTEXT_RANKERS: tuple[str, ...] = ("ts_rank", "bm25")
DEFAULT_FULLTEXT_RANKER = "ts_rank"


def validated_fulltext_ranker(value: str) -> str:
    """检查关键词排序方式是否受支持;白名单外值抛 ValueError。"""
    if value not in FULLTEXT_RANKERS:
        valid = ",".join(FULLTEXT_RANKERS)
        raise ValueError(f"全文检索排序方式非法值 {value}(值域:{valid})")
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="KM_", extra="ignore")

    app_name: str = "kongming-api"
    environment: str = "dev"  # dev | prod
    log_level: str = "INFO"

    database_url: str = "postgresql+asyncpg://kongming:kongming@localhost:5432/kongming"
    redis_url: str = "redis://localhost:6379/0"

    # 连接池弹性:缺省 5/5/关,与历史行为一致
    db_pool_size: int = Field(default=5, ge=1)
    db_max_overflow: int = Field(default=5, ge=0)
    db_pool_recycle_sec: int = Field(default=0, ge=0)  # 0=关闭;>0 为连接最大存活秒

    # JWT(ADR-003:RS256,私钥签发、公钥验签;prod 必须显式提供)
    jwt_private_key_pem: str | None = None
    jwt_public_key_pem: str | None = None
    jwt_issuer: str = "kongming"
    access_token_ttl_sec: int = Field(default=900, ge=1)
    refresh_token_ttl_sec: int = Field(default=7 * 24 * 3600, ge=1)

    # 内部服务令牌:api ↔ agent 通道(与用户 JWT 分离,ADR-002)
    internal_token: str | None = None

    # 统一身份(Keycloak)联邦:唯一登录入口;置 false 只停新登录,已签发令牌按 TTL 继续有效
    keycloak_enabled: bool = True
    keycloak_issuer: str | None = None
    keycloak_client_id: str | None = None
    keycloak_client_secret: str | None = None
    keycloak_redirect_uri: str = "http://localhost:5173/auth/keycloak/callback"
    keycloak_post_logout_redirect_uri: str = "http://localhost:5173/login"
    auth_cookie_secure: bool = False

    auth_state_ttl_sec: int = Field(default=600, ge=1)
    # 登录发起每 IP 每分钟上限:匿名入口,防 state 存储与发现文档被刷
    auth_login_rate_per_minute: int = Field(default=60, ge=1)

    # 运维超管:Keycloak sub(首次登录建过档后生效);留空则不绑定任何超管
    admin_subject: str | None = None

    # 动态数据源密码加密密钥
    datasource_encrypt_key: str | None = None

    agent_base_url: str = "http://localhost:8001"
    # 本实例对同类实例可达的自报地址(跨实例取消转发;share-turn-registry)。
    # 多实例部署须逐实例唯一且可达:k8s 填 pod IP/名称,勿用共享服务名——
    # 否则自环防护会把别实例的在途轮误判为陈旧登记。缺省按主机名推导
    # (容器内监听端口固定 8000)。
    api_self_base_url: str = Field(default_factory=lambda: f"http://{socket.gethostname()}:8000")
    # 在途轮跨实例登记键 TTL:须大于「单轮 agent 流上限 + 收敛余量」
    # (agent_timeout_sec 缺省 120s,最坏约 5 个顺序阶段);调大 agent 超时须同步调大
    turn_registry_ttl_sec: int = Field(default=900, ge=60)

    # 语音通话(013:声音直连、控制回后端):密钥/地址/模型缺省空=功能不启用,
    # 入口不出现、通话端点 503;启用后并发与时长为部署级钳制(见 tech-stack 上限表)
    voice_api_key: str | None = None
    voice_realtime_base_url: str | None = None
    voice_model: str | None = None
    voice_max_concurrent: int = Field(default=2, ge=1)
    voice_max_call_seconds: int = Field(default=600, ge=60)
    agent_timeout_sec: float = 120.0  # 覆盖 understand+generate+≤2 次 selfheal+summarize
    # 报告生成=多轮 LLM+多次取数的长任务,独立超时预算(非单轮问答口径)
    agent_report_timeout_sec: float = Field(default=600.0, gt=0)
    # 任务编排=多步长任务;须 ≥ 任务默认墙钟预算(900s)+ 余量,
    # 否则合法运行中的任务会被传输层超时误收口
    agent_task_timeout_sec: float = Field(default=960.0, gt=0)
    # 任务预算的部署级上限:任务级配置与拓扑声明在创建/声明时钳制到该天花板
    task_budget_max_steps: int = Field(default=12, ge=1)
    task_budget_max_tokens: int = Field(default=200_000, gt=0)
    task_budget_max_seconds: int = Field(default=900, gt=0)
    # 图内确认记录的过期时间，单位为分钟。
    graph_confirm_ttl_minutes: int = Field(default=30, ge=1)
    # 协作人数最多 64，轮次最多 16，与内部接口的限制保持一致。
    # 配置超过接口上限时，超出的协作事件可能会被接口忽略。
    collab_max_workers: int = Field(default=3, ge=1, le=64)
    # 圆桌轮次上限(拓扑声明钳制;与 agent 面执行兜底同值同源)
    collab_max_rounds: int = Field(default=3, ge=1, le=16)

    # 在途任务看门狗(task-kernel「运行中看门狗监督」):停滞阈值须显著小于
    # 任务墙钟预算(900s),保证看门狗先于传输层超时收口;
    # 判读后二次观察窗同用 stale_sec,最坏路径 stale*2+判读 < 900
    watchdog_enabled: bool = True
    watchdog_interval_sec: int = Field(default=60, ge=10)
    watchdog_stale_sec: int = Field(default=300, ge=60)
    watchdog_judge_timeout_sec: float = Field(default=20.0, gt=0)

    # Bot 商店版本历史上限(缺省 10);封顶淘汰只淘汰最旧的「非当前」版本,
    # 当前上架版本恒受保护
    bot_store_version_cap: int = Field(default=10, ge=1)

    # 内部元数据通道:租户 schema 内省表数上限(防 prompt 膨胀)
    meta_schema_max_tables: int = Field(default=50, ge=1)

    # 相关度闸门始终生效；向量和全文的打分不同，门槛分别设置。
    # 向量门槛已有评测依据；全文门槛尚未标定，暂设为 0（不裁全文结果）。
    # 没有知识的数据源跳过闸门，避免把“无知识”误判成“低相关”。
    rag_gate_vector_top1: float = Field(default=0.30, ge=0.0)
    rag_gate_vector_mean: float = Field(default=0.28, ge=0.0)
    rag_gate_fulltext_top1: float = Field(default=0.0, ge=0.0)
    rag_gate_fulltext_mean: float = Field(default=0.0, ge=0.0)
    rag_gate_min_count: int = Field(default=1, ge=1)
    rag_gate_mean_window: int = Field(default=3, ge=1)

    # rag 上传上限:文本类/PDF·Word 分档字节上限
    rag_text_upload_max_bytes: int = Field(default=1024 * 1024, ge=1024)
    rag_binary_upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    # embedding 维度:缓存/回调向量的程序防线——换模型未 bump 缓存版本时,
    # 旧维度向量命中会被拦为 EmbeddingError(降级),不再 500
    rag_embedding_dim: int = Field(default=2560, ge=1)

    # 全文检索按部署配置使用同一种分词规则,RAG 表说明和上传文档共用。
    # 只接受 simple、zhparser(见 FULLTEXT_TS_CONFIGS);填其他值 dev/prod 均
    # 启动失败,避免已有数据与分词规则对不上。zhparser 需在 PostgreSQL 安装
    # 扩展,步骤见 docs/HANDOFF.md「中文分词(zhparser)启用配方」。切换分词规则后须按
    # 新规则重建已有内容的全文检索数据,并重新校准检索阈值(分词影响相关性评分)。
    rag_fulltext_ts_config: str = DEFAULT_FULLTEXT_TS_CONFIG

    # 文档全文检索关键词排序:ts_rank(缺省)|bm25(评测实验)。bm25 只在
    # 隔离评测 API 进程启用,生产部署保持 ts_rank。
    rag_fulltext_ranker: str = DEFAULT_FULLTEXT_RANKER

    # 按已登记表关系为召回结果补充一跳关联表,只用已登记关系,不猜测;
    # rag_expand_max=0 时不补充。
    rag_expand_enabled: bool = True
    rag_expand_max: int = Field(default=2, ge=0)

    # 结果导出行数上限:截断保护防服务端内存失控(chat spec 默认 10000)
    export_max_rows: int = Field(default=10000, ge=1)

    # 审计留存与归档(audit spec):库内热数据窗口(天)+归档目录
    # (落盘即随部署备份链;异地加密上传为部署运维面)
    audit_archive_days: int = Field(default=180, ge=1)
    audit_archive_dir: str = "var/audit-archive"

    # mock 支付通道(payment-gateway spec):默认开启——mock 是 v1 唯一
    # 收款路径;密钥 dev 默认占位(生产必须显式替换),支付单在途时长(分钟)
    billing_mock_enabled: bool = True
    billing_mock_secret: str = "dev-mock-payment-secret"  # noqa: S105  # dev 占位刻意(生产必须替换,上文注释)
    billing_payment_ttl_minutes: int = Field(default=30, ge=1)

    # tool-calling:工具执行固定窗口限流(次/分钟,租户+用户维度)+
    # 写工具确认窗口(分钟)
    tool_rate_limit_per_minute: int = Field(default=60, ge=1)
    tool_confirm_ttl_minutes: int = Field(default=15, ge=1)

    # 触发器失败重试:退避为部署级固定间隔,不做指数退避/抖动;重试次数上限
    # max_retries 属触发器 owner 决策(0–5,表级),部署只配间隔
    trigger_retry_backoff_sec: int = Field(default=60, ge=0)

    # 问答成本控制:登录用户问答面分钟级速率((租户,用户) 维度)+ 租户
    # 日/月按次配额(已用量=sys_llm_usage 行数,含失败尝试——既有口径);
    # 公开嵌入面(助手标识, 客户端 IP) 维度速率。0=不限(关闭该闸)
    chat_rate_per_minute: int = Field(default=20, ge=0)
    chat_quota_daily_calls: int = Field(default=500, ge=0)
    chat_quota_monthly_calls: int = Field(default=10000, ge=0)
    bot_chat_rate_per_minute: int = Field(default=30, ge=0)
    # 语音转写:faster-whisper 模型规格(small/base/medium…;int8/cpu)。
    # 模型文件在位即启用,缺失时 /chat/stt 返 503 明示配方(无独立开关,
    # 状态自明);预下载配方见 docs/HANDOFF.md
    stt_model: str = Field(default="small")

    # 问答结果行数上限:chat 面 SQL 执行行数统一配置(取代 inline 魔数,
    # 与导出路径 export_max_rows 同语义不同阀:结果表渲染上限远低于导出)
    chat_result_max_rows: int = Field(default=1000, ge=1)

    # 沙箱默认关闭:开启且宿主 podman 可用才接 runner;
    # 镜像名/超时/内存/CPU 限额进容器参数(常量拼装)
    sandbox_enabled: bool = False
    sandbox_image: str = "kongming-sandbox:dev"
    sandbox_timeout_sec: int = Field(default=30, ge=1, le=300)
    sandbox_mem_mb: int = Field(default=256, ge=64, le=4096)
    sandbox_cpus: float = Field(default=0.5, gt=0, le=8)

    # mcp-interop:出站调用超时(秒;响应 1MB 闸在 client 常量)
    mcp_outbound_timeout_sec: int = Field(default=15, ge=1, le=120)

    # 出站运行时熔断(连续 timeout/conn 自动冷却,半开
    # 探测恢复;管理面探测不受限,确定性错误不计数)。enabled=False 逐字旧行为
    mcp_breaker_enabled: bool = True
    mcp_breaker_threshold: int = Field(default=3, ge=1, le=100)
    mcp_breaker_cooldown_sec: int = Field(default=60, ge=1, le=3600)

    # 答案异步评分(spec「答案异步评分」):SUCCESS 轮图外异步评审;
    # enabled=False 触发点短路,逐字回到引入前行为
    answer_scoring_enabled: bool = True
    # 评审主张分解是长输出,独立超时预算(非标题 30s 口径)
    answer_scoring_timeout_sec: float = Field(default=90.0, gt=0)

    # 联网检索域(供应商+API key 齐备才注册 web_search;
    # 均未配置=域零感知,合法缺省)。凭证只存控制面环境变量(ADR-002)
    websearch_provider: str = ""  # tavily | bocha | zhipu | alibaba_ai_search
    websearch_api_key: str = ""
    websearch_timeout_ms: int = Field(default=8000, ge=1, le=30000)
    toolgateway_websearch_quota: int = Field(default=20, ge=1, le=600)

    # 文档检索重排(部署级条件注册:provider+key+model 齐备才
    # 生效;均未配置=重排级零存在感,合法缺省)。供应商值域=RERANK_PROVIDERS。
    # 组合残缺(配 provider 缺 key/model、只配 key)prod 拒启/dev 告警降级
    # (同 websearch 口径);provider 非法值 dev/prod 同拒启(降级=换供应商,
    # 无安全降级形态,不照抄 websearch 的告警降级)。凭证只存控制面环境变量。
    rerank_provider: str = ""  # siliconflow | bailian
    rerank_api_key: str = ""
    rerank_model: str = ""
    rerank_pool_topk: int = Field(default=20, ge=1, le=100)  # 融合后候选池扩量上限
    rerank_score_threshold: float = Field(default=0.0, ge=0.0, le=1.0)  # 0=不过滤
    rerank_timeout_ms: int = Field(default=3000, ge=100, le=30000)

    # 文档实体抽取/检索扩展(部署级,缺省关=能力从未存在,合法缺省)。
    # 组合残缺(enabled 但端点/key/model 任一缺)prod 拒启/dev 告警降级
    # (同 rerank 口径)。凭证只存控制面环境变量。
    doc_entity_enabled: bool = False
    doc_entity_llm_base_url: str = ""  # openai 兼容 chat 端点(…/v1)
    doc_entity_llm_api_key: str = ""
    doc_entity_llm_model: str = ""
    doc_entity_timeout_ms: int = Field(default=8000, ge=500, le=60000)
    doc_entity_query_max: int = Field(default=8, ge=1, le=50)  # 实体路并入候选池配额
    doc_entity_allow_private_llm: bool = False  # 私网/本机 LLM 端点显式豁免(本地 dev)

    # 子块句界校准(独立部署开关,缺省关=切分与既有实现逐字一致)
    doc_chunk_sentence_boundary: bool = False

    # 租户级 HTTP 连接器(声明存库行,注入集按请求
    # 动态装配;凭证加密落库,复用 cipher 单点)。注册超时受部署级上限封顶
    # (spec「超时按注册声明,部署级上限封顶」);配额独立分桶(与既有域互不挤占)
    toolgateway_http_quota: int = Field(default=20, ge=1, le=600)
    connector_timeout_max_ms: int = Field(default=15000, ge=100, le=60000)

    # SQL 安全引擎:builtin | shadow | primary;默认影子(内置权威,
    # 引擎只对拍观测)。切换期行为契约零变更;引擎缺失/异常自动降级不阻断。
    sqlguard_mode: str = "shadow"

    # 成本对账旁路:muniu 管理面只读访问,URL 与凭证均显式配置,
    # 凭证环境变量注入,不落库不入日志;任一项空 = 对账端点报告「未配置」。
    muniu_admin_base_url: str = ""
    muniu_admin_token: str = ""
    cost_reconcile_threshold: float = Field(default=0.2, ge=0.0, le=1.0)

    def websearch_misconfiguration(self) -> str | None:
        """websearch 组合配置校验(残缺返回描述,否则 None)。

        prod 由 fail_closed_checks 拒启;dev 由 lifespan 告警降级(域不注册)。
        均未配置=合法缺省(零感知),不视为残缺。
        """
        provider = (self.websearch_provider or "").strip().lower()
        key = (self.websearch_api_key or "").strip()
        if not provider:
            # 只配 key 缺供应商同属残缺(排障时「配了 key 却没生效」
            # 不能无信号)
            if key:
                return "KM_WEBSEARCH_API_KEY 已配置但 KM_WEBSEARCH_PROVIDER 缺失,websearch 域不注册"
            return None
        if provider not in WEBSEARCH_PROVIDERS:
            valid = ",".join(sorted(WEBSEARCH_PROVIDERS))
            return f"KM_WEBSEARCH_PROVIDER 非法值 {provider}(值域:{valid})"
        if not key:
            return (
                f"KM_WEBSEARCH_PROVIDER={provider} 已配置但 KM_WEBSEARCH_API_KEY 缺失,"
                "websearch 域不注册"
            )
        return None

    def rerank_misconfiguration(self) -> str | None:
        """重排组合配置校验(残缺返回描述,否则 None)。

        prod 由 fail_closed_checks 拒启;dev 由 lifespan 告警降级(重排级不生效)。
        均未配置=合法缺省(零存在感),不视为残缺。provider 非法值不在此列
        ——走 require_rerank_provider 启动硬校验(dev/prod 同拒)。
        """
        provider = (self.rerank_provider or "").strip().lower()
        key = (self.rerank_api_key or "").strip()
        model = (self.rerank_model or "").strip()
        if not provider:
            # 只配 key/model 缺供应商同属残缺(websearch 同原则:
            # 排障时「配了却没生效」不能无信号;三变量族的 model 单配也覆盖)
            if key:
                return "KM_RERANK_API_KEY 已配置但 KM_RERANK_PROVIDER 缺失,重排级不生效"
            if model:
                return "KM_RERANK_MODEL 已配置但 KM_RERANK_PROVIDER 缺失,重排级不生效"
            return None
        if not key:
            return f"KM_RERANK_PROVIDER={provider} 已配置但 KM_RERANK_API_KEY 缺失,重排级不生效"
        if not model:
            return f"KM_RERANK_PROVIDER={provider} 已配置但 KM_RERANK_MODEL 缺失,重排级不生效"
        return None

    def require_rerank_provider(self) -> None:
        """重排 provider 白名单校验,白名单外值 RuntimeError 拒启。

        dev/prod 同(require_fulltext_ts_config 同款:非法值属配置错误而非
        合法部署形态,降级只会静默失效无信号);get_settings 启动期调用。"""
        provider = (self.rerank_provider or "").strip().lower()
        if provider and provider not in RERANK_PROVIDERS:
            valid = ",".join(sorted(RERANK_PROVIDERS))
            raise RuntimeError(f"KM_RERANK_PROVIDER 非法值 {provider}(值域:{valid})")

    def doc_entity_misconfiguration(self) -> str | None:
        """实体抽取组合配置校验(enabled 且端点/key/model 任一缺=残缺)。

        prod 由 fail_closed_checks 拒启;dev 由 lifespan 告警降级(实体级
        不生效)。未开启=合法缺省(零存在感),不视为残缺。"""
        if not self.doc_entity_enabled:
            return None
        missing = [
            name
            for name, val in (
                ("KM_DOC_ENTITY_LLM_BASE_URL", self.doc_entity_llm_base_url),
                ("KM_DOC_ENTITY_LLM_API_KEY", self.doc_entity_llm_api_key),
                ("KM_DOC_ENTITY_LLM_MODEL", self.doc_entity_llm_model),
            )
            if not (val or "").strip()
        ]
        if missing:
            return f"KM_DOC_ENTITY_ENABLED=true 但缺失 {','.join(missing)},实体级不生效"
        return None

    def require_fulltext_ts_config(self) -> str:
        """全文检索分词配置白名单校验,白名单外值 RuntimeError 拒启。

        dev/prod 同(降级=换词法,不存在安全降级形态,不照抄
        websearch 的 dev 告警降级);get_settings 启动期调用。"""
        try:
            return validated_fulltext_ts_config(self.rag_fulltext_ts_config)
        except ValueError as e:
            raise RuntimeError(str(e)) from None

    def require_fulltext_ranker(self) -> str:
        """关键词排序白名单校验,白名单外值 RuntimeError 拒启(dev/prod 同口径)。"""
        try:
            return validated_fulltext_ranker(self.rag_fulltext_ranker)
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
        # (mock 回调验签可伪造):mock 通道开启时密钥必须显式配置
        # 且不得为仓库公开的 dev 占位值——否则任何人可伪造 PAID 回调核销账单
        if self.billing_mock_enabled and self.billing_mock_secret in (
            "",
            "dev-mock-payment-secret",
        ):
            raise RuntimeError(
                "prod fail-closed: KM_BILLING_MOCK_ENABLED 开启时"
                " KM_BILLING_MOCK_SECRET 必须显式配置且不得为 dev 占位值"
            )
        # websearch 组合残缺(供应商已配缺 key / 供应商非法值)在 prod
        # 拒启而非静默降级——配置错误必须显式暴露(dev 由 lifespan 告警降级)
        ws_misconfig = self.websearch_misconfiguration()
        if ws_misconfig:
            raise RuntimeError(f"prod fail-closed: {ws_misconfig}")
        # 重排组合残缺同口径拒启(provider 非法值由
        # require_rerank_provider 在 dev/prod 一律拒,此处只管残缺族)
        rk_misconfig = self.rerank_misconfiguration()
        if rk_misconfig:
            raise RuntimeError(f"prod fail-closed: {rk_misconfig}")
        # 文档实体抽取组合残缺同口径拒启(缺省关=零存在感,不校验)
        de_misconfig = self.doc_entity_misconfiguration()
        if de_misconfig:
            raise RuntimeError(f"prod fail-closed: {de_misconfig}")


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.fail_closed_checks()
    s.require_fulltext_ts_config()  # 分词配置无效时拒绝启动。
    s.require_fulltext_ranker()  # 关键词排序配置无效时拒绝启动。
    s.require_rerank_provider()  # 重排供应商无效时拒绝启动。
    return s
