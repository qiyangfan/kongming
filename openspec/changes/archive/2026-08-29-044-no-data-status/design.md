# Design: 044 NO_DATA 一等中性形态

## 决策

### D1 判别点:agent `end_reason=no_data` → QueryOut.verdict="NO_DATA"

`_build_query_out` 的 verdict 判定链追加一档(优先级在 execution/validation
之后):`final.get("end_reason") == "no_data"` → `"NO_DATA"`,`error` 仍携带
原始说明文案(与 BLOCKED 携带 friendly 文案同形),`success=False`。
HTTP 契约纯增枚举值(字段结构零变化),ADR-005 同源生成。

### D2 消息状态第四态:CHECK 演进走迁移

baseline 内联 `CHECK (status IN ('SUCCESS','FAILED','REJECTED'))`,PG 自动
命名 `chat_message_status_check`;迁移 0028 drop+re-add 加入 `'NO_DATA'`
(列宽 VARCHAR(16) 恰好容纳,零类型变更;downgrade 还原三值)。
存量行零触碰。

### D3 chat 映射:summary 承载中性文案,error 置空

`_finalize` 四态:NO_DATA → `status="NO_DATA"`、`summary=说明文案`
(经输出护栏,无害)、`error=None`(不进错误横幅)、产物卡为空
(derive_artifacts 对非空 summary 会派生 document 卡——NO_DATA 必须跳过
派生,如实说明不产卡片)、chart=none、无列标签、无回写(非 success
本就不触发,零新增分支)。流式/同步共用 _finalize 唯一实现,自动同构。

### D4 web:镜像映射 + 中性卡片

- ChatPage 实时映射(verdict→status 三元表达式)扩 NO_DATA 档;
- assistant-card 增 NO_DATA 分支:中性图标(信息语义,非红叉)+说明文案
  (msg.summary),不渲染 SQL/结果表/反馈区(与 REJECTED 同策略:
  非「错答」语义不挂反馈?——**反裁决:NO_DATA 挂反馈区**。「答案没帮
  上忙/数据源不对」恰是高价值反馈信号,UP/DOWN 照常);
- 历史回显:status 直出读档,分支同卡片。

### D5 消费点排查结论(全过)

- 回写/护栏/chart/列标签:均挂 reply.success,NO_DATA 非成功,零分支;
- 用量统计:按执行用量采集,与消息状态无关;
- 会话列表/历史:status 直出,NO_DATA 新值随类型生成;
- 管理面 FeedbackPage:按 feedback_status 过滤,与消息状态正交。

## 落点

| 文件 | 改动 |
|---|---|
| agent main.py | verdict 判别链 + QueryOut 注释 |
| api 迁移 0028 | CHECK 加 NO_DATA(drop+re-add) |
| api chat/service.py | 四态映射 + NO_DATA 分支(summary/error/产物跳过) |
| web ChatPage.tsx | 映射扩档 |
| web assistant-card.tsx | NO_DATA 中性分支(挂反馈区) |
| contracts 产物 | make contracts |
| 测试 | api 映射/落库用例;web tsc/eslint |
