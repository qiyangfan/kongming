# Tasks

## 1. schema 与持久层(judge 运行记录)

- [x] 1.1 新增 Alembic 迁移建 judge_run/judge_result 两表(UUID v7 主键、
  状态列 VARCHAR+CHECK、timestamptz、(object_type, object_id) 二元组不设
  业务外键),对齐 ADR-004;`uv run alembic upgrade head` 通过,迁移单测
  表结构断言过
- [x] 1.2 域包落地:kongming/evalj/(或既有评估域包内子模块)models +
  repository,domain 零框架依赖;import-linter 门禁过(`make check` api
  侧 contracts/linters 绿)

## 2. rubric 声明与 judge runner

- [x] 2.1 四类对象(report/task/collab/answer)rubric 模块:维度/分档/
  权重/prompt 模板 + VERSION 常量;rubric 纯数据单测(版本递增纪律、
  四类齐备)过
- [x] 2.2 judge LLM 客户端与结构化解析:固定 JSON schema 输出约定、解析
  失败保留原文重试一次再显式失败;解析与重试口径单测(含不可解析样本)
  过
- [x] 2.3 judge run 编排:显式指定评审范围(类型×对象集)、逐对象独立
  收口、run 汇总(总数/PASS/通过率)落库、run 记录 rubric 版本+提示词
  指纹;编排单测(单对象失败不中断 run、BORDERLINE 计分母)过
- [x] 2.4 评审对象取数:报告/任务/协作经 api 查询端点、文本回答经问答
  链路(persist=false 临时对话),复用评估器既有登录态;e2e 冒烟脚本对
  一份真实报告跑通 judge 并落库

## 3. 评测集自动构造

- [x] 3.1 候选生成:按租户+数据源从 schema 知识/文档知识生成候选题,
  category 配额分批;生成 prompt 单测(结构字段齐备)过
- [x] 3.2 自洽验证:gold 直连执行+行数上限+规范校验(复用 eval 域既有
  校验器)+语义去重(embedding 余弦阈值 0.92);验证器单测覆盖 tiebreaker
  缺失/业务列缺失/重复候选三类拦截
- [x] 3.3 候选目录与并入:候选+验证报告落 datasets/candidates/<tenant>/,
  人审后一条命令收口并入正式评估集并登记原因;目录产物冒烟(生成→验证
  →并入)过
- [x] 3.4 并入后基线重建:并入触发全量评估重跑并写基线(原因=自动构造
  扩题);基线表断言过
  (注记:基线行登记已单测;真实重跑在本机单租户环境缺 sakila/olist 演示
  租户被 fail-closed 拦下——并入管线如实拒绝登记,复跑前置=多租户演示环境)

## 4. 定时门禁 judge 层

- [x] 4.1 门禁编排扩 judge 抽检层:按类型分层抽样(≥3/类型,不足全量)、
  通过率落 artifacts 与上次可比、劣化 ≥5pp(同 rubric 版本)即门禁失败;
  抽样与红线判定单测过
- [x] 4.2 空缺与凭证口径:单类型对象空缺如实记录不计失败、judge 凭证
  缺失显式失败不静默跳过;两口径单测过

## 5. 收尾门禁

- [x] 5.1 全量验证:`make check` 全绿(api ruff+mypy+pytest、契约无变化
  则 contracts-check 绿);eval-agent/ai-eval delta 经 `openspec validate
  --strict` 过
- [x] 5.2 真实环境冒烟:评估环境起栈,手动 judge run(报告+文本回答各
  ≥3 对象)落库可查,自动构造一批候选并人审并入、基线重建;结果记入
  HANDOFF
  (注记:基线重建在本机被 fail-closed 拦下——全量层需 sakila/olist 双租户
  演示数据;机制与登记逻辑已单测,见 HANDOFF 092 节)

## Workflow follow-up

- 归档走 /opsx:archive:specs delta 同步主 specs(eval-agent 新增、
  ai-eval「CI smoke 守护」整块替换,注意保留 065/073 合并终态备注)
- roadmap 第四梯队评测行与 ADR-010 载体列更新为已落地
