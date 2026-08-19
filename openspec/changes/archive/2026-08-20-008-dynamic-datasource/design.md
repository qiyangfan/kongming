# 008 动态数据源 — 设计

## Context

现状(详见 proposal Why):`sys_datasource` 空表无模型/仓储;`internal/executor.py`
`_ENGINES` dict + `dispose_read_engines()` 全量清理钩子已预留,`SUPPORTED_KEYS=("default",)`
是唯一事实源;`internal/service.py` 与 `internal/meta.py` 对非 default 硬编码 400
`UNSUPPORTED_DATASOURCE`;`config.datasource_encrypt_key`(KM_DATASOURCE_ENCRYPT_KEY)
已配 prod fail-closed 但无消费方;cryptography 已是主依赖(基线为 AES-GCM 预留);
`datascope/repository.py` 的 `probe_column_type(conn, schemas=...)` 已按连接参数化,
天然支持换连接探测目标库;datasource_key 契约链(api→agent→两个 internal 回调)
已全透传,前端不发。约束:ADR-002(凭证不出控制面)、ADR-004(0005 新迁移)、
ADR-005(契约生成)、宪法分层(domain 零框架,池失效等副作用经 Protocol 端口反转)。

## Goals / Non-Goals

**Goals:** 管理端点 CRUD+启停+测试连接+选项;凭证 AES-GCM;动态池按 key 惰性
建/定向 evict;执行路由与 meta 内省切真路由且防御层全等;chat 入口预检;
web 管理页+对话页切换器;全量审计。

**Non-Goals:** 不碰 agent 面;不实现 fetch-metadata/embedding/RAG 召回(rag 变更);
不引入专用只读登录角色(维持 004 裁定:连接级只读+防火墙双闸;动态源账号只读
权限由注册方保证,管理页文案提示);多方言不做。

## Decisions

### D1 域包与依赖方向

新域包 `kongming/datasource/`:models(Datasource 领域模型+连接信息值对象,
零框架)→ ports(DatasourceRepo/CredentialCipher/PoolEvictor Protocol)
→ service(用例:page/create/update/enable/disable/delete/test/options;
唯一性预检+保护规则+审计编排)→ repository(SQLAlchemy 实现)+ cipher
(AES-GCM 实现,infrastructure 层文件)。router 挂 `/system/datasource/*`,
全端点 `require_perm("system:datasource:manage")`(options 例外,仅登录)。
import-linter 补 datasource models/service/ports 零框架契约(同 system 域)。
**契约名对齐**:pyproject「auth 域不反向依赖业务域」的 forbidden_modules
现有前向猜测名 `kongming.datasource_admin`(基线期预留,包从未落地),
本变更落地包名为 `kongming.datasource`——契约必须同步改为此名,否则
auth 反向依赖防线对真实包静默失效(R1 评审实证);`rag_admin` 猜测名
留待 rag 变更核对,本变更不动。

池失效反转:datasource.service 不 import executor;ports 定义
`PoolEvictor.evict(key) -> None`(async),router 装配时注入 executor 实现,
service 在**事务 commit 之后**调用(上游 afterCommit 同语义,防僵尸池)。

### D2 凭证加密(cryptography AES-GCM)

- 格式:`base64(iv[12B] || ciphertext+tag[16B])`,密钥 base64 16/32 字节
  (AES-128/256-GCM 兼容),与上游 CredentialEncryptor 逐字节同构(逻辑参考)。
- 密钥读取:config.datasource_encrypt_key;prod 缺配已在启动 fail-closed。
  **dev 降级**:密钥缺省时用固定 dev-only 密钥(代码内常量+启动告警日志),
  与种子超管 DEFAULT_ADMIN_PASSWORD 固定缺省同哲学;密文可跨 dev 环境
  重放,无安全风险(prod 强制真密钥)。dev 固定密钥仍走真 AES-GCM
  round-trip(优于上游 dev 明文直通)。
- 编辑密码留空=不改(repository 不触碰 password_enc 列,沿用 007 COALESCE
  路径教训:专用 update 语句只更新提交字段);改密码后池失效(D5)。
- 解密消费方仅两处:建池(executor 装配 DSN)与测试连接(编辑态留空补位)。
  DTO/日志/审计永不出现明文或密文。

### D3 0005 迁移(ADR-004)

`sys_datasource` 补两列:`name VARCHAR(128) NOT NULL`、
`default_schema VARCHAR(64) NOT NULL DEFAULT 'public'`。表为空,无回填负担;
不加 CHECK(key/default_schema 合法性由传输层 pattern 收口,007 role_key 同款)。
不改 datasource_key 的硬 UNIQUE(spec「含禁用记录校验」与之一致,无软删列)。

### D4 执行路由与 meta 的动态化

`internal/service.py` 移除 `!= "default"` 硬编码,换 `DatasourceResolver`
(新 repository 直查 sys_datasource):不存在/禁用/越属(tenant_id ≠ 请求租户)
统一 `InvalidDatasource` → 400 `DATASOURCE_INVALID`,**同形不区分原因**
(防 key 探测;审计落 verdict=BLOCKED、reason 含分类,内部可溯)。
校验通过后:解密→`executor.get_or_create_engine(key, dsn)` 取动态池→
**事务内** `SET LOCAL search_path` 至 `<default_schema>, public`——
schema 名经**传输层 pattern + 双引号包裹与 `"` 转义双闸**(与默认路径
逐行同款,防库内脏数据纵深缺口)→ 防火墙初检→data_scope 改写→复检→
fetchmany 截断,层序与默认路径逐行同构。**边界集对齐**:schema 边界校验
check_schema_scope 与 data_scope 列探测均以 `{default_schema, public}`
为集(与 search_path 两段对齐——动态源 SQL 命中 public 表时探测不 miss,
降级触发面与默认路径一致);`probe_column_type` 换动态池连接执行(签名
已支持);角色/部门查询仍在控制面库。
`internal/meta.py` 同构:非 default 时限 default_schema 单 schema 内省,
保留表排除判定复用 sqlfw 同一函数。
**查询面 pattern 收口**:/chat/query 与 /internal/* 两个请求模型的
datasource_key 补传输层 pattern(与注册边界同一规则;当前仅 max_length,
虽无注入面——参数化查询+dict key——但口径一致性收口,R1 观察项)。

默认路径逐字节不变(零回归纪律:现有 205 测试不改语义)。

### D5 连接池生命周期

executor:`get_or_create_engine(key, *, dsn)` 异步化+模块级 `asyncio.Lock`
防并发建双池(上游 computeIfAbsent 同语义);新增 `async evict_engine(key)`
(pop+dispose,不存在则 no-op);`dispose_read_engines` 现状无调用点
(R1 评审实证:lifespan 仅 seed 无 teardown)——本变更**补 lifespan
teardown 接线**,作进程收尾清池。evict 为尽力而为:调用失败仅 error 日志
不阻断管理操作响应(旧池残留窗口随目标库凭证轮换自然收敛;多 worker
部署的跨进程失效见 Open Questions 挂账)。
建池参数与 default 完全同构(pool_size=5/max_overflow=0/连接级只读/
statement_timeout=30s)。DSN 由 host/port/database/username/明文密码组装
(asyncpg 连接串,密码仅存在于内存局部变量)。
触发 evict 的时机:datasource.service 在 enable/disable/update(连接信息列
任一变化,不含 name)/delete 的 commit 之后调 PoolEvictor;**仅改 name 不 evict**。

### D6 测试连接

`POST /system/datasource/test`:接收连接信息明文入参,asyncpg.connect
(timeout=3s)直连 `SELECT 1` 即关;不落库、不建池、不落审计(无状态操作)。
编辑态(id 入参+password 留空)服务端取库存密文解密补位。
失败 reason 收敛为驱动错误类别文案(超时/拒连/认证失败/库不存在)
+「凭证不可用」类(编辑态补位时密文不可解:密钥轮换/损坏,不暴露密文
与密钥细节);永不回显密码与明文连接串。

### D7 选项接口与前端切换器

`GET /system/datasource/options`:仅登录(无 manage 权限),租户上下文
与 chat 同构(X-Tenant-Id→白名单复校,复校查询在 datasource repository
自备,不跨域 import chat);返回**首位合成 default 项**
(`{datasource_key:"default", name:"默认数据源"}`)+当前租户启用行,
前端免特殊分支——主 spec「数据源选项接口」scenario 已随本变更 MODIFIED
立约(R1-S1:原文「返回当前租户启用数据源」字面不含合成项,偏差转正)。
router 归属:datasource 域独立 router(URL 前缀 /system/datasource 与
system 域同族不同包);管理端点全局视角(X-Tenant-Id 无语义,与 system
域头注释惯例一致,创建时租户归属表单显式指定、下拉数据复用 007 租户
只读清单);options 为用户面例外消费 X-Tenant-Id,不违反 system 域包
头注释(该注释管辖 system 域包自身),但 `api.ts`「头对非 chat 端点
无语义」注释将过时,tasks 内同步修订。
对话页切换器组件与租户切换器同构(localStorage
`km.datasource` 单源+按租户 calibrate,切租户重置首项),选中 key 随
/chat/query 发送;api.ts 统一注入免遗漏。react-query 键含数据源维度。

### D8 chat 入口预检

`chat/service.query` 在租户解析后注入 `DatasourceChecker` 端口
(datasource repository 实现):非 default 时校验存在+启用+归属,
失败 400 同形(不进 LLM 链路、不建会话);会话落库 datasource_key 语义不变。
校验结果(连接信息)不传给 agent——agent 只透传 key(ADR-002 不变)。

### D9 审计与权限点

写操作 5 类(datasource.create/update/delete/enable/disable)经 auth 域
SqlOperLogRepo 同事务落 sys_oper_log(007 组合模式);require_perm 403
perm_denied 审计自动生效。seed PERMISSIONS 增
`("system:datasource:manage", "数据源管理", 4)`,SUPER_ADMIN 全量绑定
自动覆盖(现有 bind 全 PERMISSIONS 逻辑),DEVELOPER 不绑(管理面专属)。

## Risks / Trade-offs

- [动态源 data_scope 探测跨库]:目标库无 create_by 列时降级放行(与默认路径
  无列降级同语义)→ 管理页文案提示「行动级过滤依赖 create_by 列」;防火墙+
  连接级只读兜底不破。
- [dev 固定密钥降级]:密文可被本地重放 → 启动告警+prod fail-closed 已在;
  .env.example 注记生成方法。
- [池内存膨胀]:key 无上限注册 → 池 5 连接/key 且 evict 及时,量级可控;
  不引入 LRU(过度设计),观察项登记。
- [同库异 schema 假动态源]:dev 冒烟可注册指向同实例 default_schema=
  tenant_sakila 的源 → 语义合法(search_path 首段切换),冒烟即用它,
  不另建库;文档注明这只是冒烟形态。
- [default 选项合成]:api 合成首位项与 sys_datasource 无行之间的语义差 →
  选项接口注释钉住;注册保留字 default 已被 pattern+保留字双拒,无冲突面。

## Migration Plan

0005 加列(空表,瞬时);deploy 序:迁移→api(新端点+路由)→web。
回滚:downgrade 去列(有数据时需先清行动态源行,downgrade 注记);
api 回滚即恢复 fail-closed,无数据面风险。无破坏性步骤。

## Open Questions

- 冒烟用动态源形态(同库异 schema vs 独立库)——apply 期按 .example-data
  装载实况定,不影响 spec/任务拆解。
- 动态源账号只读权限的强校验(建池时探测 default_transaction_read_only
  之外的授权面)——挂账观察,同 004 km_readonly 裁定一并随运营反馈评估。
- **密钥轮换重加密通道**(R1 挂账):轮换 KM_DATASOURCE_ENCRYPT_KEY 后
  存量密文全不可解,目前无迁移/重加密通道;需轮换时以「编辑重填密码」
  逐源修复,随运营事件再评估专用工具。
- **多 worker 池失效**(R1 挂账):PoolEvictor 只清进程内池,api 单 worker
  为池失效正确性前提(现状 Dockerfile 无 --workers);多 worker 部署需配
  Redis 失效广播,随部署形态变更评估。
- 「datasource_key 与租户归属不可修改」scenario 现挂主 spec「元数据抓取」
  requirement 下(上游平移布局),本变更已兑现该强制——rag 变更落地
  fetch-metadata 时顺手挪位到注册 requirement。
