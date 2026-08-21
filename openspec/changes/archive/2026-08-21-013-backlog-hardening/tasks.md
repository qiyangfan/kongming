# 013 挂账清理小修包 — 实施任务

## 1. api:chat.service 构造反转(E,design D1)

- [x] 1.1 chat/ports.py 新增 `ChatStoreError(Exception)` 与 `ChatRepo` Protocol(镜像 ChatRepository 现有 15 个公开方法签名 + `commit()`/`rollback()`)
- [x] 1.2 chat/repository.py:模块级 `_store_error_boundary` 装饰器(SQLAlchemyError→ChatStoreError),应用到 6 个写方法与新增 `commit()`/`rollback()` 薄方法
- [x] 1.3 chat/service.py:构造签名 `session: AsyncSession` → `repo: ChatRepo`;`self._s` 全删(8 处 commit/rollback 改走 repo);3 处 `except SQLAlchemyError` → `except ChatStoreError`;移除 sqlalchemy 全部 import
- [x] 1.4 chat/router.py:104 组合根改 `ChatService(ChatRepository(session), ...)`
- [x] 1.5 pyproject.toml:import-linter chat 契约 source_modules 追加 `kongming.chat.service`,契约名口径对齐「领域模型/用例/端口/导出纯函数」
- [x] 1.6 跑 `lint-imports` + mypy + chat 域全部测试,确认零行为变化(既有 389 测试全绿兜底)

## 2. api:重命名 strip 后校验(D,design D4)

- [x] 2.1 测试先行:test_chat 重命名用例——带空白 201 字 strip 后 ≤200 应成功(当前 422 红);strip 后 >200 应 400
- [x] 2.2 chat/router.py:RenameIn.title 去 `max_length=200`,保留 `min_length=1`,加 `max_length=2000` 防离谱请求体
- [x] 2.3 chat/service.py:rename_session strip 后补 `len(cleaned) > 200 → InvalidTitle`
- [x] 2.4 测试转绿 + 既有重命名用例零回归

## 3. api:AgentRagClient httpx 复用(G,design D2)

- [x] 3.1 rag/agent_callback.py:实例内 AsyncClient lazy 创建复用(`_get_client()`);`_embed_http`/`describe` 不再 `async with` 新建
- [x] 3.2 同文件新增模块级工厂 `get_agent_rag_client(redis)`(进程内单例);chat/router.py:52 与 rag/router.py:48 两处改调工厂
- [x] 3.3 既有 rag 58 测试零回归(embed/describe 降级语义不变);补一条 client 复用断言(同实例两次调用同一 AsyncClient 对象)

## 4. agent:rag_retrieve 非 JSON 收敛(H,design D3)

- [x] 4.1 测试先行:apiclient rag_retrieve 收到 200 但非 JSON 报文应返回 None(当前裸抛红)
- [x] 4.2 apiclient.py:rag_retrieve 的 `resp.json()` 包 try ValueError → log.warning + return None(与 describe 形态对称)
- [x] 4.3 测试转绿 + agent 85 测试零回归

## 5. web:temporal 轴标签 + scatter tooltip + 删除 busy(A/B/C,design D5/D6/D7)

- [x] 5.1 chart-view.tsx:`formatTemporalLabels` 纯函数(全列 ISO 日期形态探测,任一不匹配即原样);axisCommon.axisLabel.formatter 接入(bar/line/退化 scatter 共享)
- [x] 5.2 chart-view.tsx:数值散点 tooltip formatter 呈现 `列名: 数值` 双行
- [x] 5.3 session-sidebar.tsx:AlertDialogCancel 加 `disabled={busy}`
- [x] 5.4 `tsc -b` + eslint 零告警

## 6. 门禁与冒烟

- [x] 6.1 `make check` 全绿(api/agent ruff+mypy+lint-imports+pytest;web tsc+eslint)
- [x] 6.2 `make contracts` 产物已重生成,零端点变化,diff 仅 RenameIn maxLength 200→2000+description 一处(R1-O4)
- [x] 6.3 真 LLM 冒烟:①sakila「2005年7月每天的租赁次数」line 图轴标签呈 2005-07-05 无 T 后缀;②双数值 scatter 悬停 tooltip 含列名+数值;③会话删除确认中「再想想」禁用;④带空白长标题重命名 strip 后成功;⑤问答全链路零回归(chat.service 反转后首验);⑥rag 召回正常(AgentRagClient 单例后);Playwright MCP 留痕
