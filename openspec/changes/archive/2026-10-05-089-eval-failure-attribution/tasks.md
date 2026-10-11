# 089-eval-failure-attribution 实施任务

## 1. 测试先行(先红,纯函数,不依赖评估环境)

- [x] 1.1 失败测试:九类各至少一例,且优先级序固定、先匹配先归类
      (构造边界题:表齐但 JOIN 少 → JOIN 而非 METRIC;聚合差且过滤差 → METRIC
      先于 FILTER)
- [x] 1.2 失败测试:SELECT 层拆分——expected 未覆盖=召回层;expected 已覆盖但
      gold 表集未覆盖=生成层
- [x] 1.3 失败测试:负例/拒绝守护失败 → `NEG_GUARD` 一类,不再按正例判据细分
      (expect_no_data 生成 SQL / expect_reject 未拒)
- [x] 1.4 失败测试:`ABSTAIN`(正例题返回 NO_DATA)与 `REJECT`(verdict=BLOCKED)
      不互相混类;`EXEC` 覆盖 success=false 与无 SQL 两种
- [x] 1.5 失败测试:`OTHER` 桶——无法归因时显式归类,透出题目 id 与占比提示
      (≥20% 阈值)
- [x] 1.6 失败测试:分类为纯函数,零外部调用(以注入桩断言不触网/不调 LLM/
      不建连)

## 2. 实现

- [x] 2.1 `evals/attribution.py`:纯函数 `classify_failure(...) ->
      (category, stage, reason, signals)`;判据用词边界正则与既有抽表能力,
      不引新依赖
- [x] 2.2 `evals/test_eval.py`:full 集统计块在四组分数后增列一级分类计数 +
      SELECT 层拆分;每条失败明细带分类名与判据原文
- [x] 2.3 `evals/test_eval.py` multiturn:失败轮次调用同一纯函数归因
- [x] 2.4 `apps/api/tests/test_eval_attribution.py`:纯函数单测落默认门禁
      (与 test_eval_matchers.py 同口径,`from evals.attribution import ...`)
- [x] 2.5 `evals/README.md` 增「失败归因口径」节:九类定义、优先级序、
      判据原文、SELECT 层拆分、OTHER 纪律、用途边界(证据面而非红线)
      **验证**:1.1–1.6 转绿;`make api-check` 绿(1123 passed,覆盖率 73.78%)

## 3. 与 088 联动(证据面)

- [x] 3.1 088 落地后跑全量评估一轮,记录归因分布(扩展开启前/后 SELECT 与
      JOIN 计数变化)写入 `evals/README.md`——这是「要不要继续投入关系配置」
      的决策依据,替代行业文章的数字
      (实测:开/关两轮宽松均 50/50,零失败题 → SELECT/JOIN 归因计数两侧皆空
      ——宽松全过即无归因样本;088 收益由严格 +4pp/表命中 +1 承载,已入
      README 基线行与 088/089 注记。后续读数依赖宽松口径出现失败题)

## 4. 收尾

- [x] 4.1 `make check` 全量绿(归因不进运行时代码路径,api/agent/web 门禁
      只受测试文件影响)
      (五段独立跑全绿:api-check 1123 passed/覆盖率 73.78%、agent 459、
       liannu 22、web、contracts-check EXIT 0;归因模块仅 evals/ 与
       tests/ 两处,零运行时消费方)
- [x] 4.2 冒烟:本地起评估环境跑 `pytest evals -m full`,人工核对统计块的
      分类计数与明细判据原文可读
      (实测两轮:源码评估栈 api 18000 + agent 18001、muniu LLM 出口;
       轮 1 关/轮 2 开,统计块正常输出。OTHER 纪律的完整输出形态在评估账号
       缺租户绑定的事故轮得到实证——OTHER=44/44 + 全部题目 id + ≥20% 阈值
       提示「归因规则不足,需人工扩充」;补绑定后重跑归零。评估栈进程与
       一次性账号 eval089 已清理)
