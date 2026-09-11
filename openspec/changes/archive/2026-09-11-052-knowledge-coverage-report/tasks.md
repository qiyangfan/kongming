# 052-knowledge-coverage-report 实施任务

## 1. 测试先行:领域纯函数(宪法 Article II/III;权限+租户安全面,先红后绿)

- [x] 1.1 `apps/api/kongming/rag/models.py` 覆盖计算纯函数的失败测试:
      零覆盖表差集(内省集−登记集,含跨 schema 同名表不遮蔽)、纯结构登记
      判定(source=INTROSPECTION)、缺标签列差集(内省列名−columns_comment
      键)、失败问题归一化归并(折叠空白/去首尾/忽略大小写,次数+最近
      状态/时间)、封顶截断且计数如实(300→清单≤200+计数 300);先跑红
      (`uv run pytest apps/api/tests -k coverage` 对应子集,记录红)
- [x] 1.2 router/service 权限与租户失败测试:无 system:rag:manage → 403、
      数据源不存在/禁用/他租户 → 同形拒绝、他租户数据不出现在任何节;
      先跑红(同上记录)

## 2. repository:输入聚合(只读,零新表)

- [x] 2.1 内省输入:复用 `rag/repository.py::introspect_all`(不复制扫描
      逻辑),验证:与 fetch-metadata 同扫描集(BASE TABLE、排除系统
      schema/保留表)
- [x] 2.2 `rag.table_descriptions` 登记读取:该 (租户, 数据源) 全量条目
      (source_type/columns_comment/sample_queries),验证:现有查询函数
      够用则不新写,新增只读方法附单测
- [x] 2.3 `public.chat_message` ⋈ `chat_session` 聚合查询:近 30 天、按
      (tenant_id, datasource_key) 过滤、status∈{FAILED,REJECTED,NO_DATA}
      ∪ rating=DOWN ∪ corrected_sql 非空,只取 question/status/rating/
      corrected_sql/created_at;验证:聚合 SQL 单测(时间窗边界+过滤谓词)
- [x] 2.4 `rag.query_history` 概况查询:SUCCESS 条数、去重问题数、上限
      占用率(cap=500);验证:单测含空维度(0 行→零值概况)
- [x] 2.5 跑 `uv run pytest apps/api/tests` rag 子集,2.1–2.4 绿,全程
      断言零写语句(纯读契约)

## 3. service 编排 + router 端点

- [x] 3.1 service:数据源校验复用 fetch-metadata 同形逻辑(存在+启用+
      归属当前租户,同形拒绝);内省失败整体失败(不产出半份);纯函数
      组装五节报告(零覆盖表/纯结构登记/缺标签列/失败问题簇/few-shot
      概况),各节计数与封顶清单;验证:1.1/1.2 测试转绿
- [x] 3.2 router:`GET /system/rag/coverage-report`,租户上下文 +
      system:rag:manage 依赖(与 fetch-metadata 同构);不加审计写、
      不加缓存;验证:1.2 权限/同形拒绝用例经 router 层转绿
- [x] 3.3 确定性回归:同库态连续两次调用响应一致、无 LLM 调用、无缓存
      失效调用(单测以依赖替身断言零交互);验证:专属用例绿

## 4. 契约与门禁(ADR-005)

- [x] 4.1 响应模型入 OpenAPI(schema 注解齐全),`make contracts` 重新
      生成,`contracts/openapi.json` 与前端类型产物随代码就绪;验证:
      `git status` 含契约产物更新、CI 漂移门禁本地预演(`git diff
      --exit-code` 于再生成后)通过
- [x] 4.2 `make check` 全绿(api ruff+mypy+pytest 对应子集逐 task 已跑,
      此处全量收口),pyright 通过
- [x] 4.3 手工冒烟:起 api,对 default 数据源触发覆盖报告,核对四类
      空白与已知缺口相符(如 sakila_ext 类零注释列、无示例表),封顶与
      计数字段如实;对不存在数据源请求同形拒绝;记录冒烟路径于 apply
