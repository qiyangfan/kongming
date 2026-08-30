# 050-id-exposure-governance 提案:内部 id 暴露治理

## Why

全面排查证实「不可读 id 到达用户面」存在两条未经角色治理的通道:① agent 侧
`compute_stats` 对所有数值列无差别产出 `{列名}_max/_min/_avg`,标识列/期间列
照样进(实测徽标出现 `store_id_avg 1.5`),且物理列名+英文后缀键直出用户文案;
② 管理面反馈/审计列表在昵称缺失时回退渲染 `user_id` 前 8 位(4 处),后端
查询未取 username,前端无从回退。与 049 同源的「标识列当度量」病,治本于
通道入口而非前端遮盖。

## What Changes

- **统计资格治理(agent)**:`compute_stats` 增加列角色过滤——名称信号
  (`id/no/code` 后缀、期间词)与整数稠密序列(与 049 画像同规则)的列
  SHALL NOT 进数值统计;两调用点(summarize/react)共用一函数天然全覆盖;
  过滤同时净化 summarize/fallback_summary 的 LLM 输入(不再对门店 ID 求均值)。
- **统计键名用户化(api)**:新增 `humanize_stats` 纯函数——`rows`→「行数」、
  `{列}_{max|min|avg}` 唯一命中结果列时→「{列标签} 最高/最低/均值」(041 列
  标签单源;歧义/未命中 fail-open 保留原键);`_finalize` 落库与返回前应用,
  `_to_message` 读档后再应用(存量英文键幂等升级);契约零改动(键为自由
  文案),前端徽标零改动。
- **身份回退链(feedback+audit)**:两域列表查询补取 `u.username`,契约纯增
  `user_username`;前端 4 处 fallback 统一为 `昵称 ?? 用户名 ?? 已注销`
  (audit 保留 user_id 为空→「系统」语义),抽 `displayUser` 单源。

## Non-goals

- 不改 stats 契约形态(dict[str, object] 键为自由文案,结构化改造无必要)。
- 不动 trace_id/bot_key/plan_key 等操作者语义原语与 error_detail/EXPLAIN
  折叠技术面(排查豁免清单,见会话审计)。
- 不做存量档迁移:旧英文键经读档 humanize 幂等升级,无需回填。
- agent 硬凑表(043 哨兵失守)另案立案,不并入本变更。

## Capabilities

### Modified Capabilities

- `chat`:新增「结果统计呈现」Requirement——统计资格(标识/期间列排除)+
  键名可读性(列标签中文化)+存量档读档归一。
- `nl2sql-agent`:新增「结果统计资格」Requirement——本地确定性统计的列
  角色过滤规则。
- `feedback`:新增「操作者身份可读性」Requirement——昵称缺失回退链。
- `audit`:同上,审计列表操作者列回退链。

## Impact

- apps/agent:`graph/nodes.py`(compute_stats 过滤)、tests/test_nodes.py。
- apps/api:`chat/models.py`(humanize_stats)、`chat/service.py`(finalize/
  to_message 接线)、`feedback/repository.py`+`router.py`、
  `audit/repository.py`+`router.py`(username 纯增)、对应测试。
- apps/web:FeedbackPage/AuditPage 4 处 fallback+`lib` displayUser 单源;
  `make contracts` 重生成。
- 无 schema 迁移;agent 进程需重启加载新代码。
