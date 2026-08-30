# 051-entity-match-sentinel 实施任务

## 1. prompt:表级判据操作化(apps/agent/kongming_agent/graph/prompts.py)

- [x] 1.1 规则 10 增补实体核对子句:核心业务实体必须能在表说明或列语义中
      找到对应方为「支撑本问题的表」;点名两种自我合理化形态(量词泛化
      「库存数量→对任意表 COUNT」、维度错配「任意文本列→问题里的分类」)
- [x] 1.2 反例清单追加对位反例:门店/影片分类问句在仅有商品表时
      SELECT create_by AS category, COUNT(*) 硬凑(原案例原型,应输出哨兵)

## 2. eval:负例回归(apps/api/evals)

- [x] 2.1 `test_eval.py` `_eval_single` 新增 expect_no_data 分支:断言
      success=False+verdict=NO_DATA+SQL 空;pass 口径与拒绝用例一致
- [x] 2.2 新建 `default.yaml`:default_no_data_001(原案例,smoke)+
      default_no_data_002(泛化变体);README 补文件表与用例形态说明
- [x] 2.3 清理 rag.query_history 同题硬凑沉淀与 km:ragctx 缓存(README
      重复性口径)

## 3. 验证与门禁

- [x] 3.1 重启 agent 加载 prompt;负例 2 题 ×3 轮稳定性(每轮前清 ragctx);
      smoke 集全绿(含既有正例不误伤)
- [x] 3.2 浏览器复验:default 数据源原问句呈现 NO_DATA 中性说明卡
- [x] 3.3 make check 全绿;HANDOFF 当前状态更新
