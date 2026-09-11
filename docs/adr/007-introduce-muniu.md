# ADR-007: 引入 LLM 网关 muniu 作为 agent 面统一模型出口

日期: 2026-09-11

状态: 已切换,灰度中(2026-09-11 三处 `.env` 切换完成,直连冒烟/e2e 问答/网关计量与计价实证全过;灰度一周每日对账达标后转「已接受」。muniu 侧论证原稿见 muniu 仓库 docs/kongming-adr-draft.md)

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

| 备选 | 否决理由 |
|---|---|
| 维持直连(现状) | 上述三项持续成本不消失;多上游故障语义永远缺失 |
| one-api | 无 arm64 官方镜像;无首 token 边界语义;无 conformance 测试集;配额模型与整数 μ 口径不一致 |
| LiteLLM proxy | Python 运行时,与单二进制零依赖运维诉求冲突;代理层性能与内存开销更高 |
| 把网关能力嵌进 kongming | 职责越界:agent 应用不应内嵌基础设施;且无法服务 kongming 之外的调用方 |

## 与既有决策的关系

- **ADR-002(信任边界)**:不仅不冲突,反而强化——agent 面从「持有多家供应商 key」退化为「只持有一把可随时吊销的网关 key」,泄露面从 N 收敛为 1;供应商 key 集中在 muniu 单点,轮换/吊销/审计有一致入口。muniu 与 kongming 同属基础设施信任域,不引入新的跨域信任。
- **021 出口白名单**:`model_allowed_hosts` 需纳入 muniu 主机(127.0.0.1 随 `llm_base_url` 自动入白名单;注册表条目指向 muniu 后通过)。
- **009 embedding**:embedding 客户端回退链(`embedding_base_url=None → llm_base_url`)使 embedding 通道随主模型自动走网关,零配置改动;`text-embedding-v2` 经 muniu `model_routes` 别名路由,25/批的分批约束(dashscope 单批上限)在 kongming 侧不变,透传后上游约束不变。

## 灰度与回滚

1. muniu 侧建租户 `kongming` 与专用 key(`muniu` 仓库 `scripts/bootstrap-kongming.sh`;起步 rpm 120,按现有用量 ×2 回填);
2. 模型别名对齐(`muniu` 仓库 `docs/kongming-alignment.md`,2026-09-11 已回填:qwen-plus / qwen-vl-max / text-embedding-v2,别名与上游模型名直通);
3. kongming 侧切换仅三处:`KM_AGENT_LLM_BASE_URL` → `http://127.0.0.1:8787/v1`、`KM_AGENT_LLM_API_KEY` → mk- key、注册表 qwen-vl 条目的 base_url/api_key 同步;embedding 与白名单零改动;
4. 灰度一周(每日对账,muniu `scripts/reconcile.sql`,差异 >1% 立案)→ 全量 → 本 ADR 转「已接受」;
5. 回滚路径:`.env` 三处改回原直连值即恢复,muniu 无状态参与,回滚零成本。

## 后果

- 正面:统一密钥管理与轮换入口;跨供应商统一计量与成本视图;统一故障语义(首 token 边界 failover);语义缓存为后续降本留闸。
- 负面:新增一个需运维的常驻进程(单二进制,运维面小);多一跳网络延迟(实测首字节 p50 增量 ms 级,muniu docs/benchmarks);muniu 自身成为可用性单点(其故障语义与生产观察期为该风险的缓解证明)。
