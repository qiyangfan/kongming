# Tasks: 零工具 Bot 三层闸治理

## 1. agent 内核:无工具纪律段(测试先行)

- [x] 1.1 `apps/agent/tests/test_react.py`:更新 `test_build_react_system_bot_injection` 为 `has_tools=True` 显式传参;新增 `test_build_react_system_no_tools_discipline`(无工具段标识在位/工具名与工具教学不得残留/有工具分支零回归)——先跑确认失败
- [x] 1.2 `apps/agent/tests/test_react.py`:新增图级用例——空 tools 时 LLM 收到的 system 含无工具声明且不含工具教学;`test_react_empty_tools_degenerates` 既有断言保持通过
- [x] 1.3 实现 `graph/react/prompts.py`:新增 `REACT_NO_TOOLS_DISCIPLINE` 常量;`build_react_system(bot_system_prompt, *, has_tools)` 按能力选纪律段(有工具逐字零回归)
- [x] 1.4 实现 `graph/react/nodes.py`:`react_llm` 传 `has_tools=bool(state.get("tools"))`
- [x] 1.5 agent 侧测试全绿(uv run pytest tests/test_react.py tests/test_prompts.py)

## 2. api 服务层:运行闸 fail-fast(测试先行)

- [x] 2.1 `apps/api/tests/test_bot.py`:新增 `test_run_empty_tools_fails_fast`——tools=[] 建公开 Bot,公开页运行断言 success=False、error 含「未配置可用工具」、FakeAgent 零调用;先跑确认失败
- [x] 2.2 实现 `apps/api/kongming/bot/service.py`:提取 `resolve_tools(creator_id, tenant_id, row)` 公有方法(权限求交→剔除需确认→MCP 只读补入,原内联逻辑迁移注释保留);run 在求交后 tools 空 → 同构失败形态直返(零 LLM 调用,输入护栏仍在求交前)
- [x] 2.3 api bot 域测试全绿(uv run pytest tests/test_bot.py)

## 3. store 产品层:上架闸(测试先行)

- [x] 3.1 `apps/api/tests/test_store.py`:新增 `test_publish_empty_tools_rejected`——tools=[] Bot 上架 400 含「未配置可用工具」且保持未上架;先跑确认失败
- [x] 3.2 实现 `apps/api/kongming/store/service.py`:`StoreService` 新增必选 `ToolLister` 端口,publish 在归属判定后校验求交后工具集非空,空 → `StoreValidationError`
- [x] 3.3 实现 `apps/api/kongming/store/router.py`:`_StoreToolLister` 适配器(经 bot 域 `_get_service`+`get_agent_client` 装配 `resolve_tools`,与 `_StoreRunner` 同范式)注入 `_svc`
- [x] 3.4 api store 域测试全绿(uv run pytest tests/test_store.py;既有 publish/run 用例零回归)

## 4. 全量质量门禁与契约

- [x] 4.1 `make check` 全绿(api/agent ruff+mypy+pytest,web tsc+eslint)——后台跑须盯 make-exit 行
- [x] 4.2 确认 `contracts/openapi.json` 零结构漂移(无新增端点/模型;不跑生成,直证 diff 为空)

## 5. 实测复核与收尾

- [x] 5.1 浏览器实测:商店「数据参谋」点建议问题 → 应答区显示友好错误(非伪调用文本);请求耗时显著下降(零 LLM)
- [x] 5.2 有工具 Bot 反向实证:管理后台确认既有带工具 Bot 路径不受影响(测试直证即可,不另建数据)
- [ ] 5.3 change 工件勾结 + `/opsx:archive` sync 两个 main spec(custom-agent/bot-store)
