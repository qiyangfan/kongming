# Design: add-retrieval-quality-eval

## Context

- 评测体系现态:文件态 YAML 评测集 + 进程外 pytest harness(`apps/api/evals/`,
  smoke/full/multiturn 显式分层)+ judge 两表落库;纯函数指标(matchers/
  attribution)由默认门禁单测守护;README 基线表 + 5pp 红线。检索评测沿用
  该形态,不新起炉灶。
- nl2sql 侧已有召回试跑先例(`/system/rag/recall-probe`:逐路命中+融合分+
  零副作用+权限复用+同源纯函数);文档面试跑端点与之对称。
- 文档检索现态:子块向量 + 子块全文 → RRF 融合 → 可选精排 → 父块回填;
  `retrieve_docs` 为编排函数,逐路命中形态未对外暴露。
- 后续变更(同日 `add-doc-event-entity-recall`)将新增实体召回路——试跑
  端点的逐路结构须可扩展新路,归因面才不返工。

## Goals / Non-Goals

**Goals:**
- 检索质量可量化:macro Recall@5 与全跳覆盖率进基线表,红线进 nightly。
- 逐路归因:试跑响应按召回路拆分,实体路落地后 before/after 可归因到路级。
- 主检索零回归:逐路命中形态经提取共享,主链路行为逐字不变。

**Non-Goals:**
- 不做评测集自动构造、不做 web 界面、不建评测落库表(见 proposal Non-goals)。

## Decisions

### D1: 评测数据面 = 新增试跑端点,不复用 /internal/rag/retrieve

`/internal/rag/retrieve` 的响应面向 agent 组装(schema_hint/doc_hints 拼装),
无逐路原生分与融合名次,不能支撑归因。新增 `GET /system/document/retrieval-probe`
(权限复用 `system:document:manage`),复用 recall-probe 的先例语义
(同源纯函数/零副作用/权限不新增)。备选(直连库取证)否决:harness 与
真实链路同源是 eval 域既定纪律。

### D2: gold = 文档名 + 必含文本片段,运行时映射父块

块标识(父块 id/序号)随语料重切而漂移,硬编码必然脆。gold 写「片段必须
出现在被召回的父块中」,映射由分块纯函数在运行时确定性计算——语料重切后
gold 自动跟随,与「同名覆盖式更新」语义兼容。边界:同一片段跨父块时取
包含片段的首个父块;片段选取规范(唯一性、长度)进评测集自检。

### D3: 指标 = 父块级 Recall@k + 全跳覆盖率,k 缺省 5

对齐 SAG 论文 Recall@5 口径(检索层比较才有对话基础);全跳覆盖率衡量
「证据链完整性」——多跳问答的失败形态多为漏跳,单看 Recall 会掩盖。k 可配
为后续与不同 top-k 对比留口。指标在父块级计算(回填单位即引用单位)。

### D4: 种子语料 = 评测租户既有数据源 + 内嵌 md 语料

语料挂 sakila 评测租户既有数据源 key(tenant+datasource 双维隔离契约不变,
零新增数据源登记);语料为业务口径类文档(政策/规则/定义),刻意构造跨文档
关联事实(如「A 文档定义等级,B 文档定义等级对应权益」)。语料与评测集放
`evals/retrieval/`,随仓库版本化,评测前置幂等上传。

### D5: 分层与红线沿用既有口径

pytest 独立标记 `retrieval` 显式选择;eval-nightly 纳入;红线 = macro
Recall@5 劣化 ≥5pp 判失败(与 eval 域黄金指标红线同款式);逐题溯源口径
沿用。试跑端点零副作用保证评测不验证缓存(与 011/047 教训一致)。

## Risks / Trade-offs

- [小样本方差:语料题量小时指标抖动] → 评测集自检下限(总题数 ≥12、
  multi-hop ≥5),不足即拒跑;基线对照时标注题量。
- [试跑端点契约漂移] → 新端点走 make contracts 产物再生,ADR-005 门禁兜底。
- [语料与真实业务文档分布差异] → 种子语料仅为回归基线面,不宣称代表真实
  分布;后续语料扩充走评测集变更。

## Migration Plan

零 DB 迁移;上线顺序无依赖(端点与 harness 同变更交付);回滚 = 移除端点与
evals/retrieval 目录,主检索无行为变更无需回滚。

## Open Questions

无。
