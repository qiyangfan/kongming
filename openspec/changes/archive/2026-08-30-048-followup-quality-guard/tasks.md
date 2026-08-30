# Tasks: 048 聚合追问与去重护栏

- [x] 1. prompts.py:UNDERSTAND_MULTI_SYSTEM 聚合口径显式化句;规则 5
       单行汇总句;规则 9 对应关系去重具体化;反例一增;文件头例外清单
       登记(prompt 资产纪律)
- [x] 2. test_prompts.py 四组钉句(含例外清单登记断言)
- [x] 3. evals/olist_multi_turn.yaml mt_003 t2 问句消歧(「客户记录数」)
- [x] 4. 重启 agent 载新 prompt;清 ctx 缓存;重跑 full+multiturn,验证
       sakila_018 / olist_mt_003 / olist_mt_006 三靶点;README 基线行
- [x] 5. make check 全绿;收尾(归档待指令)
