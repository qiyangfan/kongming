# 设计:分析报告自动生成

## 范围与证据

- 取数、绘图、呈现三地基已就绪:问数执行链路(防火墙/data_scope/只读池/六层防御)、cloud-code-sandbox(数据分析环境与配额)、conversation-artifacts(类型化卡)。
- 生成编排的落点:agent 面已有多轮 ReAct 内核与用量采集(038),报告生成是「多轮 LLM + 多次既有工具回调」的组合,不新增执行通道。
- 本设计不引入新外部依赖;涉 schema(ADR-004)与契约(ADR-005),信任边界零改动(ADR-002)。

## D1:编排在 agent 面,事实在控制面

报告生成 = 规划(章节树)→ 逐章取数 → 逐章撰写 → 组装。规划与撰写是纯 LLM 工作,放 agent 面(与 ReAct 内核同进程同范式);取数 100% 经既有 `/internal` 工具回调(execute_sql/run_python_code),控制面持全部凭证与防御——报告不申请任何新回调端点。报告域包(持久化/版本/导出/权限)落控制面 `report/`,域内分层 router→service→models→repository,models/service 零框架依赖(import-linter 守护)。

否决「报告生成放控制面」:规划/撰写需要 LLM 编排循环,控制面引入编排等于复制 agent 面职责,违反双进程分工(ADR-002 的精神);否决「报告全放 agent 面」:报告是业务事实与权限客体,必须住在持凭证侧。

## D2:生成会话与报告状态机

报告状态 `status ∈ {GENERATING, READY, PARTIAL, FAILED, DELETED}`(VARCHAR+CHECK,ADR-004/VI)。生成请求落库后返回报告标识,agent 面以「报告生成上下文」驱动多轮;进度状态(规划中/取数中/撰写中)写报告行的 phase 字段,前端轮询。生成上下文与 chat 会话解耦——入口 (a) 把会话产物作为只读素材传入,不回写会话;产出仅以 report 卡挂回发起会话(见 specs delta)。

## D3:版本化存储

三表一次建全(ADR-006 精神):

- `report`(id UUID v7,tenant_id,user_id,title,status,phase,datasource_key,version,created_at/updated_at,deleted_at)——版本指针在主表,当前版本号;
- `report_version`(id,report_id,version,meta,data_time_point,created_by,created_at)——版本不可变,含封面元数据;
- `report_section`(id,report_version_id,parent_id,title,narrative_md,bindings_jsonb,chart_jsonb,ord,data_status ∈ {OK, MISSING, PENDING},fail_reason)——章节树以 parent_id 自关联,叶子持绑定与图表。

快照存 `bindings_jsonb`(SQL 文本 + 截断后的列/行)。「刷新数据」= 以最新版本的绑定重执行 → 新版本 + 新快照;「编辑保存」= 复制最新版本章节树后改 → 新版本。并发冲突:编辑提交携带所基于的版本号,服务端不匹配即拒绝(乐观锁,无需长事务)。

## D4:预算、用量与 trace

报告生成预算三约束(步数/token/时长)在 agent 侧计数,控制面报告行落预算消耗摘要;耗尽即停,终态 PARTIAL(对齐「预算退出诚实」)。LLM 调用沿用既有用量采集,协议归因(077)追加报告维度标签;一次生成共用一条 trace 链(同一 trace 语义,父子 span 覆盖规划/取数/撰写)。

## D5:导出与前端

导出为服务端拼装 markdown(叙述原样 + 快照渲染为 markdown 表格 + 图表降级为数据表),不调 LLM、不渲染图片——v1 刻意不做 PDF/Word(Non-goal)。前端:报告列表页(DataView 双形态)、详情页(章节树 + 图表复用 chart 渲染器 + 版本切换)、编辑页(叙述 markdown 编辑 + 绑定查看);骨架只用标准件,375/768/1280 亮暗两态验收。

## D6:权限点

新增 `report:generate` / `report:read` / `report:export`,走既有 RBAC 权限点体系;默认角色映射在迁移种子中给 ANALYST 及以上 generate/read,export 给 ANALYST 及以上(部署可调)。权限判定是安全规则类——测试先行(先写失败测试再实现,宪法 Article III)。

## 风险与缓解

- 生成周期长(多轮 LLM+多次查询):异步 + 进度查询 + 预算硬顶;前端不阻塞会话。
- 快照体积:行数截断上限(实现定,缺省数百行级)+ JSONB 单行体积护栏,超出只存截断标注。
- 与既有问答链路互扰:报告取数走既有工具回调的独立 trace 段,限流配额按租户+用户维度共用既有口径,不新增旁路。
