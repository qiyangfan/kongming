# 059-user-preference-memory 设计

## Context

- `user_memory`(011/020/022/045):content/category/confidence/source/
  embedding/team_id,EXPLICIT+IMPLICIT 双路径,向量召回 top-3 注入
  memory_hint。
- `chat/memory_tasks.py`:问答成功后隐式抽取落库(category/confidence
  0.5)。
- 风险:隐式自由文本可含业务结论;注入后模型可能当作数据范围/事实约束。
- 045 已将 user_memory 收编保留表,数据面拒读;正当通道=召回注入+/memory
  API。

## Goals / Non-Goals

**Goals:** 偏好默认值跨会话复用;抽取目标封闭;注入可审计可关。

**Non-Goals:** 见 proposal。

## Decisions

1. **新表 `user_prefs` 单行 (user_id, tenant_id)**  
   `prefs` JSONB(领域层闭合 schema 校验,拒绝未知键)、`updated_at`。
   *替代方案*:继续堆 user_memory 行——拒绝:槽位语义是「一份配置」,
   多行向量召回与「覆盖更新」冲突,且自由文本与配置混模正是现状病灶。

2. **IMPLICIT 只写 patch,目标槽位封闭**  
   抽取 LLM 输出 schema 仅含槽位字段;未知字段丢弃;全空不写库。
   抽取失败静默放弃(不影响问答)。**禁止**写「用户上月看了 X」类事实。

3. **EXPLICIT 两路**  
   - 槽位意图(默认数据源/时间窗/称呼风格…)→ user_prefs;  
   - 其余「记住…」→ `user_memory` source=EXPLICIT 的 NOTE(用户自述),
     category=NOTE,confidence=1.0,可继续向量召回但注入段标注
     「用户备注,非数据事实」。隐式路径不再产生 NOTE。

4. **注入契约**  
   `preference_hint` 段:已填槽位的键值表 + 固定前缀「以下为用户偏好
   默认值,可忽略;不得作为 WHERE/数据范围约束」。NOTE 召回段独立,
   同样禁止当作业务事实。零槽位零 NOTE = 不注入(零回归)。

5. **不约束 SQL 的执行层含义**  
   preferred_datasource 影响的是**数据源选择默认**(与现有
   datasource-switcher 对齐的会话默认),不是行级过滤;default_time_range
   仅在用户未说时间时作为理解层默认,**可在回答中说明所用范围**。
   两者都不得静默改写已生成 SQL 的业务条件而不告知。

6. **存量 IMPLICIT**  
   停止新增;列表标 LEGACY;默认不注入;用户可删或「转化为槽位」
   (v1 只做删除与停止注入,转化是手工编辑 prefs)。

7. **保留表收编**  
   `user_prefs` 加入精确名 RESERVED 清单(与 user_memory 同理:
   归属列 user_id,datascope 降级不构成防线)。

8. **契约(ADR-005)与 Schema(ADR-004)**  
   GET/PUT /memory/prefs;GET/DELETE 既有 /memory 列表加 kind/legacy
   标记。迁移一次建全。

Schema 核对:user_prefs 新建;user_memory 不改列(legacy 用 source/
category 现有字段语义扩展值域需同步 CHECK,见 tasks)。

## Risks / Trade-offs

- [用户想要「记住我只看华东」作为过滤] → 明确产品边界:那是数据权限/
  筛选,回答中告知「已按你的偏好默认,当前未过滤区域」比静默过滤安全;
  真需求走 datascope/筛选器提案。
- [槽位过少不够用] → 闭合 schema 演进走正常变更加槽位;宁少勿开
  自由文本。
- [抽取 LLM 漏槽] → 低风险(漏=无默认);不重试不阻塞。
- [NOTE 召回仍可能被当真] → 注入前缀强约束 + 057 数值审计兜底
  (备注中的数字同样受白名单)。

## Migration Plan

1. 迁移 user_prefs + 保留表收编(防火墙测试先行)。  
2. 切隐式抽取为槽位 patch;停 IMPLICIT 落 user_memory。  
3. 存量 IMPLICIT 默认不注入(配置开关一次性)。  
4. 回滚:恢复旧抽取任务代码路径;表保留。

## Open Questions

(无——外部记忆中台、M0–M4、动作记忆已出界。)
