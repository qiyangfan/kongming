# 013 挂账清理小修包 — 设计

## Context

7 个挂账项横跨 api/agent/web 三进程,各自独立、互不耦合。动机见 proposal.md。
现状关键点(决定改造形态的事实):

- **E(chat.service 构造反转)**:service 仅 8 处 `self._s` 且全部用于
  commit/rollback,不直接执行 SQL;3 处 `except SQLAlchemyError` 均为
  006 R1 的「agent 回调期间会话被并发删除」落库容错段。既有 import-linter
  契约「chat 领域模型/端口/导出纯函数不依赖框架」只含
  models/ports/export,service 是 chat 域唯一漏网者(system/datasource/rag
  三域契约均含 .service)。`title_tasks.py` 经 kongming.db 间接 import
  sqlalchemy,属后台任务组合根(与 router 适配层同性质),不入契约。
- **G(httpx 复用)**:AgentRagClient 在 chat/router.py:52 与
  rag/router.py:48 两处均为**请求级构造**(Depends 工厂内 new),实例随
  请求销毁——只加实例级 client 复用无意义,必须先改共享单例。
- **D(重命名 strip 后校验)**:spec 已立约「去首尾空白后非空且不超 200」,
  但传输层 `Field(min_length=1, max_length=200)` 先于 service 的 strip
  执行,带空白 201 字 strip 后 ≤200 被 422 误拒;service 层 strip 后
  只查非空、不查长度(列上限靠 DB 约束兜底)。

## Goals / Non-Goals

**Goals:**
- 7 项挂账全清零,每项改动最小化、不波及无关代码路径。
- E 项后 `kongming.chat.service` 进 import-linter 零框架契约,与三域对齐。
- G 项后 fetch-metadata 500 表润色共享一条 keep-alive 连接链。

**Non-Goals:**
- 不重构 title_tasks 的会话管理(010 已立三段式短会话,形态正确)。
- 不动 agent 侧 apiclient `_post` 的每请求新建形态(调用频率低,
  009 挂账⑭只点名 api 侧 AgentRagClient;范围纪律)。
- 不动 ChatRepository 任何 SQL 语义与读路径异常形态(读路径
  SQLAlchemyError 冒出转 500 为现状,保持不变)。
- 其余 39 项挂账维持挂账(评估结论见 HANDOFF 归档时更新)。

## Decisions

### D1. chat.service 反转 = ChatRepo Protocol + ChatStoreError 转译

ports.py 新增 `ChatRepo`(Protocol):镜像 ChatRepository 现有 15 个公开
方法签名 + `commit()` / `rollback()`。ChatRepository 天然满足 Protocol
(结构化子类型,无需显式继承),只需补 commit/rollback 两个薄方法。

service 3 处 `except SQLAlchemyError` 是契约障碍——反转后 service 不可
import sqlalchemy。**转译方案**:ports 定义 `ChatStoreError(Exception)`;
ChatRepository 的写路径方法(create_session/touch_session/rename_session/
update_title_if_current/insert_message/delete_session)与 commit 经模块级
装饰器把 SQLAlchemyError 转译为 ChatStoreError;service 改
`except ChatStoreError`。读路径方法不转译(service 不容错,SQLAlchemyError
冒出维持现状 500 语义)。

- 备选 A:容错段整体下沉 repository 组合方法(persist_quietly)——否决:
  改变方法粒度与测试锚点,006 R1 的容错语义注释锚在 service 层,动它
  等于重写评审史。
- 备选 B:service 改 `except Exception`——否决:违背 006 窄化纪律,
  会把编程错误( AttributeError 等)一并吞掉。
- 装饰器而非逐方法 try:7 个方法 ×3 行重复 try 噪音大,模块级一个
  `_store_error_boundary` 装饰器更整洁,且对未来新增写方法有提示作用。

service 构造签名 `session: AsyncSession` → `repo: ChatRepo`;router.py:104
组合根改为 `ChatService(ChatRepository(session), ...)`。测试面零直接
ChatService 构造点(全经 TestClient 依赖注入),E 项预期测试零改动。
import-linter 契约「chat 领域模型/端口/导出纯函数不依赖框架」
source_modules 追加 `kongming.chat.service`,契约名随口径改为
「chat 领域模型/用例/端口/导出纯函数不依赖框架」。

### D2. AgentRagClient 复用 = 模块级共享单例 + lazy AsyncClient

agent_callback.py 新增模块级工厂 `get_agent_rag_client(redis)`:
进程内缓存一个 AgentRagClient 实例;实例内 AsyncClient lazy 创建
(首次请求时建,进程生命周期内复用,不 close)。chat/router.py:52 与
rag/router.py:48 两处 Depends 工厂改调该工厂。

- httpx.AsyncClient 设计为并发任务共享(内部连接池),单例安全。
- lazy 创建而非 `__init__` 创建:避免模块 import 期/无事件循环环境
  (测试直接构造)触碰网络对象。
- 并发首建竞态无害:最多多建一个实例被 GC,无状态正确性问题。
- settings 在首次构造时读取后冻结:进程内 settings 不可变,与
  get_settings() lru_cache 语义一致。
- 备选:依赖注入容器/lifespan 注册——否决:当前组合根是 Depends 工厂
  风格,模块级单例是最小 diff;lifespan 注册留给未来统一组合根时再做。
- 测试面注记(R1-S3):TestClient 测试经组合根触达工厂(chat/router
  get_chat_service→get_rag_service),但 embed/describe 类级补丁或
  测试环境连接失败兜底使 _client 恒空池,跨 loop 复用无害;单例无业务
  状态。单 loop 前提(连接池绑定创建时 loop)已在 _get_client docstring
  钉住,成功建连的新测试须复位单例槽。

### D3. rag_retrieve 非 JSON 收敛 = 对称 describe 既有形态

apiclient.rag_retrieve 的 `resp.json()` 包 try ValueError →
`log.warning` + `return None`(回退实时内省)。与同文件 describe 的
「响应非 JSON,降级纯结构」形态逐字对称,docstring 承诺
「任何失败返回 None」自此兑现。

### D4. 重命名 strip 后校验 = 传输层放宽 + service 补长度闸

router.RenameIn.title 去掉 `max_length=200`(保留 min_length=1 快速拒空);
service.rename_session 在 strip 后补 `len(cleaned) > 200 → InvalidTitle`
校验——非法判定以 strip 后值为准,与 spec 立约逐字对齐。

- 传输层不放任任意长度:加 `max_length=2000` 防离谱请求体(与
  QuestionIn.question 同宽),200 的精确闸只在 service 层。
- InvalidTitle 既有映射(400)复用,无新错误类型。

### D5. temporal 轴标签 = 全列 ISO 日期形态探测 + 数据层截断

chart-view 新增纯函数 `formatTemporalLabels(labels)`:全列匹配
`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}` 形态才启用(任一不匹配即原样,
宁不错格式化);启用时 labels 数据层映射为日期部分(YYYY-MM-DD),
时间部分非全零时附加 HH:mm。**数据层映射而非 axisLabel.formatter**
(实施期裁定,优于原设想):labels 是轴 data/tooltip/退化点图的唯一
数据源,映射一次处处同源;formatter 只遮轴显示,tooltip 仍露 ISO
原文,两源表述反而分裂。**pie 切片名同源截断(有意为之,R1-S2)**:
全日期列的 pie name/图例/tooltip 随之呈日期部分,与轴系形态一致;
数值散点 x 轴为 value 型不经 labels,不受影响。

- 探测在 web 侧而非 api 下发标志:chart 契约 schema 为自由 string
  (011 已钉),加字段=契约变更,远超本小修收益;字符串形态探测
  确定性足够(全列一致才启用,混入即放弃)。
- 不引日期库:截断即可,toLocaleDateString 的本地化分隔符反而偏离
  数据仓库习惯的 ISO 日期形态。

### D6. scatter tooltip = item formatter 呈现列名+数值

数值散点分支 tooltip 改
`formatter: (p) => \`${chart.x}: ${p.value[0]}<br/>${chart.y}: ${p.value[1]}\``
(y 为 null 的空值点 echarts 不渲染,无 tooltip 触发)。退化类目点图
trigger:"axis" 与 bar/line 同构,不动。

### D7. 删除确认 busy 禁用 = AlertDialogCancel 加 disabled={busy}

session-sidebar 删除对话框既有 busy 状态直接接到 Cancel 按钮;Radix
AlertDialogCancel disabled 后不可点、不触发关闭。零新状态、零新交互。

## Risks / Trade-offs

- [ChatRepo Protocol 与 ChatRepository 未来漂移] → Protocol 结构化检查
  由 mypy 在组合根(router.py:104 实参)处兜底;契约进 lint-imports 后
  service 再 import sqlalchemy 即门禁红。
- [G 单例在测试间泄漏状态] → 单例无业务状态(仅配置+连接池),泄漏无害;
  TestClient 经工厂触达但 _client 恒空池(类级补丁/连接失败兜底,R1-S3
  核过);成功建连的新测试须复位单例槽(docstring 已钉)。
- [temporal 探测误判全列为日期的非日期列(如纯编号字符串)] → 形态
  要求 ISO 完整 `YYYY-MM-DDTHH:mm`,编号不可能匹配;任一不匹配即整列
  放弃,不出现半格式化列。
- [RenameIn 传输层放宽后超长标题直达 service] → service 层 200 闸 +
  DB 列上限双兜底;422→400 的状态码差异(传输层拒 vs 领域拒)对前端
  无感(重命名对话框统一错误文案)。

## Migration Plan

纯代码变更,零 schema/契约/配置迁移。按 tasks.md 顺序实施,api/agent/web
三侧独立可回滚(各自无交叉依赖);`make check` 全绿 + 冒烟记录后归档。

## Open Questions

(无——7 项全部为既有评审已裁定的观察项,形态在实施期即定。)
