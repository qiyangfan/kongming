# 010 会话体验深化 — 实施任务

> 安全面(EXPLAIN 管线/搜索转义/导出权限/重命名归属)测试先行:先写失败测试再实现。
> 每完成一组跑对应 make check 子集;动 api 路由/模型后跑 make contracts。

## 1. 共享基础与依赖

- [x] 1.1 `kongming/like.py` 新建纯函数 `like_pattern(keyword)`(自 system/repository.py `_like_pattern` 上移);system/repository 改引用并删本地副本;跑 system 域测试确认零回归(行为等价,既有 ILIKE 用例锁定)
- [x] 1.2 apps/api 新增依赖 openpyxl(uv add,uv.lock 入库);docs/tech-stack.md 依赖清单登记一行
- [x] 1.3 api config 新增 `export_max_rows`(env `KM_EXPORT_MAX_ROWS`,默认 10000,下界 1)+ .env.example 注记;config 测试补下界断言

## 2. EXPLAIN 执行计划(安全面,测试先行)

- [x] 2.1 失败测试先行——internal/service `explain()` 用例:改写后 SQL 被拼 `EXPLAIN (FORMAT JSON)` 前缀执行、data_scope 改写生效(SELF 角色计划含过滤)、非 SELECT 轮(构造 REJECTED 消息 SQL)复检 BLOCKED、消息无 sql_text 友好错误、动态源快照路由(datasource_key 来自会话落库值)、审计落 EXPLAIN 形态 sql_text;跑红
- [x] 2.2 internal/service.py 实现 `explain()`:与 `execute()` 共享私有段(租户/数据源/绑定/初检/改写/复检/只读池连接),复检通过后拼前缀执行,返回解析后计划 JSON(解析失败降级 `{"raw": text}`);测试转绿
- [x] 2.3 失败测试先行——`POST /chat/explain` 端点用例:归属 404 三态(他人/跨租户/不存在)、无 chat:execute 403、未认证 401、消息无 SQL 400、成功 200 含 Plan 树;跑红
- [x] 2.4 chat 域实现:ExplainPort(Protocol)+ chat/service explain 编排(消息加载+会话数据源快照+DatasourceChecker 复校)+ router 端点 + 组合根绑定 internal.service.explain;测试转绿
- [x] 2.5 EXPLAIN 端到端用例:真实 dev 库对 SUCCESS 消息取计划,断言返回 `[{Plan:{Node Type:...}}]` 形态;`make check` api 子集绿

## 3. 结果导出 CSV/Excel(测试先行)

- [x] 3.1 失败测试先行——chat/export.py 纯函数用例:CSV(BOM 头/CRLF/RFC 4180 转义含逗号引号换行/None→空串)、xlsx(openpyxl 读回断言表头加粗+列宽+类型分流)、行数截断(上限+1 行入,断言截断+warn);跑红
- [x] 3.2 实现 chat/export.py 零框架纯函数 `to_csv`/`to_xlsx`(自算列宽:东亚宽字符按 2 计,clamp [8,60],表头+前 100 行采样);测试转绿
- [x] 3.3 失败测试先行——`POST /chat/export` 端点用例:权限 403/未认证 401/format 非法 400/CSV 响应头 Content-Disposition 时间戳文件名/xlsx MIME;跑红
- [x] 3.4 chat router/service 实现导出端点(service 调纯函数,Response 字节流+filename*);测试转绿;`make check` api 子集绿

## 4. 会话搜索与重命名(测试先行)

- [x] 4.1 失败测试先行——repository 搜索用例:标题命中/问句内容 EXISTS 命中/通配符 `%` `_` `\` 字面化/他人会话问句命中不返回/纯空白 q 等价不传;跑红
- [x] 4.2 chat/repository 实现 q 过滤(title ILIKE ESCAPE + EXISTS user 问句)+ service 透传 + router `GET /chat/sessions?q=`(q 可选 ≤200 字符);测试转绿
- [x] 4.3 失败测试先行——重命名用例:成功更新标题+updated_at/空白标题参数错误/超长拒绝/越权 404/无权限 403/重命名后标题不被改写覆盖(update_title_if_current 条件更新断言行数);跑红
- [x] 4.4 chat/repository `update_title_if_current(expected 乐观锁)` + service + `PATCH /chat/sessions/{id}` router;测试转绿;`make check` api 子集绿

## 5. 标题 LLM 改写(agent + api 链路)

- [x] 5.1 agent 失败测试先行——`POST /internal/rewrite-title`:正常返回净化标题/LLM 异常降级错误形态/空输出降级/内部令牌缺失 403(与既有内部端点同构);跑红
- [x] 5.2 agent 实现:title prompt(全新撰写,文件头注明非上游移植)+ llm 调用(低温/小 max_tokens/短超时)+ main.py 内部端点;测试转绿
- [x] 5.3 api 失败测试先行——agent_callback `rewrite_title`(httpx 错误收敛)+ chat service 首个 SUCCESS 轮挂 BackgroundTasks(条件:标题==截断初始值)+ repository 条件回写;断言:成功改写/agent 失败保持截断/已重命名跳过改写(行数 0);跑红
- [x] 5.4 api 实现标题改写编排(净化:strip/去换行/去包裹引号/cap 50);测试转绿;`make check` api+agent 子集绿

## 6. 契约与前端类型

- [x] 6.1 `make contracts` 重生成 openapi.json + web 类型;确认四端点(export/explain/PATCH rename/sessions?q)入契约;web tsc 绿

## 7. web 前端

- [x] 7.1 lib/chat-api.ts 封装(rename/export blob/explain)+ api.ts `postBlob`(Content-Disposition 解析 + application/json 错误形态检测,上游实证坑)
- [x] 7.2 session-sidebar:搜索框(受控+300ms 防抖,react-query 键含 q)+ 时间分组纯函数 `groupSessionsByTime`(今天/昨天/近7天/近30天/更早,组内倒序)+ 分组渲染
- [x] 7.3 会话条目 DropdownMenu(重命名 Dialog 受控输入 Enter 提交/删除迁移入内),与既有删除确认同构
- [x] 7.4 assistant-card:导出按钮组(columns+rows 存在时显,blob 下载+错误行内 role=alert)+ 「执行计划」折叠卡懒加载(展开才请求,失败态重试钮,递归 formatPlan 缩进 `<pre>`,无 Plan 降级 JSON.stringify)
- [x] 7.5 裸元素/shadcn 合规自查(新交互全走 shadcn:Input/Dialog/DropdownMenu/Button;token 化禁硬编码色值);`pnpm tsc -b && pnpm eslint` 绿

## 8. 质量门禁与冒烟

- [x] 8.1 `make check` 全量绿(api/agent ruff+mypy+lint-imports+pytest,web tsc+eslint)
- [x] 8.2 curl 冒烟链:登录→发问(新会话截断标题→首个 SUCCESS 后 LLM 改写回写,查库验证)→重命名→再发问验证不被覆盖→搜索(标题词/问句词/通配符字面)→导出 CSV(文件头 BOM 校验)+xlsx(openpyxl 读回)→EXPLAIN(SUCCESS 消息计划树+REJECTED 消息 BLOCKED+他人消息 404)
- [x] 8.3 Playwright MCP UI 冒烟:侧栏分组/搜索框过滤/重命名对话框/导出下载/执行计划展开渲染;截图存档
- [x] 8.4 审查循环:独立评审(api 安全面切口+web 状态机切口)发现问题清零;HANDOFF 更新(端点清单/冒烟记录/挂账)
