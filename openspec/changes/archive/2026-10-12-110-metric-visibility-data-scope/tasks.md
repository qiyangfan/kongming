# 110-metric-visibility-data-scope 实施任务

## 1. 测试先行(先红)

- [x] 1.1 失败测试:迁移形态——0053 纯增两列(visible_scope CHECK IN ('ALL','DEPT') NOT NULL DEFAULT 'ALL'、dept_id UUID FK sys_dept);downgrade 删列还原
- [x] 1.2 失败测试:`list_metrics` 可见性过滤参数——None=全量(管理面);部门集合=ALL 档 ∪ 命中部门 DEPT 档;空集合=仅 ALL 档;跨租户/跨数据源零命中不变
- [x] 1.3 失败测试:`CallerDeptScopeResolver` 适配器——角色 ALL→universal=True;DEPT→本部门;DEPT_AND_BELOW→递归子部门并集;CUSTOM→勾选并集;SELF→空集合;用户不存在/无启用角色→空集合;CUSTOM 空集合→空集合且不抛
- [x] 1.4 失败测试:`enrich(caller_user_id=None)`→仅 ALL 档;`/internal/rag/retrieve` 的 req.user_id 透传 enrich(102 管道延续);probe 面不受影响(仍全量)(透传以注入点代码路径+3.2 实机冒烟验证)
- [x] 1.5 失败测试:管理端校验——DEPT 档缺 dept_id 拒(文案);ALL 档 dept_id 置空;MetricOut 回显两字段
- [x] 1.6 失败测试:076 同源回归——可见性过滤后文字段仍由结构化命中派生,两侧数量内容一致;无命中零注入逐字不变

## 2. 实现(api)

- [x] 2.1 迁移 `0053_metric_visibility.py`(D6:纯增两列+CHECK+FK RESTRICT,零回填);dev 库已升 0053
- [x] 2.2 `semantic/models.py` CallerScope;`semantic/ports.py` 窄端口;`semantic/repository.py` list_metrics 过滤参数(D3 SQL 单点);`semantic/service.py` enrich 加 caller_user_id 并按 CallerScope 分派(None/SELF→仅 ALL 档形态;解析异常读面降级)
- [x] 2.3 `semantic/router.py` 适配器(组合 SqlDataScopeRepo+most_permissive/dept 集合解析,semantic 零 datascope import)+ MetricIn/Out 字段与校验(D5,含归属部门同租户校验 DEPT_INVALID);`rag/router.py` 注入点透传 caller_user_id
- [x] 2.4 `make contracts` 再生(MetricIn/Out 纯增字段),`contracts/openapi.json` 与前端类型入库
- [x] 2.5 web `SemanticPage` 指标表单可见范围 Select+部门 Select(DEPT 档)、列表「可见范围」列;启停 upsert 带原值防 DEPT 档被重置;typecheck+lint 绿

## 3. 收尾

- [x] 3.1 `make check` 全量绿(coverage fail_under 不降)——api-check 1507 passed(+5 新测试,coverage 73.84%;唯一失败=本机代理截流已登记假败,HANDOFF 载明),web typecheck+lint 绿,契约再生幂等
- [x] 3.2 实机冒烟(评估栈):DEPT 档指标「VIP复购率」归属数据部→ALL 用户 metric_hits 两枚全见、SELF 用户/CUSTOM 空集合用户/无身份均仅见 ALL 档「公共毛利」;DEPT 缺部门 422 文案拒、跨租户 dept_id 400 DEPT_INVALID;recall-probe 面不受影响
- [x] 3.3 文档现态:HANDOFF 指标域条目;docs/roadmap.md 远期条目改为已落地口径;`openspec archive` 归档并同步主规格
