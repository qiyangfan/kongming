# Proposal: 分析报告自动生成

## Why

问数链路(自然语言 → SQL → 执行 → 图表/洞察)的产出止步于会话内一问一答,业务侧真正要交付的是**可保存、可编辑、可导出的分析结论**——「RAG / 问数 / 多智能体 / 报告」四件套的最后一块。取数(NL2SQL)、计算绘图(cloud-code-sandbox)、会话产物呈现(conversation-artifacts)三个地基均已就绪,补上报告域即可把数据链路闭环到「交付物」,也是通用智能体化的第一个纵向消费者。

## What Changes

- 新增报告域:报告 = 元数据 + 章节树,每章含标题、中文叙述、数据绑定(SQL 引用 + 结果快照)与图表配置;从会话问答上下文一键生成,或直接下指令生成。
- 生成链路:LLM 规划章节 → 逐章经既有问数链路实际取数(只读 + 六层防御)→ 沙箱渲染图表 → 组装落库;**数据真实原则**——叙述中的数据断言必须来自实际执行结果,查询失败的章节如实标注数据缺失。
- 报告持久化:版本化(编辑保存产生新版本,历史版本只读),章节数据为生成时点快照,「刷新数据」重执行绑定查询并产生新版本。
- 导出:markdown(v1,图表降级为数据表)。
- 报告卡:conversation-artifacts 纯增 `report` 类型,报告挂回发起会话。
- 预算与用量:报告级生成预算(步数/token/时长),耗尽以部分完成语义落库;用量与 trace 归集进既有观测(038 语义)。

## Capabilities

### New Capabilities

- `report-generation`: 分析报告的生成入口、结构契约、数据真实原则、版本化与快照、导出、生成预算——报告作为一类一等交付物的全部行为契约。

### Modified Capabilities

- `conversation-artifacts`: artifact 类型集合纯增 `report`(报告卡携带报告标识与版本,点击进入报告详情);既有 sql/chart/document 派生行为零改动。

## Impact

- `apps/api`:新域包 `report/`(router→service→models→repository),报告/章节/版本持久化与导出端点;新权限点(report:generate/report:read/report:export,RBAC 测试先行)。
- 数据库:新 Alembic 迁移建报告三表(ADR-004;UUID v7 主键、状态列 VARCHAR+CHECK、timestamptz)。
- 契约:新增公开端点后必须 `make contracts` 重生成并提交产物(ADR-005)。
- `apps/agent`:报告规划/逐章撰写的多轮 LLM 编排,取数 100% 经既有 `/internal` 工具回调,零凭证纪律不变(ADR-002)。
- `apps/web`:报告列表/详情/编辑页,骨架只用标准件(PageHeader/DataView/EmptyState/ErrorAlert 等),移动优先双形态。

## Non-goals

- PDF/Word 导出——排版引擎选型需单独评估,后续变更。
- 定时/订阅式报告推送——与 triggers-webhooks(032)的桥接是独立变更。
- 报告级反馈沉淀闭环(feedback 域扩展)——后续变更。
- 多智能体协作生成报告(规划/撰写/审查分工)——依赖 091,明确不做。
- 对外分享链接与报告级权限面——团队协作语义归 team-workspace 演进。
- 报告模板市场/自定义模板参数化——后续产品决策。
