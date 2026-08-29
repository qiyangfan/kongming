# Feat: 044 NO_DATA 一等中性形态(如实说明不再伪装成失败)

## Why

043 落地后,知识未覆盖时系统如实回答「当前数据源未找到与问题相关的数据表」,
但该回答复用 FAILED 终态呈现——「查询失败:」前缀 + 错误横幅包装一句中性
说明,自相矛盾。用户反馈即命中 043 Non-goal 登记的升级条件(形态误导),
本变更将其升级为一等形态。

语义审视:「未找到相关数据表」是**成功的问答**(用户得到了准确答案),
既非执行失败(FAILED)也非请求被拒(REJECTED);谎报 SUCCESS 则污染
回写/统计口径(执行根本未发生),排除。正解=消息状态第四态。

## What Changes

- **agent 契约**:QueryOut.verdict 增加 `NO_DATA`(短路终态
  `end_reason=no_data` 的对外判别);四态 PASS/BLOCKED/ERROR/NO_DATA。
- **chat 模型**:chat_message.status CHECK 增加 `'NO_DATA'`(迁移 0028,
  drop+re-add 内联 CHECK);映射后 summary=中性说明文案、error=None、
  产物卡为空(如实说明不派生 document 卡)。
- **chat 映射**:`_finalize` 三态扩展为四态;流式/同步共用唯一实现零分叉。
- **web**:verdict→status 镜像映射(ChatPage)与结果卡(assistant-card)
  增 NO_DATA 中性分支(非错误横幅);类型经 `make contracts` 生成。
- 测试先行:映射/落库/产物为空/web 分支;消费点全排查(回写/护栏/chart
  对非 success 本就不生效,零新增分支)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `nl2sql-agent`:QueryOut.verdict SHALL 含 `NO_DATA`(知识未覆盖短路
  终态的对外判别);
- `chat`:消息状态 SHALL 支持第四态 `NO_DATA`——如实说明型回答以中性
  形态持久化与呈现,SHALL NOT 伪装为执行失败。

## Non-goals

- 043 的哨兵纪律本体(已由并行实施落地,本变更只做其呈现层升级);
- 其他「生成侧如实终态」形态(问题含糊/写操作引导等)复用 NO_DATA 的
  语义扩展——留待形态出现时评估;
- 空结果(执行成功但 0 行)的呈现(属 SUCCESS 语义,不动)。
