# Design

## Context

089 框架三件套(域清单/策略器/出站切面)已就绪,本变更是框架第一性验证之外的
第一个全新域:验证「新域接入=声明清单+实现策略器」的承诺真实成立,同时暴露
框架对「多域并存」的最后一处单域假设(单执行器/单配额值)。

## Goals / Non-Goals

- Goals:websearch 域全治理语义(注册/白名单/脱敏/配额/审计/启停)零特判接入;
  演示可跑(配 key 即有联网检索)。
- Non-Goals:见 proposal(HTTP 连接器/正文抓取/热切换/缓存均不做)。

## Decisions

### D1 供应商适配:两家首发,统一检索卡

`tavily`(POST /search,Bearer 头,`results[].{title,url,content}`)与
`bocha`(POST /v1/web-search,Bearer 头,`data.webPages.value[].{name,url,
summary|snippet}`)。执行器内归一为 `{"results": [{"title","url","snippet"}]}`;
字段映射收在适配函数单点,新增供应商=新增一个映射分支。白名单主机按供应商
常量单源(tavily→api.tavily.com;bocha→api.bochaai.com),端点由供应商派生,
部署不可单独改端点(防「配置了白名单外端点」的组合攻击面)。

### D2 按域执行器:网关微扩,缺省回落

`ToolGateway.__init__` 增可选 `domain_executors: dict[str, Executor]`;
`execute` 取 `domain_executors.get(domain) or executor`。knowledge 域不声明
专属执行器走回落=既有行为零回归;websearch 声明专属(需装配鉴权头)。凭证
进执行器构造参数(组合根装配),不进 DomainSpec(清单仍是无凭证数据)。

### D3 按域配额:分桶映射替换单值透传

service 现状 `quota = DOMAIN_TOOLS.get(tool_name) and self._knowledge_quota`
把单一配置值当作全域值。改为 `dict[str, int | None]` 按域分桶
(`{"knowledge": cfg, "websearch": cfg}`),取值 `bucket.get(DOMAIN_TOOLS.get(
tool_name, ""))`;knowledge 桶值不变=行为零回归。`DOMAIN_TOOLS` 增
`"web_search": "websearch"`(工具名→域标签单源,审计与配额共用)。

### D4 注册条件与 fail-closed 校验

注册双条件:供应商非空 ∧ API key 非空(websearch 不受 localhost 白名单约束
——出站对象是公网 API 主机,白名单语义=域清单 whitelist_hosts 精确主机)。
配置组合校验进 `fail_closed_checks`:供应商配置了合法值但 key 缺失 → 拒启
(prod)/告警(dev);供应商配置了非法值 → 同样显式失败(枚举误配不静默)。
均未配置=域不注册,零感知(与 siku 未配置同形,合法部署形态)。

### D5 观察形态与工具 schema

`web_search` ToolDef 参数:`query`(必填,1–500 字符)、`count`(可选,
1–10,缺省 5)。观察:`{"count": n, "results": [{"title","url","snippet"}]}`,
空结果如实 `count=0`。策略器 redact 是 allowlist 单点:非 dict/缺字段一律丢,
不透传供应商响应原文。description 写明「时效性问题、库内无据的问题先联网检索;
引用时带链接」。

### D6 权限种子与推荐集

`tool:websearch:search`(sort 39,续 090 的 38)。显式授予制:不进
DEVELOPER 推荐集(查询内容外发第三方 + API 成本;与 knowledge_search 同哲学),
SUPER_ADMIN 由既有增量逻辑自动覆盖。

### D7 契约与 web 零漂移

不改任何 pydantic 响应模型:域清单摘要端点返回形态不变(websearch 域条目
自动出现);`make contracts` 预期零 diff,门禁照跑验真。web 管理面域列表
自动多一行,无代码改动。

## Risks / Trade-offs

- 供应商响应形态漂移(第三方 API 演进)→ 执行器归一函数对畸形报文显式
  protocol 失败,不静默吐空;失败文案带异常类型可溯源。
- 检索卡 snippet 可能含注入文本 → 走既有 ReAct 工具观察通道,LLM 输入面
  已有 prompt-guardrails(030)兜底;本域不做二次内容过滤(记录为已知边界)。
- demo 依赖外部 key → 无 key 时域零感知,平台其余能力零影响(081 同形)。

## Migration Plan

纯新增,无数据迁移;未配置供应商的部署重启后行为与升级前逐字段一致。

## Open Questions

(无)
