# 050-id-exposure-governance 实施任务

## 1. agent:统计资格过滤

- [x] 1.1 `graph/nodes.py` compute_stats 增加列角色判定:标识名信号
      `(?:^|_)(?:id|no|code)$|^id_`、期间词 {year,quarter,month,week,day}、
      整数稠密序列(min∈{0,1}、连续、1<distinct≤16、max≤64)的列不产出
      max/min/avg;注释互引 049 画像规则
- [x] 1.2 tests/test_nodes.py 钉集改写扩充:标识列/期间列/稠密序列排除、
      正常度量列保留、bool 排除保持、fallback_summary 不受影响

## 2. api:统计键名用户化

- [x] 2.1 `chat/models.py` 新增 humanize_stats(stats, columns, labels) 纯函数:
      rows→「行数」;{列}_{max|min|avg} 唯一命中→「{标签} 最高/最低/均值」;
      歧义/未命中 fail-open 保留原键
- [x] 2.2 `chat/service.py` `_finalize` 落库与 ChatOutcome 前应用;
      `_to_message` 读档后再应用(幂等,存量英文键升级)
- [x] 2.3 测试:humanize 单测(映射/歧义/幂等/无标签回退物理名)+ happy path
      集成断言对齐

## 3. feedback/audit:身份回退链

- [x] 3.1 feedback/audit repository 补取 u.username,router 契约纯增
      user_username
- [x] 3.2 web `lib/display-user.ts` 单源(displayUser(nickname, username)),
      FeedbackPage/AuditPage 4 处替换(nickname ?? username ?? 已注销;
      audit 保留 id 空→「系统」);make contracts 重生成

## 4. 门禁与实测

- [x] 4.1 make check 全绿(api/agent/web/contracts 漂移)
- [x] 4.2 dev 重启 agent+api,浏览器实测:问数结果徽标无 id 统计键且中文
      形态;存量旧消息回显徽标升级;反馈/审计页身份列无 UUID 片段
