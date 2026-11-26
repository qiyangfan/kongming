# Tasks: add-slot-rendered-answers

## 1. 契约核心(测试先行)

- [x] 1.1 先写失败测试:`apps/agent/kongming_agent/slots.py` 契约用例覆盖合法槽位、
      未知键、cell 越界、畸形花括号、字面数字(半角/全角/`PG-13` 形态)、问句前提、
      含数字别名、NULL 单元格、datetime 日期渲染、引用键标记不误伤
- [x] 1.2 实现槽位注册与渲染(命名空间 rows/stats/argmax/argmin/cell/q/calc/alias/nulls);
      槽位清单按结果形态生成,argmax/argmin/nulls 只进内部清单,不改对外 stats
- [x] 1.3 实现契约校验顺序(剥引用键 → 剥槽位 → 数字禁令/花括号残留 → 键与界校验),
      违约返回结构化原因,供 trace 与兜底分流
- [x] 1.4 compute_stats 旁路派生 argmax/argmin(复用 `_is_stat_worthy`,label 取首个
      文本列),单测覆盖无文本列、标识列排除、并列取首行
- [x] 1.5 `uv run pytest apps/agent/tests -k slot` 红转绿

## 2. 三条路径接线

- [x] 2.1 summarize:SUMMARIZE_SYSTEM 改槽位契约 + 上下文附槽位清单;T1 渲染接线;
      1×1 数值短路 T0 确定性作答(测试断言 summarize 不调用 LLM)
- [x] 2.2 ReAct 收口:复用同一渲染器;删除 audit 调用与统计旁白兜底
- [x] 2.3 fused_answer:FUSED_ANSWER_SYSTEM 补正文数值纪律;数据数字槽位化;文档数字
      逐字片段校验;违约走 T3 纯数据答退化(原因 doc_number_unquoted);引用净化不变
- [x] 2.4 兜底分层 T2(确定性轮廓 + 未生成解读)与 `answer_contract` trace 事件接入
      stage_trace/事件流
- [x] 2.5 回归样例(固定数据、不调 LLM):前提阈值(100)、含数字标签(PG-13)、
      月度序列三形态断言产出完整答案句

## 3. 退役与清理

- [x] 3.1 删除 `numeric.audit_text` / `filter_insights` / `audit_summarize_output`
      与 `fallback_summary` 及三处调用;保留 extract_numbers/compute_*/派生统计
- [x] 3.2 清理受影响测试(`test_numeric_grounding.py`、`test_nodes.py` 等):删审计
      用例,补契约/兜底用例;全仓 grep 旧白名单口径残留
- [x] 3.3 复核 api 侧无 summary 形状依赖变化(字段与前端零改动)

## 4. 提示词、评测与文档

- [x] 4.1 三处 prompt 重写与 `test_prompts` 断言更新;记录新 prompt 指纹
- [x] 4.2 重跑 sakila/olist 评测,记录新基线(兜底 tier 分布受评测通道不落摘要限制,以单测与冒烟样本覆盖;旧基线标注不可比)
- [x] 4.3 HANDOFF 更新:新契约口径、兜底分层、验收样本与边界(中文数字/报告链路)

## 5. 门禁与收尾

- [x] 5.1 `make check` 全量绿(agent 子集优先,契约产物零 diff);2026-11-27 契约产物重导后全量门禁通过
- [x] 5.2 手工冒烟:标量、分组、月度序列三形态 + 一条融合问句,记录 summary 与 trace tier
- [x] 5.3 `openspec validate 2026-11-26-add-slot-rendered-answers --strict` 通过后归档
