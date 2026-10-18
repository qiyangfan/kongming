# Tasks

## 1. 依赖迁移与安全语义测试先行

- [x] 1.1 `pyproject.toml`:`mcp` 从 dev 组迁 prod 组,版本窗 `>=2.3,<3`;
  按 SDK 2 迁移指南整理 import 对照表(design D3);验证:`uv sync` 成功且
  锁定 2.3+,ruff/mypy 过
- [x] 1.2 失败测试先行(入站安全语义钉板):1MB 闸 413 先于鉴权 401 先于
  body 缓冲、notifications 短路 202、key 撤销即 401、租户隔离(key 只见
  绑定 Bot)——按现实现先全部绿(基线),重写后不落一盏;验证:pytest
  该组全绿并留档基线输出
- [x] 1.3 失败测试先行(出站行为钉板):工具快照发现注册、断连降级不影响
  其余、异常归一分类——同上先立基线;验证:pytest 该组全绿

## 2. 出站收编(client.py → SDK session)

- [x] 2.1 新增用例先红:新修订 server(无会话握手)直连发现工具(SDK
  FastMCP 以 2026-07-28 形态起 server)、旧修订 server 透明兼容、协议修订
  不匹配归一为同步错误分类;验证:新用例在旧实现上红
- [x] 2.2 重写 `mcp/client.py` 为 SDK `streamablehttp_client`+`ClientSession`
  适配(异常归一、响应体上限守卫、key 掩码保留;service 层接口零改动),
  删除手写帧聚合/会话透传;验证:1.3 基线组 + 2.1 新用例全绿
- [x] 2.3 `test_mcp_live.py` 全量回归(真实 server 双形态);验证:live
  测试通过

## 3. 入站收编(inbound.py → SDK server 挂载)

- [x] 3.1 新增用例先红:SDK 客户端打平台 /mcp 端点一致性(initialize
  声明当前修订、旧修订客户端兼容、tools/list/tools/call 全链)、闸/鉴权
  中间件次序断言;验证:新用例在手写实现上红(修订声明项)
- [x] 3.2 重写 `mcp/inbound.py` 为 SDK server 挂载(api-key/1MB 闸前置
  ASGI 中间件,key→Bot 绑定与 tools/call 代理身份在 tool provider 回调
  薄适配);验证:1.2 基线组 + 3.1 新用例全绿
- [x] 3.3 双向官方栈一致性测试收口(SDK 客户端 ↔ 平台端点;平台出站 ↔
  SDK server);验证:一致性套件通过并纳入默认门禁

## 4. 回归与收尾

- [x] 4.1 `make check` api 子集全量(ruff+mypy+pytest)通过;import-linter
  契约零改动确认(SDK 只出现在 adapter 两文件)
- [x] 4.2 真实 MCP server 端到端复验(113 验证过的外部 server 配置);
  HANDOFF 补「SDK 2 收编后协议面口径」(修订声明值/兼容矩阵)

## Workflow follow-up

- 归档走 `/opsx:archive`
- 顺带的依赖批量升级归 `stack-refresh-2026-10` 变更,勿在本变更内夹带
