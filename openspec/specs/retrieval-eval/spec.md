## Purpose

量化文档检索质量:以 multi-hop 评测集与 Recall@k 指标对 chunk 级检索做直接
评测,为文档检索能力演进提供 before/after 证据面与回归红线。

## Requirements

### Requirement: 检索评测运行形态

检索评测 SHALL 以进程外 harness 形态对真实链路运行:经登录凭证调用文档检索
试跑端点(与主检索共用同一召回/融合纯函数),以评测租户既有数据源承载语料,
SHALL NOT 新增数据源登记。语料上传 SHALL 幂等前置(同内容重传跳过,复用
既有 content_hash 幂等语义)。检索评测 SHALL 为显式分层运行(独立评测标记),
SHALL NOT 进入无评估环境的默认测试收集。

#### Scenario: 评测走真实检索链路

- GIVEN 评测环境(双进程+凭证)就绪且语料上传完成
- WHEN 运行检索评测
- THEN 逐题经文档检索试跑端点取得逐路命中与融合名次,与真实用户检索同源
- AND 评测不落业务数据、不污染缓存语义(试跑零副作用)

#### Scenario: 默认门禁隔离

- GIVEN 无评估环境(无服务/无凭证)的常规测试环境
- WHEN 运行默认测试收集
- THEN 检索评测用例不被收集执行(须显式指定分层运行)

#### Scenario: 语料幂等前置

- GIVEN 语料文档已上传且内容未变
- WHEN 评测前置再次上传同名语料
- THEN 跳过重切,块数不变,评测直接进入逐题执行

### Requirement: 检索评测集结构

评测集 SHALL 为文件态:语料 Markdown 文档集 + 问题集(每题含 id/question/
category/gold)。gold SHALL 以「文档名 + 必含文本片段集」表达,运行时由分块
纯函数确定性映射到 gold 父块集合,SHALL NOT 硬编码块标识。category SHALL
覆盖单跳、同文档多跳、跨文档多跳三类;多跳题的 gold SHALL 映射到不少于两
个父块。种子语料 SHALL 包含跨文档关联事实(证据分布于不同文档),题量与
跳型分布 SHALL 满足约定的下限(总题数与多跳题数下限进评测集自检)。

#### Scenario: 题目结构与跳型覆盖

- GIVEN 检索评测集文件
- THEN 每题含 id/question/category/gold,category 覆盖单跳/同文档多跳/跨文档多跳
- AND 多跳题 gold 映射到 ≥2 个父块,跨文档多跳题的 gold 父块分布于 ≥2 份文档

#### Scenario: 语料重切后 gold 映射稳定

- GIVEN 语料被重新上传触发重切
- WHEN 评测计算 gold 父块映射
- THEN 同一文本片段映射到包含它的父块,映射不依赖块序号或块标识

#### Scenario: 评测集自检下限

- GIVEN 评测集装载
- WHEN 自检执行
- THEN 总题数与多跳题数低于约定下限时评测启动失败(防小样本虚高)

### Requirement: Recall@k 指标与基线红线

指标 SHALL 为纯函数:父块级 Recall@k(k 部署级可配,缺省 5)与全跳覆盖率
(gold 父块全部进入 top-k 的题占比),输出逐题明细与分类聚合。指标纯函数
SHALL 由默认测试门禁单测守护(不依赖评估环境)。基线 SHALL 记录进评测
README 基线表(标注原因);回归红线 SHALL 为宏观 Recall@5 劣化 ≥5pp 判
失败,劣化超预期逐题溯源(与 eval 域基线口径一致)。

#### Scenario: 指标计算

- GIVEN 某题 gold 父块集合与试跑返回的 top-k 父块名次
- WHEN 指标计算
- THEN Recall@k = gold 命中数 / gold 总数,全跳覆盖率 = 全部 gold 命中记 1 否则记 0

#### Scenario: 指标纯函数默认门禁守护

- GIVEN 指标为纯函数
- WHEN 常规测试门禁运行(无评估环境)
- THEN 指标单测执行,判定回归即时暴露

#### Scenario: 回归红线

- GIVEN 新基线与前基线并列
- WHEN 宏观 Recall@5 劣化 ≥5pp
- THEN 判失败并逐题溯源(区分评测集改动影响与检索行为变化)
