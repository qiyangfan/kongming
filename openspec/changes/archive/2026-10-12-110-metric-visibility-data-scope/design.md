# Design — 110 指标定义可见性按 data_scope 过滤

## 地形结论(2026-10-12 勘测)

- 指标定义域在 `apps/api/kongming/semantic/`,表 `rag.metric_definitions`(0012 建表)仅有 tenant+datasource 两维,**无任何归属/可见性列**。
- 注入点在 `rag/router.py` `/internal/rag/retrieve`:先 `svc.recall(...)`,再 `SemanticService.enrich(tenant_id, datasource_key, question, tables)` → `list_metrics`(仅 tenant+ds 过滤)→ `match_metrics`(子串 top-5)→ `format/project`(076 结构化派生)。**enrich 在 ContextCache get/put 之外**——按调用者过滤不涉及缓存键(与 102 必须并指纹的地形不同)。
- data_scope(083)现役消费面是 SQL 行级改写(`datascope/applier.py::apply`);判定原语(`most_permissive`/`custom_dept_ids`/`dept_and_below`/`user_dept_id`)在 `datascope/repository.py` + `datascope/service.py`,可复用。
- caller 身份管道已由 102 铺到注入点门口:`RetrieveIn.user_id`(可选)已存在并已传给 `svc.recall(caller_user_id=...)`,agent 侧零改动。

## Decisions

**D1 判定语义 = 定义侧档位 × 调用侧有效范围**。指标定义增 `visible_scope ∈ {'ALL','DEPT'}`(缺省 ALL)+ `dept_id`(DEPT 档必填);调用者有效范围=启用角色 `most_permissive`:ALL→全量;DEPT/DEPT_AND_BELOW/CUSTOM→ALL 档 ∪(DEPT 档且 dept_id∈调用者部门集合)(DEPT=本部门、DEPT_AND_BELOW=递归子部门、CUSTOM=勾选并集,复用 083 集合解析);SELF/无身份(caller_user_id=None)/用户不存在/无启用角色→仅 ALL 档。**None=仅 ALL 档**(不设「不过滤」绕过面;与 102「缺省不携带=纯公共」同哲学;enrich 唯一生产调用点即 /internal/rag/retrieve,内部端点另有 internal token 门槛)。

否决方案:①复用 `datascope/rewrite` 做可见性——行级改写作用于执行结果集,口径泄露面在提示词注入,两机制耦合会造成「改写=可见性」的语义错位;②照抄 102 的 team 维度——roadmap 明文 data_scope(部门治理轴),team 是工作空间协作轴,正交不混装。

**D2 CUSTOM 空部门集合读面降级**。083 的 fail-closed(空集合拒执行)语义只适用执行/写面;注入是读面,降级方向=收紧(少注入),无越权风险——空集合按「仅 ALL 档」处理 + debug 日志,不抛异常、不阻断召回。复用 `datascope.service.unsupported_reason` 判据,处置分层在代码注释与 spec 场景中明示。

**D3 判定下推仓储 SQL 单点**(102「SQL 同判投影」纪律):`list_metrics` 增可选 `visible_dept_ids: list[str] | None` 参数——None=不过滤(全量,管理面/全量调用者);序列=过滤(`(visible_scope='ALL' OR (visible_scope='DEPT' AND dept_id = ANY(CAST(:depts AS uuid[]))))`,空数组自然退化仅 ALL 档)。match_metrics 在过滤后候选集上运行,076 派生语义不变。

**D4 端口与组合根**。`semantic/ports.py` 增窄端口 `CallerDeptScopeResolver.resolve(user_id, tenant_id) -> CallerScope`(`dataclass`:`universal: bool` + `dept_ids: tuple[str, ...]`);适配器放 `semantic/router.py`(照 102 `_TeamMembershipAdapter` 先例),内部组合 `SqlDataScopeRepo`(`user_role_scopes`→`most_permissive`→ALL 早退→`user_dept_id`/`custom_dept_ids`/`dept_and_below`)。semantic 领域零 datascope import(lint-imports 既有契约不放宽)。`enrich` 签名纯增 `caller_user_id: str | None = None`;注入点 `rag/router.py` 传 `str(req.user_id)`(None 原样)。缓存零改动。

**D5 管理面全量视角**。`MetricIn`/`MetricOut` 纯增 `visible_scope`/`dept_id`;校验:DEPT 档缺 dept_id→400 文案级拒(「自定义范围需至少选择一个部门」同风格);ALL 档 dept_id 强制置 NULL。GET 列表/详情不过滤(admin 全量管理视角,102 同口径);POST upsert 与 PUT(启停带原值)透传。web `SemanticPage` 指标表单加可见范围 Select(ALL/DEPT)+部门 Select(DEPT 档显示,候选走既有部门查询)、列表纯增列(SHOULD,不阻塞门禁)。

**D6 迁移 0053(纯增,零回填)**:`ALTER TABLE rag.metric_definitions ADD COLUMN visible_scope VARCHAR(8) NOT NULL DEFAULT 'ALL' CHECK (visible_scope IN ('ALL','DEPT'))` + `ADD COLUMN dept_id UUID REFERENCES sys_dept(id)`(ON DELETE RESTRICT:部门删除被引用即拒,保守零级联意外);downgrade 删两列。缺省 ALL=存量行为零回归。

**D7 评估与基线可比性**:evals 调用者=超管(effective ALL)→ 本变更零影响基线;DEPT 档受限指标对受限调用者少注入是特性不是回归。实机冒烟用评估栈一次性账号造部门/角色形态验证(见 tasks 3.2)。

## Risks

- semantic-layer 主规格「生成链路注入」已被 076/095 两次修订——delta MUST 按当前主规格超集书写,否则归档校验撞车(102 实录教训)。
- `sys_dept` 无 tenants 隔离假设:部门树是租户内体系(0003 起挂 tenant),FK 不带 tenant 复合校验,依赖管理端校验层保证 dept 与 metric 同租户(与 083 部门选择面同信任级别,登记为已知边界)。
