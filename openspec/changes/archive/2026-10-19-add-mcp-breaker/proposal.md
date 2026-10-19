# Proposal: add-mcp-breaker(MCP 出站运行时熔断)

## Why

MCP 出站调用为每次即开即用连接(adopt-mcp-sdk2 现态),单次失败要烧满全程
预算(缺省 15s)才归一为结构化错误。当外部 MCP server 宕机/网络不可达时,
agent 循环对同一 server 的连续工具调用会反复烧满超时预算——3 个工具步即
45s 纯等待,且毫无快速止损语义。ruoyi-ai 对齐调研(`docs/ruoyi-ai-alignment.md`)
将「MCP 运行时熔断(连续失败自动冷却)」登记为净缺口(roadmap 第四梯队
2026-10-18 段),现前置项(adopt-mcp-sdk2、Milvus 排序裁决)均已收口,开工
解除阻塞。

## What Changes

- **运行时熔断**:对启用 server 的运行时工具执行(tools/call 与
  read_resource 出站)增加连续瞬时失败(_timeout/conn_ 两类,server 不可达
  的信号)自动冷却——连续失败达阈值即进入冷却期,期内运行时调用快速失败
  (不出站),错误以新分类 `unavailable` 归一回灌(agent 可自愈改道语义,
  与既有五类归一同形)。
- **半开恢复**:冷却期满自动放行单个探测请求(半开态)——成功即复位回
  正常态,失败则重计冷却;半开态其余并发请求仍快速失败。
- **管理面不受限**:连通性测试/发现同步/模板预览是管理员显式恢复与诊断
  动作,SHALL NOT 被熔断拦截;发现同步成功 SHALL 显式复位该 server 熔断
  状态(管理员确认可达后运行时立即可用,不等半开)。
- **确定性错误不计入**:protocol/remote/unsupported 三类是确定性行为
  (协议不匹配/参数错误/原语不支持),不计入熔断计数——熔断是瞬时故障
  的止损器,不掩盖确定性配置错误的真实报错。
- **配置化**:阈值/冷却期/总开关部署级环境变量可配(缺省启用,3 次/60s);
  总开关关闭时行为与现状逐字一致(零回归口径)。
- **零 schema 零对外契约变化**:熔断状态为进程内存态(不落库,重启即清,
  与「每次即开即用连接」的无状态出站形态自洽);`unavailable` 分类仅出现
  在运行时回灌文本与管理面透传文案,OpenAPI 契约面零变化。

## Capabilities

### New Capabilities

(无——本变更是 mcp-interop 既有出站链路的运行时韧性增强,不立新域。)

### Modified Capabilities

- `mcp-interop`:新增「出站运行时熔断」Requirement(计数/冷却/半开/复位/
  管理面豁免/确定性错误豁免/配置缺省),既有「MCP 工具执行纵深防御」
  Requirement 的失败回灌语义扩 `unavailable` 分类。

## Impact

- `apps/api`:mcp 域新增 breaker 状态机(纯领域逻辑,零框架依赖)+
  McpCaller 端口装饰器(运行时拦截与记账);四个组合根装配点(mcp/bot/
  chat/toolcall router)统一经单点工厂包装;McpService 增发现同步成功
  复位联动;config 增三个部署级配置项。零新表、零 Alembic 迁移。
- `apps/agent`:零改动(错误归一文本经既有回调链回灌,形态不变)。
- `contracts/openapi.json`:零变化。
- `apps/web`:零改动(管理面 test/sync 响应形态不变;熔断不拦管理面)。
- 依赖:零新增第三方库。

## Non-goals

- **熔断状态管理面可视化**:进程内存态生命周期短(冷却期分钟级、重启即清),
  不做列表展示/手动复位端点;排障可见性走日志告警(OPEN 时 warning 一条)
  与 sync/test 联动。
- **禁用/删除时清理熔断条目**:禁用期间运行时调用在 service 层已被拒
  (到不了出站),陈旧条目在半开探测后自然收敛,量级为每 endpoint 一条
  计数器,不做生命周期挂钩。
- **跨进程/分布式熔断计数**:孔明部署形态为单 api 容器(compose),进程
  内单例即完整语义;多副本形态留待真实部署需求出现再议。
- **粒度细化到工具级**:熔断以 endpoint(server)为粒度——server 不可达
  是全工具共同命运,工具级粒度会把 server 级故障碎片化。
- **既有五类错误分类变更**:timeout/conn/protocol/remote/unsupported 语义
  不动,仅新增 unavailable 一类(运行时熔断专属)。
