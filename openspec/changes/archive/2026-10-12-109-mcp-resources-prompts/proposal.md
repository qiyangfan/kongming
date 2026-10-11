# Proposal

## Why

mcp-interop 域当前只消费外部 MCP server 的**工具**原语(tools/list →
动态注册);MCP 协议的另外两类原语——resources(资源)与 prompts(提示
模板)——未接入,协议完备性缺一角。产品待办已登记「MCP resources/prompts
原语」。资源读取让 Agent 能引用 server 侧的数据文档(prompt 不用搬运
数据),提示模板枚举让 server 侧沉淀的提示资产可见——两类原语的发现与
只读消费是自然的下一片。

## What Changes

- 连通性测试扩展:initialize 握手 + tools/list 之外增加 resources/list
  与 prompts/list 探测(原语可选:server 不提供该类原语如实计数 0,
  不算失败),配置详情展示三类原语清单。
- 资源消费:每 server 注册一个读资源工具(`mcp__<serverKey>__read_resource`,
  uri 参数校验 MUST 命中该 server 已枚举资源白名单),资源内容作为
  外部数据进工具观察——截断受上限约束、进观察前过既有注入检测面;
  执行走既有 MCP 工具执行纵深防御(requirement 不变,自动继承)。
- 提示模板消费(只读):配置详情提供模板清单(name/description/
  arguments)与按名拉取的内容预览(prompts/get 渲染结果),供管理面
  查看;模板内容作为外部文本处理(展示前过注入检测)。
- server 禁用/删除:其资源工具与原语清单随之消失(与既有工具语义同形);
  单 server 原语探测失败 MUST NOT 影响其余 server(降级记录,同既有
  断连降级口径)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `mcp-interop`:新增「资源与提示原语消费」requirement(原语探测、
  资源读取工具、模板只读预览、白名单与截断约束);「MCP server 配置
  管理」requirement 的连通性测试语义随之扩展(探测含三类原语)。

## Impact

- `apps/api`:mcp-interop 域(连通性探测扩展、资源清单/模板清单存库、
  read_resource 工具注册与执行适配);新迁移(原语清单快照列,纯增)。
- `apps/web`:MCP server 配置详情页原语清单展示(资源/模板 tab 或分区);
  类型经管线生成。
- `apps/agent`:零改动(工具执行恒经既有回调)。
- contracts 经管线重生成;schema 仅纯增列/表。
