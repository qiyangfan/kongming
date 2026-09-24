# 056-skill-pack 设计

## Context

- 术语/口径现状:semantic-layer 已有 metric(sql_template + business_terms),
  但无「词 → 业务释义」的 glossary 面;表注释承担了部分中文语义,与垂类
  术语混放,无法整包启停。
- few-shot 现状:query_history 自动沉淀(成功 SQL)+ sample_queries 表级
  示例,均为数据面知识;无「人工黄金问法包」。
- 人设现状:SUMMARIZE_SYSTEM / doc_answer 提示硬编码在
  `graph/prompts.py`,无按垂类切换。
- 信任边界:Agent 零 DB 凭证(ADR-002),Skill Pack 由控制面持有并投影,
  Agent 只消费文本段。

## Goals / Non-Goals

**Goals:**

- 一个垂类 = 一包可装卸内容;上新垂类不改平台代码。
- 零包时生成链路逐字零回归。
- 包内容纯数据,可版本化、可启停、可审计。

**Non-Goals:** 见 proposal。

## Decisions

1. **落点独立域 `skill-pack`(不塞进 rag 或 custom-agent)**  
   包是「生成上下文资产」,不是文档知识,也不是 Bot 配置。塞进 rag 会把
   术语/人设和文档分块搅在一个生命周期里;塞进 custom-agent 会把垂类内容
   绑死到 Bot 实体。独立域满足「一个 change 一个能力域」,未来 Bot 级挂包
   走 binding 表扩展即可。

2. **存储:两张表,内容 JSONB**  
   - `skill_pack`:id(UUID v7)、tenant_id(平台共享包为 NULL 时见决策 3)、
     pack_key、name、version、description、glossary/fewshots/persona/metrics
     四段 JSONB、status(ACTIVE/RETIRED)、created_at/updated_at。  
   - `skill_pack_binding`:pack_id、datasource_key、enabled、updated_at,
     唯一键 (pack_id, datasource_key)。  
   *替代方案*:文件系统 zip——拒绝:多环境同步难、审计难、与 ADR-004
   「行为契约进 baseline」不一致。

3. **租户作用域**  
   包归属 `tenant_id`,**不**做平台级共享包(NULL 租户)于 v1:多租户
   共享内容是内容运营问题,过早抽象。垂类包在每个需要的租户下各建一份
   (或经导出/导入复制,属后续)。

4. **注入投影契约(控制面 → Agent)**  
   加载器按 (tenant, datasource) 取 enabled 包,合并后截断封顶
   (glossary ≤50 条、fewshots ≤10 条、persona ≤800 字),投影为三个
   文本段:`skill_terms` / `skill_examples` / `skill_persona`。经既有
   retrieve/回调响应语义段下发(与 schema_hint/memory_hint 同通道),
   **不**新增 agent 侧解析格式(纯文本段)。零段 = 不注入。

5. **metrics 段与语义层**  
   包内 metrics 在启用时经语义层既有登记路径写入(带来源标记
   source=SKILL_PACK 便于回溯);`metric_name` 冲突时拒绝启用并报错
   (宁缺毋错,不静默覆盖)。禁用/退休包**不**删除已登记指标——指标已被
   生成链路依赖,删除是破坏性动作,归人工治理。

6. **fewshot 不进执行面**  
   黄金 SQL 只作 prompt 示例文本,与 043 表范围纪律一致:模型不得把示例
   表名当成本数据源可用表。示例段固定前缀「以下为问法风格示例,表名列名
   不可照抄」。

7. **管理面权限**  
   新权限点 `system:skill:manage`(与 rag/manage 同构:租户内管理员);
   查询列表可用 `system:skill:view`。权限注册走既有权限种子机制。

8. **契约(ADR-005)**  
   CRUD/启停/列表响应模型入 OpenAPI;字段英文、文案值中文。

Schema 核对:两张表均为新建,无 baseline 列复用问题;迁移一次建全
(ADR-004)。`user_memory` 等保留表清单不涉及(skill_pack 为管理面表,
若默认源可读需同步收编进 SQL 防火墙保留表——**决策:收编 `skill_` 前缀**
进 RESERVED 规则,与 sys_/chat_ 同哲学,见 tasks 安全项)。

## Risks / Trade-offs

- [多包拼接冲突(glossary 同词不同释义)] → v1 简单拼接+封顶,文档写明
  不承诺冲突消解;治理上建议每 (tenant, datasource) 同时启用 ≤1 垂类包。
- [示例 SQL 被模型照抄表名] → 示例段禁令前缀 + 043 表范围纪律双层;
  若实测仍误照抄,再加示例表名剥离(独立演进)。
- [metrics 启用失败导致包启停半状态] → 事务内先校验 metric_name 冲突
  再写,冲突则启用整体失败(无半开)。
- [JSONB 契约漂移] → 领域模型用显式结构(dataclass/pydantic 在 api 适配层
  校验,领域 models 零框架),导入导出保留原始 JSON 以便人审。

## Migration Plan

1. 迁移建两张表 + 保留表收编 skill_ 前缀(防火墙测试先行)。  
2. 部署后默认零包,链路零回归。  
3. 手工录入一个冒烟包(glossary 3 条 + fewshot 2 条 + persona 1 段),
   启停各验一次注入/消失。  
4. 回滚:禁用全部 binding 即回到零包行为;表结构保留。

## Open Questions

(无——平台级共享包、zip 导入、术语 fail-closed 工具均已在 Non-goals
明确出界。)
