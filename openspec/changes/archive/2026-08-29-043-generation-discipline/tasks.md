# Tasks: 043-generation-discipline

## 1. 测试先行(失败测试)

- [x] 1.1 test_prompts:SQL_GEN_SYSTEM 含规则 10 关键句(表范围纪律+哨兵唯一
  例外于规则 7);`_EMPTY_HINT` 新文案含「严禁臆测」;既有断言全部保持
- [x] 1.2 test_nodes:generate 哨兵输出→短路产物(`generated_sql=""`、
  `end_reason="no_data"`、友好文案),零 validate/execute 推进
- [x] 1.3 test_nodes:包裹形态(```sql NO_RELEVANT_TABLE```)识别;哨兵作子串
  出现在解释文本中不误触
- [x] 1.4 test_nodes:喂回增强——error 含 does not exist 时修正上下文附清单
  引导;其余错误形态喂回不变
- [x] 1.5 图级:generate→validate 条件边(哨兵→END;正常→validate);
  既有 blocked/retry_exhausted 路径回归

## 2. 实现

- [x] 2.1 prompts.py:规则 10(表范围纪律+规则 7 唯一例外)+ 文件头例外
  清单登记(2026-08-29,043)
- [x] 2.2 nodes.py:`_EMPTY_HINT` 重写;generate 哨兵检测(clean 前 strip+
  反引号剥离+大小写折叠全等)与短路产物;`if error:` 段 does-not-exist
  引导追加
- [x] 2.3 build.py:`generate→validate` 改条件边 `_after_generate`
- [x] 2.4 main.py:QueryOut.verdict 注释更新(None 语义列 no_data);
  `_build_query_out` 零改动(既有 error 形态自然承接)

## 3. 门禁与契约

- [x] 3.1 agent ruff+mypy+pytest 全绿;api 全量回归(web 零改动)
- [x] 3.2 `make contracts` 复核零漂移(QueryOut 无字段变化)

## 4. 实测验证

- [x] 4.1 default 租户重问事故同题(「统计每个员工处理的租赁总数」):
  秒级友好文案;sys_sql_audit 零新增执行记录(短路前零执行)
- [x] 4.2 sakila_dyn 源同题正常出数(纪律不误伤);goods 类既有问题回归
- [x] 4.3 bot/react 渠道回归(Non-goal 面确认零影响)
