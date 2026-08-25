# 037 entity-alias 实施任务

## 1. 迁移与资产层(测试先行)

- [x] 1.1 0024 迁移(rag.table_descriptions ADD alias VARCHAR(64) NULL)
  +conftest 零变更(存量表)核对
- [x] 1.2 失败测试:润色产出 alias(goods→「商品表」;畸形剔除不判
  失败);覆盖优先级(MANUAL_EDIT alias 不被润色改写/空 alias 允许
  AI 补齐)
- [x] 1.3 agent describer prompt 加 alias 产出(例外清单登记+单测
  断言)+DescribedTable 模型纯增+api 润色组装/落库 alias

## 2. 召回与注入(测试先行)

- [x] 2.1 失败测试:build_embedding_text 拼 alias;「商品表」问句
  命中 goods;schema_hint 含「goods(商品表)」形态;alias NULL
  零回归(形态与引入前一致)
- [x] 2.2 api rag retrieve/schema_hint 拼装带 alias+embedding/tsv
  文本拼 alias

## 3. 摘要称谓对齐(测试先行:prompt 资产纪律)

- [x] 3.1 失败测试:test_prompts 断言 SUMMARIZE_SYSTEM 称谓规则
  标识(例外清单+全角脚本对拍);nodes.summarize 上下文含 alias 段
- [x] 3.2 SUMMARIZE_SYSTEM 称谓对齐规则(用户用语>别名>表说明
  中文名;禁裸物理名/全限定名/编造)+nodes.py summarize 上下文
  注入 alias 段

## 4. 治理面(测试先行)

- [x] 4.1 失败测试:编辑端点 alias 落库 MANUAL_EDIT;响应回传 alias
- [x] 4.2 /admin/rag 编辑对话框 alias 字段+make contracts 重生成

## 5. 验证与收尾

- [x] 5.1 make check 全绿;016 evals smoke 复验零劣化
- [x] 5.2 真冒烟(清同题 query_history+km:ragctx 缓存后):「商品表
  有多少种商品」→摘要称「商品表」非 goods;「订单表」手编别名后
  摘要沿用;用户称谓与别名冲突时用户优先;reindex 后 alias 落库
- [x] 5.3 R1 独立评审+修复清零
- [x] 5.4 HANDOFF+挂账(列级别名/存量回填批处理/alias 提炼质量
  观测)+归档

## 实施注记(收尾回填)

- 前一 agent 半成品收口:0024 迁移 `public.rag.*` 三段式跨库引用
  (测试库建库即炸,772 全错)+upsert 漏 bind 参数 `alias`+find_table
  漏选 alias 列,三处先行修复。
- 治理面按 knowledge delta 立「与 table_comment 同面编辑」:
  EditTableIn{table_comment,alias}(前 agent 的 alias-only 端点偏离
  spec,已重写);编辑后 embedding 以新说明+别名重算,失败保留旧向量
  (曾误清空)。
- R1 独立评审(1 严重+2 必修+8 建议)全修:S1 alias 命中保留分支时
  tsv/embedding 未同源重算(召回静默失效)→DO UPDATE 内 tsv 按生效
  别名重算+service._ingest 按生效别名算向量+同源直证测试;F1 alias
  零字符校验可注入伪表名→EditTableIn 校验 422+describer 结构降级+
  models.clean_alias 纵深;F2 端到端别名召回测试补齐;B1 内省形态
  不注入 summarize;B2 上传不清既有非空 alias;B3/B4/B5 delta spec
  措辞对齐;B6 describer 头注例外清单化。B7(describe payload 透传
  既有 alias)/B8(tsv simple 中文分词局限)挂账 HANDOFF。
