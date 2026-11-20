# ADR-007: 引入 LLM 网关 muniu 作为 agent 面统一模型出口

日期: 2026-09-11

状态: 已接受;定位已修订(2026-09-26,见文末「修订」)——muniu 降级为**可选端点之一**,不再作为 agent 面强制统一出口;当前部署仍走 muniu 形态,灰度结论不变(2026-09-11 三处 `.env` 切换完成,直连冒烟/e2e 问答/网关计量与计价实证全过;muniu 侧论证原稿见 muniu 仓库 docs/kongming-adr-draft.md)

## 背景

agent 面(`apps/agent`)当前直连各上游供应商的 openai 兼容端点:

- 主模型与 embedding 回退链走 dashscope compatible-mode(`llm_base_url` 缺省,009 embedding 同源回退);
- vision 经 021 注册表(`MODEL_REGISTRY_JSON`)独立条目直连;
- 密钥明文散落在部署环境(`KM_AGENT_LLM_API_KEY`、注册表条目 `api_key`)。

三个持续成本:①密钥分散,轮换/吊销需逐处操作;②无跨供应商统一计量,用量只能到各家控制台分别拉取;③无统一故障语义,单上游限流/故障直接影响 agent 可用性,换道与冷却策略缺位。

## 决策

> 在 kongming 与各上游之间引入 muniu 网关(github.com/solvela/muniu,单二进制,OpenAI 兼容入站):agent 面全部 LLM 出口(主模型/embedding/vision 注册表条目)的 `base_url` 统一指向 muniu,密钥收敛为一把可随时吊销的 muniu `mk-` key;各上游供应商真实 key 全部收敛到 muniu 的环境变量,agent 面不再持有。

muniu 提供:多上游路由与 failover(首 token 边界语义)、per-key RPM/TPM 限流与日配额、usage 计量落库与成本核算(每上游尝试一行,读侧 DISTINCT(request_id) 聚合)、语义缓存(租户级开关,灰度期关闭)、管理 API 与控制台。

## 为什么不是维持直连 / one-api / LiteLLM / 自嵌

| 备选                    | 否决理由                                                                                |
| ----------------------- | --------------------------------------------------------------------------------------- |
| 维持直连(现状)          | 上述三项持续成本不消失;多上游故障语义永远缺失                                           |
| one-api                 | 无 arm64 官方镜像;无首 token 边界语义;无 conformance 测试集;配额模型与整数 μ 口径不一致 |
| LiteLLM proxy           | Python 运行时,与单二进制零依赖运维诉求冲突;代理层性能与内存开销更高                     |
| 把网关能力嵌进 kongming | 职责越界:agent 应用不应内嵌基础设施;且无法服务 kongming 之外的调用方                    |

## 与既有决策的关系

- **ADR-002(信任边界)**:不仅不冲突,反而强化——agent 面从「持有多家供应商 key」退化为「只持有一把可随时吊销的网关 key」,泄露面从 N 收敛为 1;供应商 key 集中在 muniu 单点,轮换/吊销/审计有一致入口。muniu 与 kongming 同属基础设施信任域,不引入新的跨域信任。
- **021 出口白名单**:`model_allowed_hosts` 需纳入 muniu 主机(127.0.0.1 随 `llm_base_url` 自动入白名单;注册表条目指向 muniu 后通过)。
- **009 embedding**:embedding 客户端回退链(`embedding_base_url=None → llm_base_url`)使 embedding 通道随主模型自动走网关,零配置改动;`text-embedding-v2` 经 muniu `model_routes` 别名路由,25/批的分批约束(dashscope 单批上限)在 kongming 侧不变,透传后上游约束不变。

## 灰度与回滚

1. muniu 侧建租户 `kongming` 与专用 key(`muniu` 仓库 `scripts/bootstrap-kongming.sh`;起步 rpm 120,按现有用量 ×2 回填);
2. 模型别名对齐(`muniu` 仓库 `docs/kongming-alignment.md`,2026-09-11 已回填:qwen-plus / qwen-vl-max / text-embedding-v2,别名与上游模型名直通);
3. kongming 侧切换仅三处:`KM_AGENT_LLM_BASE_URL` → `http://127.0.0.1:8787/v1`、`KM_AGENT_LLM_API_KEY` → mk- key、注册表 qwen-vl 条目的 base_url/api_key 同步;embedding 与白名单零改动;
4. 灰度一周(2026-09-12 修订:个人本地项目低流量,生产式「每日对账,差异 ≤1%」不适用——分母近零且上游账单天级延迟;判据改为**一周内正常使用无 5xx/无挂起/问答行为无回归,月末与上游 dashscope 账单做一次量级核对**;muniu `scripts/reconcile.sql` 保留作按需自检工具)→ 全量 → 本 ADR 转「已接受」;
5. 回滚路径:`.env` 三处改回原直连值即恢复,muniu 无状态参与,回滚零成本。

## 后果

- 正面:统一密钥管理与轮换入口;跨供应商统一计量与成本视图;统一故障语义(首 token 边界 failover);语义缓存为后续降本留闸。
- 负面:新增一个需运维的常驻进程(单二进制,运维面小);多一跳网络延迟(实测首字节 p50 增量 ms 级,muniu docs/benchmarks);muniu 自身成为可用性单点(其故障语义与生产观察期为该风险的缓解证明)。

## 成本对账(055,2026-09-13)

接入网关后出现**双轨计价**:产品轨(开发者门户自有牌价,token×厘,`sys_invoice`)与成本轨(muniu credit(1e-9) 逐次计量)。双轨本身合理——对租户收费是产品定价,允许与成本解耦;缺陷是接入时**零对账管道**,上游改价/计量漂移/路由打错价档均不可见。

- **对 muniu HANDOFF「下一步 6b」原裁决的修订**:原裁决为「kongming 实现 ingest 接收端点接收 muniu usage 推送 + 下线自有模型计价」。本 ADR 修订为**只读旁路对账**(理由三条):①muniu 计量汇为单 sink 插拔,ingest 通道已归控制面锦官且跨仓联调闭环,为对账改数据面投递拓扑代价与收益不成比例;②锦官按 key 归账用户并驱动钱包,kongming 若并行接收同批行将形成第二套归账真相,对账问题递归;③kongming 租户身份与锦官用户身份未统一,自有牌价计费是租户产品能力,「下线」超出了成本可见的原始诉求。
- 落地:`GET /system/billing/cost-reconciliation`(管理面只读)——muniu `GET /admin/v1/usage`(credit 口径)与 `sys_llm_usage`/`sys_invoice` 同期汇总并列,偏差率超阈值(缺省 ±0.2)落审计告警;成本侧不可用时报告显式标注,不影响计费主链路。muniu 侧零开发、零契约变更。
- 长期演进:锦官统一身份后,牌价/成本/钱包在控制面合流,对账升级为从锦官取数(muniu HANDOFF 6b 的最终归宿)。

## 修订(2026-09-26):muniu 是可选端点,不是强制出口

用户裁决:不应强制 kongming 按经 muniu 走;muniu 的定位是一个**类似反代中转站的自建端点选项**(类比 ollama),部署者可在官方直连端点与 muniu 之间自由选择。原「agent 面全部 LLM 出口统一指向 muniu」的表述**废止**。

- **技术事实不变**:agent 面配置层(`llm_base_url`/`MODEL_REGISTRY_JSON`)本就接受任意 OpenAI 兼容端点,官方直连从来是一等公民——原 ADR 收敛的是「默认部署形态」,不是能力边界。本修订把这一事实升格为架构口径,并补齐选型参考。
- **选型参考**:`docs/model-providers.md` 官方端点预设目录——各家官方 OpenAI 兼容端点、各家 Anthropic 分支端点(消费路径说明)、muniu 与 ollama 等「自建网关/本地推理」类条目。选 muniu 的理由仍是原 ADR 的三项成本(密钥收敛/统一计量/failover);不选时这三项成本自担,零架构阻碍。
- **现网不动**:当前部署仍是 muniu 形态,灰度与月末对账照旧;`.env` 三处改回直连即回官方形态,切换成本与原回滚路径一致。
- **供应商侧配套**:官方端点清单与各家 Plan 订阅端点红线以 muniu 仓 `docs/provider-presets.md` 为交叉核对源(2026-09-26 版)。

## 修订(2026-11-20):身份权威改归独立 IdP 虎符

本 ADR「成本对账」小节以「锦官统一身份」为前提的两处长期演进表述(「kongming 租户身份
与锦官用户身份未统一」「长期演进:锦官统一身份后……」),其身份权威改为平台级独立
IdP 虎符(`longzhong/hufu`,标准 OIDC OP,见 ADR-006 追加节):

- 孔明以可选 OIDC provider 接入,本地认证与本地会话签发保持默认;关闭 provider 即回落,
  孔明可独立发布。互联只经标准 OIDC(发现文档/JWKS/授权码 + PKCE/`sub` 与 profile claims),
  不共享库/DB、不依赖私有端点。
- 身份权威不碰账本,资金锚仍在锦官 `users.id`(三仓影子映射)。
- 成本对账从身份权威取数仍属后续演进,不改变本 ADR 任何计价与对账口径。
