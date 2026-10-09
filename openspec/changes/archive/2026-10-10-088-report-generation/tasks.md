# 实施任务

每项按不超过两小时拆分;本次仅提案,以下任务均未实施。

## 1. 存储与权限(测试先行)

- [x] 1.1 先写失败测试:报告归属隔离(他人 404 同形)、删除后拒操作、权限点 report:generate/read/export 的授予与拒绝面。(tests/test_report.py 14 例全绿)
- [x] 1.2 新建 Alembic 迁移:report / report_version / report_section 三表(UUID v7、VARCHAR+CHECK 状态、timestamptz、软删),验证升级与回滚。(0037,dev 库 up→down→up 回环实证)
- [x] 1.3 report 域包落地:models(状态机迁移规则 fail-closed)→ repository → service(版本化/乐观锁/快照语义),domain 零框架依赖过 import-linter。(23 contracts kept)

## 2. 生成编排(agent 面)

- [x] 2.1 报告生成上下文与多轮编排:规划→逐章取数(经既有工具回调)→撰写→组装;预算三约束计数与 PARTIAL 终态。(agent report_gen.py 编排器 + 7 剧本化测试)
- [x] 2.2 数据真实原则回归:查询失败章节标注缺失不编造、防御拒绝不绕道、修正 SQL 重过全量防御——先写失败测试再实现。(缺数章节走确定性标注不调 LLM;rejected 不修复不绕道;修正稿重走同通道)
- [x] 2.3 用量与 trace:报告维度归集(077 协议归因)、同一 trace 链贯穿;038 采集面回归。(llm._report 沿既有通道携 trace_id/protocol;报告行存 trace_id,用量按 trace 归集;agent 全量 466 绿)
- [x] 2.4 生成进度 phase 写回与终态落库(READY/PARTIAL/FAILED),失败保留已产出章节。(/internal/report/progress 回写;编排中途失败经 _degraded_payload 保章节,api 落 PARTIAL+fail_reason)

## 3. 契约与导出

- [x] 3.1 控制面公开端点:生成发起/进度/详情/列表/编辑保存/刷新数据/删除/导出;跑 `make contracts` 重生成类型并核对无既有契约漂移(ADR-005)。(九端点齐;契约纯增,既有 schema 仅键序重排零语义漂移)
- [x] 3.2 markdown 导出实现与审计留痕;快照截断口径与导出行数一致性测试。(纯函数装配;report.export 审计落 sys_oper_log 有断言;截断口径=同一快照对象,审查修复后刷新路径 limit=cap+1 恢复超限标注)
- [x] 3.3 conversation-artifacts 纯增 report 卡:派生规则、partial/failed 摘要如实、老前端兼容回归。(derive_artifacts 互斥分支单一定义;摘要叶子口径;未知 type 前端忽略)

## 4. 前端与验收

- [x] 4.1 报告列表/详情/版本切换页(DataView 双形态,标准件骨架);报告卡点击进详情。(ReportsPage/ReportDetailPage+report 卡入 artifact-blocks;ResultTable 抽公共件)
- [x] 4.2 会话内一键生成入口(补充要求输入);编辑页叙述 markdown 编辑 + 绑定查看。(ChatPage 工具行入口+Dialog;编辑对话框含乐观锁提示)
- [x] 4.3 `pnpm typecheck && pnpm lint` + 宪法 Article 8 自检 grep;375/768/1280 亮暗两态走查改动页。(tsc/eslint/vitest86/build 绿;playwright 6 档计算样式断言:亮暗背景正确翻转、零横向溢出)
- [x] 4.4 端到端冒烟:会话问答→一键生成→刷新数据产生新版本→编辑保存→导出 markdown→删除;`make check` 对应子集全绿并记录证据。(真栈:api18000+agent18001+dashscope qwen-plus+web5175;全链路含报告卡回显与下载事件断言;环境注:sakila 全新库无中文注释,063 预筛共现零命中属既知形态,冒烟按部署开关 KM_AGENT_SCHEMA_PRUNE=false 关闭)

## 5. 收尾

- [x] 5.1 独立代码审查(数据真实原则与权限面为重点),缺陷修复后复跑门禁。(独立子代理审查:5 P1+13 P2;P1 全修——read 权限点挂端点/刷新 CAS/截断标注 limit+1/row_count 真值/任务强引用;P2 修 9 项;新增 read 拒绝面与竞态 409 回归钉,api 1107 绿+四门禁绿)
- [x] 5.2 归档前 sync delta 进主 specs,核对 conversation-artifacts 主 spec 同步无冲突。(report-generation 新域+conversation-artifacts MODIFIED 已手工同步,strict 零 ERROR;16 项超长文本 WARNING 属冻结豁免口径)
