# Tasks: 046 SQL 投影 id 收敛

## 1. prompt 资产(测试先行)

- [x] 1.1 test_prompts.py 先写失败断言:规则 3 收紧句(技术主键默认不进
  投影/优先业务编号列)、规则 6 DISTINCT 例外句、新增反例句;既有关键句
  断言(「ID/编号列不能替代名称列」「排序键可能并列时必须追加主键等唯一列
  作第二排序键」)核对保持存活
- [x] 1.2 prompts.py 规则 3 收紧:技术主键等代理键默认不进投影(点名不拦)
  +业务编号列优先作展示标识;保留「ID/编号列不能替代名称列」句尾
- [x] 1.3 prompts.py 规则 6 追加 DISTINCT 例外句(PG 硬约束:ORDER BY 列
  必须在投影内;主键入投影去重失效——排序键取已投影列)
- [x] 1.4 反例区:新增「DISTINCT 去重查询追加投影外主键排序」一条;校准
  既有「ID 可附加」措辞与收紧口径一致
- [x] 1.5 prompts.py 文件头例外清单登记 046 增量(日期+变更名),全角标点
  与上下文形态一致
- [x] 1.6 1.1 断言全绿;ReAct 内核/UNDERSTAND/SUMMARIZE 资产零改动核对

## 2. 排查与登记

- [x] 2.1 grep evals/ gold:是否存在预期结果含代理主键列的题;有则登记
  清单入 design 备案(不改判 Execution Match,单独立项)——结论:72 题
  零需改,唯一 SELECT store_id 题系用户点名编号(合法),已备案 design D5
- [x] 2.2 全仓 grep「ID 可附加」类旧措辞残留(提示词/测试/文档),与收紧
  口径不一致处收口——仅余新断言中的负向守卫(刻意保留)

## 3. 验证收口

- [x] 3.1 agent pytest 全绿(重点 test_prompts.py)——192 passed(+2 新断言)
- [x] 3.2 冒烟实测:default 源明细题(如「列出所有影片的标题」)投影不含
  film_id;「租金最高的 5 部影片」tiebreaker 形态不回归;sakila_dyn 源
  不误伤——实测三例:default「列出所有商品的名称」→SELECT name(无 id);
  sakila_dyn「租金最高的5部影片」→SELECT title, rental_rate ORDER BY
  rental_rate DESC, film_id LIMIT 5(投影无 film_id+tiebreaker 原样);
  default「价格最高的3个商品」→投影合格,但该题排序超出玩具表范围
  (goods 无 price 列,模型 ORDER BY id 兜底,既有边界与 046 无关,登记)
- [x] 3.3 make check 全绿,工作区清零待归档

## 4. 冒烟收编:列级哨兵补全(046 冒烟「价格最高」案例裁决)

- [x] 4.1 test_prompts.py 先写失败断言:规则 10 列级子句(关键信息项不
  存在且无法合理推导/不得近似顶替/两级哨兵触发条件/不硬凑近似答案)
- [x] 4.2 prompts.py 规则 10 补列级子句,哨兵触发改两级表述;例外清单
  046 行同步措辞——初版抽象条款实测无效(模型仍按 name DESC 冒充价格
  排序),迭代为点名自我合理化具体表述+对位反例后 3/3 稳定哨兵
- [x] 4.3 nodes.py:`_NO_DATA_ERROR` 泛化为「未找到回答该问题所需的
  数据…」;does-not-exist 喂回引导泛化至「表或列」;对应测试断言同步
  (test_nodes/test_graph_chain)
- [x] 4.4 api/web 兜底文案镜像同步(chat/service.py、assistant-card.tsx)
  +api 测试断言对齐(service.py 折行修 ruff E501)
- [x] 4.5 agent+api pytest 绿,make check 全绿(api 861+agent 193,
  make-exit 亲验;曾因 apps/agent 目录误跑 make 假绿一次、E501 真红一次,
  均已修正重跑)
- [x] 4.6 冒烟复测:「价格最高的3个商品」3/3 稳定 NO_DATA+泛化文案零
  执行;「列出所有商品的名称」与 sakila_dyn top5 不回归(无哨兵误伤)
