# Tasks

## 1. 测试基建

- [x] 1.1 dev 依赖组加 `mcp`(官方 SDK,仅测试起真实 server 用);uv lock 同步
- [x] 1.2 新增 `tests/test_mcp_live.py`:FastMCP(SSE)同进程 task 拉起于 127.0.0.1 随机端口,提供回显工具/resource/prompt 各一;SDK 缺失或起服务失败 pytest.skip

## 2. 真实闭环验证

- [x] 2.1 生产 `McpClient` 全方法面对真实 server 闭环:probe → list_tools → list_resources/read_resource → list_prompts/get_prompt → call(回显断言);整体超时兜底

## 3. 收尾

- [x] 3.1 门禁(ruff+mypy+pytest;新测试在本机真跑通过)
- [x] 3.2 变更归档(openspec archive)
