# Design

## Context

- 配额策略表已有 scope(VARCHAR + CHECK,TENANT/BOT)与 scope_ref、
  表达式唯一约束(零 uuid 归一,TENANT 行 NULL 不重复);网关判定双路
  (租户级 + api-key 绑定 Bot 命中的 BOT 级)。
- 用量表 `sys_llm_usage` 含 bot_id/user 归属;Bot/工作流资源表含
  team_id 归属(team-workspace 域)。
- chat 面已有「用户问答速率/配额」(062,调用次数、日/月、chat 切面),
  与本变更的网关切面(User 维度)正交。

## Goals / Non-Goals

**Goals:**

- scope 扩展为纯枚举面扩展:策略表结构、判定框架、唯一约束全部复用,
  新增两级的差异收敛在「scope_ref 校验目标」与「用量聚合口径」两点。

**Non-Goals:**

- chat 面 062 用户问答配额的重构(两切面各司其职;统一是更大的立项)。
- 用量表加 team_id 归属列(联表解析足够,见 Decisions)。
- TEAM 级套餐兜底/分账(团队账单是支付域深水区,不在本变更)。
- 配额预警通知推送(既有 X-Quota-Warning 响应头语义已覆盖)。

## Decisions

1. **TEAM 聚合联表解析而非 usage 加列**:加列需写入期解析 + 存量回填 +
   归属变更回溯问题;联表(用量行 → bot.team_id)当月窗口聚合在租户内
   量级下成本可忽略,且「资源换队跟随现归属」语义自然成立(当月治理
   窗口语义,join 即定义)。备选(加列快照归属)在归属变更时产生
   「历史用量归属哪个队」的裁决负担,否决。
2. **判定多路化而非链式短路**:任一超限即拒,拒绝原因取「先命中的超限路」
   (确定性顺序:TENANT → BOT → USER → TEAM),保证同请求拒绝归因稳定
   可测。
3. **USER 级判定切面仅网关**:chat 面已有 062 双闸(速率 fail-open +
   配额 fail-closed);USER 级 billing 配额补的是网关(api-key)切面的
   用户维度,两边语义与口径不同源是有意的(切面不同),spec 注明避免
   后续误合。
4. **CHECK 扩展走新迁移**:scope CHECK 加 'USER','TEAM' 两个枚举值
   (ALTER DROP + ADD CONSTRAINT),存量行不受影响;不做数据迁移。
5. **配置面选择器数据源**:USER/TEAM 选择器复用既有「计费配置租户清单」
   端点的同口径原则——单授 billing:manage 可取当前租户用户/团队清单
   (新查询端点挂 billing:manage,不依赖 user:manage / team:manage),
   与既有 R「计费配置租户清单」同一治理理由。

## Risks / Trade-offs

- [联表聚合在团队/资源量大时的查询成本] → 当月窗口 + 租户内过滤 +
  索引点查;个人项目量级无虞,真实放量时加列快照是清晰的升级路径
  (spec 语义不变)。
- [多路判定的测试矩阵膨胀] → 判定顺序确定性(Decision 2)把组合空间
  压成单序;测试按「逐路独立 + 两两叠加」采样,不全 combinatorial。

## Migration Plan

1. 迁移(CHECK 扩展);2. 策略 CRUD + 判定多路化(测试先行);3. 配置面
   表单。回退:revert(CHECK 收回前需确认无 USER/TEAM 行,迁移 downgrade
   带守卫)。

## Open Questions

(无)
