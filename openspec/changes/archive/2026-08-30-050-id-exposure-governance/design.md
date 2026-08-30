# 050-id-exposure-governance 设计

## Context

排查证据与通道定性见 proposal。049 已在图表通道建立列角色画像
(api `chat/models.py::_profile_columns`);agent 是独立进程独立 venv,
物理上不可跨包 import(api 包级依赖会拖入 FastAPI 全家,ADR-002 信任
边界亦要求 agent 最小依赖),故统计资格过滤在 agent 侧落地一份纯值面
小规则,规则与 049 对齐并注释互引;键名可读性的单源在 api(列标签
041 单源在 api,agent 无库注释访问权,信任边界内不可得)。

## Goals / Non-Goals

Goals:标识/期间列不进数值统计(agent);统计键名用户可读(api,契约
零改动);管理面身份列昵称缺失可读回退(feedback/audit)。
Non-goals 见 proposal。

## Decisions

### D1 统计资格过滤(agent,compute_stats 单点)

与 049 D1 同规则、纯值面+名称信号,逐列独立判定(无"仍剩度量"条件——
统计逐列产出,不存在图表候选的归零问题):

1. 列名小写命中标识信号 `(?:^|_)(?:id|no|code)$|^id_` → 跳过;
2. 列名小写 ∈ {year, quarter, month, week, day} → 跳过;
3. 数值样本呈整数稠密序列(min∈{0,1}、连续、1<distinct≤16、max≤64)→ 跳过;
4. 其余数值列 → 产出 max/min/avg。

`num` 后缀不入信号、bool 排除等既有语义保持。summarize 喂 LLM 与
回传走同一 stats——LLM 输入同步净化(不再生成「门店 ID 均值 1.5」类洞察)。

### D2 统计键名用户化(api,humanize_stats 纯函数)

- 映射:`rows`→「行数」;键 `k` 存在列 `c∈columns` 与后缀
  `s∈{max,min,avg}` 使 `k==f"{c}_{s}"`(列名在结果集内唯一,分解恒无歧义)
  → 「{label(c)} 最高/最低/均值」,label 取 041 column_labels(缺省回退
  物理列名);键前缀未命中任何列(异源键/畸形档)→ 保留原键 fail-open。
- 接线:`_finalize` 在落库 result_json 与 ChatOutcome 返回前应用
  (落库即事实源);`_to_message` 读档后再应用一遍——存量档英文键
  (columns 仍在档内)自然升级,已中文化键不匹配模式幂等保留。
- 契约零改动:`stats: dict[str, object]` 键本为自由文案,前端徽标
  `{k} {v}` 渲染零改动。

### D3 身份回退链(feedback+audit,契约纯增 user_username)

- repository 两处 JOIN 已连 sys_user,补取 `u.username AS user_username`;
  router 契约纯增字段(缺省 None,零回归)。
- 前端统一 `displayUser(nickname, username)`:昵称 ?? 用户名 ?? 「已注销」;
  audit 保留第三态语义:user_id 为空=系统操作→「系统」(与现有行为一致),
  user_id 存在但两名称皆缺→「已注销」。title 悬停展示可读名。
- 4 处调用点(FeedbackPage 2、AuditPage 2)收敛单源 `lib/display-user.ts`。

## Risks / Trade-offs

- [agent 与 api 两份画像规则漂移] → 规则简短(信号正则+稠密阈值)且注释
  互引 049/050;跨进程单源需共享包(架构级变更,非本问题体量值得)。
- [humanize 键歧义漏映射] → fail-open 保留原键,徽标退回现状不劣化。
- [存量档回显二次 humanize 误改] → 幂等性由测试钉死(中文键/非 stats 形态
  键原样保留)。

## Migration Plan

无 schema 迁移;agent 进程重启加载;存量消息读档自然升级键名。回滚=还原
代码,数据无损。

## Open Questions

无。
