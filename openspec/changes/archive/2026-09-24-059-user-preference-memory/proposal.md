# 059-user-preference-memory 提案:用户偏好记忆(闭合槽位,收窄隐式提取)

## Why

022 agent-memory 现状是**自由文本长期记忆 + 隐式 LLM 抽取偏好/事实/目标**。
对闲聊助手成立,对 2C 问数是负资产:

1. **可复用密度低**:跨会话真正有用的只有稳定偏好(默认数据源、时间窗、
   口径别名、展示习惯);对话里闪过的事实被当成「重要记忆」存下来就是
   噪声与过期源。
2. **脏记忆比无记忆更伤**:LLM 误把「某月销售额下降 20%」抽成用户画像,
   下次注入生成层 = 给 SQL 加假约束,直接污染结果。
3. **注入语义危险**:自由文本以「记忆」身份进提示,模型易当业务事实
   而非默认值。

结论(架构裁定,与 ChatGPT 式全局记忆相反):**要跨会话偏好默认值,不要
自动提取重要事实。** 记忆是可编辑的默认值面板,不是人生日记。

## What Changes

- **闭合偏好槽位**(user × tenant 单行配置,领域校验封闭 schema):
  - `preferred_datasource`
  - `default_time_range`(如 近7天/近30天/自然月)
  - `metric_aliases[]`(用户用语 → 指标/口径提示,只作提示文字)
  - `chart_pref`(表/图、默认维度粒度)
  - `language` / `timezone`
  - `display_style`(结论先行/全表等)
- **隐式提取收窄**:会话后抽取任务 SHALL 只允许产出上述槽位的补丁
  (patch),SHALL NOT 写入自由文本事实/事件/业务结论;空补丁=不落库。
- **显式写入**:
  - 「记住默认用 X 数据源/时间范围…」→ 写对应槽位;
  - 用户主动「记住:我喜欢结论先行」→ 写 display_style;
  - 自由文本便签(可选保留):仅 source=EXPLICIT 的用户自述笔记,
    **不**自动抽取,注入时标记为「用户备注」且可关。
- **注入语义**:以「用户偏好默认值(可忽略,不构成数据范围约束)」
  段注入;MUST NOT 作为业务过滤条件约束 SQL(例如「只看华东」不得改写
  WHERE)。前端可见可关(正在使用哪些偏好)。
- 旧 IMPLICIT 自由文本记忆:停止新增;存量进只读/可删,不注入(或仅
  显式确认后转入槽位/备注)。
- 自管理页改为**槽位编辑为主**;契约经 `make contracts` 再生成(ADR-005)。

## Non-goals

- **不做** ChatGPT 式「自动提取重要记忆」自由文本库(本变更明确收窄
  对象)。
- **不做** M0–M4 分层 / 服务端写入口 reducer / 写操作动作记忆(无写工具,
  另立提案)。
- **不做**跨用户/跨租户共享记忆(团队记忆已有且维持显式写入)。
- **不做**记忆自动演化置信度(v1 槽位无 confidence 演化;沿用写入即
  确定)。
- **不做**「用户只关心华东」类数据范围自动收窄(那是 datascope 的事,
  不是记忆)。
- **不引入**外部 Mem0/LangMem 服务进程:槽位抽取一次 LLM 调用即可,
  编排属于现有 chat 后台任务通道;独立记忆中台属过度设计。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `agent-memory`:长期记忆收窄为**闭合偏好槽位**为主——隐式提取只补
  槽位、注入为可关默认值、不约束 SQL 业务范围;显式笔记与槽位分列。

## Impact

- **Schema(ADR-004)**:新表 `user_prefs`(user_id+tenant_id 唯一,
  prefs JSONB 或拆列,一次建全);`user_memory` 保持但 IMPLICIT 写路径
  收窄(或加 kind 区分 NOTE/LEGACY,见 design)。
- **契约(ADR-005)**:偏好读写端点入 OpenAPI;MemoryPage 交互字段生成。
- `apps/api/kongming/memory`:service 抽取目标改为槽位 patch;注入通道
  改为默认值段(与 memory_hint 区分文案)。
- `apps/agent`:understand/generate 拼接「偏好默认值」段,带「可忽略、
  不约束数据范围」前缀。
- 记忆表保留表收编已由 045 完成;新表 `user_prefs` MUST 同步进 SQL
  防火墙保留表清单(测试先行)。
- 信任边界(ADR-002)无涉;认证(003/006)无涉。
