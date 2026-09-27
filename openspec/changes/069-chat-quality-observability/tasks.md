# 069-chat-quality-observability 实施任务

## 1. 测试先行:落库契约与聚合口径(租户隔离属安全相关,先红后绿)

- [ ] 1.1 失败测试:多轮问答(改写发生 + selfheal 1 次)终态落库后,
       assistant 消息含 trace_id / retry_count=1 / resolved_question 非空;
       单轮问答 resolved_question 为 null、retry_count 如实;先跑红
- [ ] 1.2 失败测试:质量信号四字段不出现在用户响应与 SSE result 事件
       (逐字段同构契约不被破坏);先跑红
- [ ] 1.3 失败测试:汇总聚合的租户隔离——T1/T2 各有轮次,以 T1 查询
       只见 T1(非超管按请求租户收敛);先跑红
- [ ] 1.4 失败测试:趋势按 Asia/Shanghai 日历日补零(窗口内空天有桶,
       桶数=窗口天数);谓词放 ON 内的既有先例对齐;先跑红
- [ ] 1.5 失败测试:NO_DATA 轮次(无执行审计行)进入汇总与明细分页
       —— 该形态今天在任何观测面不可见,是本变更核心新增可见性;先跑红
- [ ] 1.6 失败测试:汇总端点无 `system:chat-quality:view` 返回 403,
       不泄漏任何聚合数据;先跑红

## 2. 迁移与 agent 面信号源

- [ ] 2.1 迁移 `0031_chat_quality_signals.py`:chat_message 增
       `trace_id VARCHAR(64)` / `retry_count SMALLINT NOT NULL DEFAULT 0` /
       `resolved_question TEXT` / `prompt_version VARCHAR(8)`
       (存量 null/0,零回算)+ `idx_chat_message_created (created_at)`
       **验证**:up/down 往返;既有消息读取零回归
- [ ] 2.2 agent prompt 资产指纹:启动时对 prompt 模板集合算稳定 hash
       (前 8 位),随 QueryOut 每轮携带(query-streaming「逐字段同构」
       不受影响——指纹只进落库不进用户事件,见 design D8)
       **验证**:指纹随 prompt 资产变更而变化、重启稳定;单测断言
- [ ] 2.3 agent QueryOut 纯增 `resolved_question`(仅改写实际发生且有
       history 时非空)与 `prompt_version`;`_initial_state` 侧 clear 语义
       与既有 optional 字段对齐
       **验证**:既有 agent 测试零回归;节点单测覆盖单轮/多轮两形态

## 3. api 面接线

- [ ] 3.1 新能力域包 `apps/api/kongming/quality/`(router→service→
       repository,domain 零框架依赖);`pyproject.toml` import-linter 补
       本域契约(与 llmusage/audit 同款:禁跨域 import、repository 层
       契约)
       **验证**:`make api-check` 的 lint-imports 绿
- [ ] 3.2 `chat/service.py` `_finalize_reply` 单点写四字段(同步/流式
       共用同一落库出口,零分叉);AgentReply 映射补 resololved_question
       /prompt_version/trace_id(已在函数签名)
       **验证**:1.1、1.2 转绿;流式/同步双通道单测都覆盖
- [ ] 3.3 `GET /system/chat-quality/summary`:总览 + 按天趋势(≤90 天,
       补零)+ 数据源切片 + prompt 版本切片;token 均值经
       sys_llm_usage trace_id join
       **验证**:1.3–1.5 转绿;explain 确认走 created_at 索引无全表扫
- [ ] 3.4 `GET /system/chat-quality/page`:状态/数据源/时间范围筛选,
       created_at 倒序;trace_id 列供前端跳转时间线抽屉
       **验证**:1.5、1.6 转绿
- [ ] 3.5 权限种子 `("system:chat-quality:view", "问答质量查看", N)`
       追加(auth/seed.py 既有序号后顺延;SUPER_ADMIN 种子覆盖自动生效)
       **验证**:种子迁移对新库生效;1.6 转绿

## 4. 契约与前端

- [ ] 4.1 `make contracts` 重新生成并提交产物(ADR-005);核对
       contracts/openapi.json 仅新增两个管理端点与 schema,用户面
       schema 零变化
       **验证**:CI contracts 漂移门禁本地预演绿
- [ ] 4.2 `apps/web/src/pages/admin/QualityPage.tsx`(lazy 路由,图表库
       体积治理同款)+ `lib/quality-api.ts` + `ADMIN_NAV` 入口 +
       `main.tsx` PermRoute(权限点双源同构,勿只改一处)
       **验证**:`pnpm typecheck && pnpm lint && pnpm build` 绿
- [ ] 4.3 页面范式对齐 UsagePage/FeedbackPage:PageHeader + StatCard +
       EChart 趋势(走 `lib/echarts.ts` 单源,禁第二份 option 样板)+
       DataView + 明细分页 + trace_id 跳转时间线抽屉;暗色两态走查
       **验证**:宪法 Article 8 自检 grep;亮/暗两态截图走查记录
- [ ] 4.4 明细行动作:复制问题(clipboard)+ 跳转 /admin/rag;窄屏行操作
       收溢出菜单
       **验证**:375 档行动作可达、横向不溢出

## 5. 收口

- [ ] 5.1 spec delta sync:`observability/spec.md`(新增 Requirement +
       Purpose 措辞扩展为「用量 + 问答质量可观测」)、`chat/spec.md`
       (「问答查询端点」修订段落)、`web-admin/spec.md`(管理页守卫 +
       问答质量观测页)
- [ ] 5.2 文档同步:管理面页面清单(AGENTS.md / web 侧文档如有列举)、
       `docs/roadmap.md` 观测段(若有)"已完成"标记——防幽灵引用
- [ ] 5.3 手工冒烟:多轮问答(含一次 selfheal)→ /admin/quality 可见
       trace_id/retry_count/改写问题 → 点 trace_id 跳转时间线 →
       NO_DATA 轮次(问一个知识未覆盖问题)进入明细分页;记录于 apply
- [ ] 5.4 全量 `make check` 收口 + `pnpm build` 冒烟

> 069 落地记录:
