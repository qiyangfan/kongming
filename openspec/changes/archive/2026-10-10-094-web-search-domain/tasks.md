# Tasks

## 1. 域包(websearch)

- [x] 1.1 `toolgateway/websearch.py`:供应商常量单源(端点/白名单主机/tavily、bocha)、
      `WebSearchPolicy`(出口校验/参数校验/redact allowlist 检索卡/失败分类+failure_reason)、
      `WebSearchExecutor`(单次出站+Bearer 头+供应商响应归一,畸形报文 protocol 收敛)
- [x] 1.2 `toolgateway/models.py`:`DOMAIN_TOOLS` 增 `web_search→websearch`

## 2. 框架微扩

- [x] 2.1 `gateway.py`:`ToolGateway` 增可选 `domain_executors` 映射,
      `execute` 按域取执行器、缺省回落既有单执行器;既有测试零回归
- [x] 2.2 `toolcall/service.py`:域配额单值改按域分桶(knowledge 桶值不变零回归),
      新增 `web_search` 分发分支(端口缺失收敛 ERROR;观察=count+检索卡)

## 3. 注册与装配

- [x] 3.1 `toolcall/registry.py`:`web_search` ToolDef(schema/description/perm);
      `build_registry` 增 websearch 注册条件参数(供应商+key 齐备才注册)
- [x] 3.2 `config.py`:供应商/key/超时/域配额四配置项;`fail_closed_checks`
      增组合校验(供应商合法值缺 key 拒启、非法供应商值拒启,均未配置零感知)
- [x] 3.3 `toolcall/router.py`:域清单装配增 websearch 条目(端点/白名单/超时/配额),
      策略器映射、专属执行器装配(凭证组合根注入)、端口绑定 `_GatewayWebSearchClient`
- [x] 3.4 `main.py`:lifespan 注册表装配传 websearch 条件;`auth/seed.py`
      增 `tool:websearch:search`(sort 39,不进推荐集)

## 4. 测试(先行:安全规则类)

- [x] 4.1 策略器:出口白名单拒绝(零网络请求)/参数越界拒绝/脱敏 allowlist
      (评分与白名单外字段不入观察、追踪参数剥离、无链接卡片丢弃)/失败分类
      (连接类重试一次、响应类不重试)
- [x] 4.2 执行器:两供应商真实 TCP 存根报文归一/畸形报文与解码失败 protocol/
      HTTP 非 2xx protocol/读超时 protocol(慢存根定点)/连接层未送达重试恰好
      一次(accept-关连剧本);凭证只进请求头不进观察
- [x] 4.3 注册与装配:双条件注册/缺 key fail-closed 拒启/均缺零感知/
      build_registry 确定性;按域执行器路由与回落零回归
- [x] 4.4 配额分桶:websearch 超限不挤占 knowledge(limiter 键按工具分桶);
      service 分发分支观察形态(含空结果 count=0)

## 5. 收尾

- [x] 5.1 `.env.example` 增供应商配置样例与注释
- [x] 5.2 门禁:api ruff+mypy+pytest、`PYRIGHT_PYTHON_FORCE_VERSION=latest`
      双 app 0、lint-imports、openspec strict、contracts 零 diff 验真
- [x] 5.3 roadmap 089 后续清单回写(web 检索域已落地)、HANDOFF 当前状态行
