# Proposal: 工具网关 web 检索域(联网搜索)

## Why

通用智能体方向的工具广度缺口:当前 LLM 只能查库与查本地知识,不能联网检索,
「全能 AI Agent 助手」的演示与能力叙事缺最显眼的一块。089 工具网关框架落地时
已把 web 检索域登记为第一接入候选(框架齐备,接入=声明清单+实现策略器),
owner 2026-10-10 定调「有意义的能力都要落地,不设择机触发」——本变更兑现该登记。

## What Changes

- 新增 `websearch` 工具域(089 框架第二域):声明式域清单条目(端点按检索
  供应商配置派生,出口白名单=对应搜索 API 主机)+ 域策略器(出口校验/参数
  校验/响应脱敏 allowlist/重试分类)+ 域执行器(单次出站协议适配)。
- 检索供应商适配器:首发支持 `tavily` 与 `bocha` 两家(POST + Bearer 类鉴权
  形态),响应统一整形为检索卡(title/url/snippet);供应商由部署配置选择,
  凭证只存控制面环境变量(ADR-002 不动)。
- 工具注册:`web_search` 部署级条件注册(供应商+API key 均配置才注册,缺一
  零感知);权限点 `tool:websearch:search` 显式授予制(查询内容外发第三方,
  与 knowledge_search 同哲学,不进 DEVELOPER 推荐集)。
- 框架微扩:网关出站切面支持按域选择执行器(域执行器映射,缺省回落既有单
  执行器,既有域零回归);域配额从单一值扩为按域配置(配额第二维度语义不变)。
- 治理面零改动复用:域清单摘要/启停端点/审计域标签/域配额闸全部走 089 既有
  面;websearch 域自动出现在既有治理清单中。

## Capabilities

### New Capabilities

(无——web 检索域是 tool-gateway 能力域的域实现,不另立能力域。)

### Modified Capabilities

- `tool-gateway`: 新增「web 检索域」requirement(注册条件、供应商适配、
  脱敏观察形态、凭证治理、配额独立);「统一出站通道」requirement 增补
  按域执行器选择语义(既有单域行为零回归)。

## Impact

- `apps/api`:`toolgateway/websearch.py`(策略器+执行器+供应商适配);
  `toolcall/registry.py`(web_search ToolDef + 注册条件);`toolcall/service.py`
  (分发分支 + 按域配额);`toolcall/router.py`(域清单装配 + 按域执行器
  映射 + 端口绑定);`config.py`(供应商/key/超时/配额配置 + fail-closed
  校验);`auth/seed.py`(权限点);`main.py`(注册表装配条件)。
- Agent 面零改动(工具经既有回调与授权注入自动可见);无 schema 变更
  (域清单为部署配置);无 web 改动(治理面自动多一行);无契约漂移
  (不改任何响应模型)。
- 域策略器是安全规则类:测试先行(白名单拒绝/脱敏漏斗/注册条件/凭证不外泄),
  宪法 Article III。
- `.env.example` 增供应商配置样例。

## Non-goals

- 不做 HTTP 连接器域与内部系统连接器(各自独立后续变更;HTTP 连接器需先立
  白名单/凭证治理面之约)。
- 不收编 MCP 出站(089 Non-goal 不变)。
- 不做网页正文抓取/浏览(web_search 只返回检索卡;页面阅读是后续独立能力)。
- 不做多供应商热切换/聚合召回(单部署单供应商;换供应商=改配置重启)。
- 不做检索结果的持久化缓存(出站语义保持同步回调;缓存是后续优化项)。
