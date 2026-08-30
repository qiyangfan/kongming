# 049-chart-candidates 实施任务

## 1. 领域层:角色画像与候选纯函数(apps/api/kongming/chat/models.py)

- [x] 1.1 新增 `ChartCandidate`(type/x/y 元组/series,frozen)与 `_profile_columns`
      列角色画像(temporal/期间列/标识列名信号/整数稠密序列阈值 min∈{0,1}+连续+distinct≤16+max≤64/measure,判定顺序按 design D1)
- [x] 1.2 实现 `suggest_charts(columns, rows, sql="") -> list[ChartCandidate]`,
      形状→候选映射按 design D3 表(时序分组多系列 line/单维 bar+pie 闸/两维分组柱/双度量 scatter/零候选),y 多度量截前 3
- [x] 1.3 pie 资格闸:sqlglot 投影可加性(COUNT/SUM)+标签唯一+值全非负,解析失败 fail-closed 排除(design D2)
- [x] 1.4 删除 `suggest_chart`,保留 `ChartSuggestion` 并提供首候选展平构造;
      `derive_artifacts` 签名按 chart 候选形态适配(三路径同源语义不变)

## 2. 服务层与契约(apps/api)

- [x] 2.1 `service.py`:`_finalize` 改调 `suggest_charts`,`ChatOutcome` 增
      `chart_candidates`;result_json 落库纯增 `chart_candidates` 键
- [x] 2.2 `service.py` `_to_message`/`MessageResult`:读档优先候选择,存量档缺失重算
      `suggest_charts`(重算含 sql);`ChartSuggestion` 默认单图展平路径保持
- [x] 2.3 `router.py`:新增 `ChartCandidateOut(type/x/y: list[str]/series)`,
      `ChatQueryOut` 与 `MessageResultOut` 纯增 `chart_candidates=[]`;`_out`/`_msg_out` 装配;`chart` 字段语义=首候选展平不变
- [x] 2.4 `make contracts` 重生成 openapi.json 与前端类型(纯增零删改,漂移门禁过)

## 3. 前端(apps/web)

- [x] 3.1 `chart-shared.ts`:绑定类型收敛 `{type,x,y: string[],series: string|null}`,
      存量单图 normalize(y→[y],series=null);候选列表与类型去重工具
- [x] 3.2 `chart-view-impl.tsx`:切换器渲染候选(≤1 隐藏);bar/line 多系列渲染
      (series 维分组柱/多 y 系列+bottom 图例);pie 恒单 y;回落依赖改候选指纹
- [x] 3.3 `chart-view.tsx`/`assistant-card.tsx`:装配 `chart_candidates`(生成类型,
      `?? []` 收窄),ChartArtifactCard 与缺档回落路径同源传候选
- [x] 3.4 `tsc -b` + `eslint` 全绿;图表色板/下载/调参(tune)零回归
- [x] 3.5 (实测收尾修正)分组系列按类目对号取数(缺组合补 null,防柱体挤
      半轴与值归属错类目);类目轴标签分级全显(≤12 横排/≤24 旋转 35°/
      >24 抽稀);整数量测 y 轴 minInterval 1;多度量系列名与调参占位接
      041 列标签(表达式列维持 041 物理名回退)

## 4. 测试与门禁

- [x] 4.1 `test_chat_query.py` 推断钉集改写扩充:标识列不进度量(门店×分类→分组柱,
      无 scatter)、期间列(year)、整数稠密序列、单维 bar/pie 闸(可加/不可加/负值)、
      多度量系列、双度量 scatter、零候选形状、重算同源(落库 vs 重算一致)
- [x] 4.2 `test_artifacts.py`/契约相关测试对齐;`make check` 全绿(api ruff+mypy+pytest、agent、web tsc+eslint、contracts 漂移)
- [x] 4.3 dev 环境实测:门店×分类问数验证分组柱状图与碎刻度根除;单维聚合验证
      bar 默认+pie 次候选;多度量验证图例;刷新回落首候选;存量旧消息回显不回归
