# Design: add-mcp-breaker

## Context

出站链路现态(adopt-mcp-sdk2 后):`HttpMcpCaller` 为无状态适配器(每次调用
即开即用连接,不引入常驻连接池),错误在 `_run` 梯子归一为 `McpCallError`
五类;消费方四个组合根装配点(mcp/bot/chat/toolcall 四个 router 各自
`new HttpMcpCaller`),每个请求新建 `McpService`。运行时热路径 =
`McpService.call_tool` → `caller.call/read_resource`;管理面路径 =
`test_connection/sync/preview_prompt` → `caller.probe/get_prompt`。

## Goals / Non-Goals

- Goal:运行时出站对持续不可达 server 止损(快速失败),恢复自动探测;
  管理/诊断路径零影响;零 schema/零端口契约变更。
- Non-Goals:见 proposal(状态可视化/分布式计数/工具级粒度/禁删挂钩)。

## Decisions

### D1 落点=McpCaller 端口装饰器,不是 service 内拦截

熔断是横切关注点(与权限/审计同类),落 service 用例层会把状态机调用散进
每个用例;装饰器包 `McpCaller` 端口则 service 层零感知。按方法天然分流:
`call/read_resource`(运行时)拦截+记账,`probe/get_prompt`(管理面)
直通不拦——与 spec「管理面不受限」在结构上互锁(不是约定,是类型面保证)。

### D2 计数维度=endpoint,不是 server_id/server_key

`McpCaller` 端口方法签名只有 endpoint(无 server 标识);按 endpoint 计数
则端口契约零变更(四个测试 stub 同步免改)。endpoint 与 server 在本域
一一对应(配置行唯一),语义等价;endpoint 非凭证(审计既有明文记录),
日志可见无泄漏。server 删除后同名 endpoint 重建继承陈旧计数:概率极低且
半开探测(缺省 60s)自然纠偏,不做禁删挂钩(Non-goal)。

### D3 状态机=CLOSED/OPEN/HALF_OPEN 三态纯类,同步方法,时钟注入

- `allow(key)`/`record_success(key)`/`record_failure(key)`/`reset(key)`
  四个同步方法——无 await,单事件循环内检查-置位天然原子,不引入锁;
- 时钟经构造注入(`time.monotonic` 缺省),测试用 fake clock 无 sleep;
- 半开语义:`allow` 在 OPEN 且冷却期满时转 HALF_OPEN 并放行本请求;
  HALF_OPEN 中其余 `allow` 拒绝;`record_success` 复位 CLOSED,
  `record_failure` 回 OPEN 重计冷却;
- 新错误分类 `unavailable`(「熔断冷却中,未出站」)——独立于既有五类,
  回灌文本明确「未出站」以区别于远端失败;半开探测失败的回灌保持叶子
  错误类别(timeout/conn),不吞成 unavailable。

### D4 计入分类=timeout/conn 两类;成功清零与联动复位

熔断是瞬时不可达止损器:timeout(超时)/conn(连接失败)计入;
protocol(协议不匹配)/remote(远端错误,含参数错/工具错)/unsupported
(原语不支持)是确定性行为,计入会把单工具问题放大成整 server 熔断且
掩盖真实报错。任何成功出站清空计数(健康 server 的零星单次失败永不触发)。
联动复位仅一处:`McpService.sync` 探测成功后 `reset`(构造器可选注入,
缺省 None 零感知)——管理员确认可达后运行时立即可用。disable/delete
不挂钩(Non-goal:service 层已拒,条目自然收敛)。

### D5 装配=模块级惰性单例 + 四组合根统一包装

熔断状态必须跨请求存活 → 进程级单例(形态同 `get_cipher` 既有先例);
`get_mcp_breaker()` 读 Settings 构造,`breaker_enabled=False` 时返回
None。四个组合根把 `HttpMcpCaller(...)` 换成 `build_mcp_caller(settings)`
工厂返回(直通或包装),装配点单点收敛(≥2 处抽公共组件宪法);测试全部
手动构造 `McpBreaker` + stub caller 注入,不触单例(无 pytest 污染,
无需测试重置钩子)。

### D6 可观测=OPEN 时 log.warning 一条

熔断打开/半开失败回 OPEN 各记一条 warning(server endpoint+连续失败数),
恢复成功记 info;不做管理面可视化端点(Non-goal,状态生命周期短)。

## Risks / Trade-offs

- [半开单探测吞吐] 冷却期满后第一个请求探测期间,该 server 其余请求快速
  失败。→ 探测窗口=单次出站预算(≤16s),恢复场景本就异常期,可接受。
- [进程重启清零] 重启后对已恢复 server 首调用正常,对仍宕机 server 重新
  累计 3 次才熔断。→ 内存态是部署形态自洽选择,分布式计数属 Non-goal。
- [endpoint 变更绕过计数] 管理员改 endpoint 再触发旧故障:新 endpoint
  重新计数。→ 语义正确(endpoint 即新目标)。

## Migration Plan

零迁移:纯新增组件+装配替换,缺省启用但只影响「已连续失败 3 次」的
异常路径,健康路径零感知;`KM_MCP_BREAKER_ENABLED=false` 一键回逐字
旧行为。无 schema、无契约、无前端面变化。

## Open Questions

(无——排序裁决/粒度/计数维度均已在 D1–D4 收口。)
