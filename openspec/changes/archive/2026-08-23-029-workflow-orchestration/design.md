# 029 workflow-orchestration 技术设计

## Context

能力积木(019 工具/027 沙箱/025 Bot/028 MCP)单轮形态齐备;工作流
把它们串成确定性 DAG。约束:引擎零凭证(ADR-002);节点执行 100%
复用既有防御;无 eval;v1 无画布(Non-goal 已声明)。

## Decisions

### D1: 定义模型(0017)

sys_workflow_def:id uuid7 / tenant_id / wf_key(pattern 同
server_key)/ name / version int / definition JSONB({nodes:[{id,
type, config, depends_on:[...]}], edges 隐含于 depends_on}) /
enabled bool / created_at;唯一 (tenant_id, wf_key, version)。
**版本冻结**:同 key 已有行→保存即 max(version)+1 插新行;旧版本
物理不可改(无 UPDATE 端点);enabled 仅作用于最新版本(旧版本随
新版本产生自动失活——执行恒按调用方指定版本或最新启用版本)。

sys_workflow_run:id / tenant_id / wf_key / version / trigger_source
(MANUAL|API)/ inputs JSONB / status VARCHAR CHECK(SUCCESS/FAILED/
TIMEOUT/STEP_LIMIT)/ node_runs JSONB([{node_id,status,outputs 摘要,
error,duration_ms}])/ error / duration_ms / created_at。

### D2: DAG 结构校验(纯函数,models.validate_dag)

- 节点 id 唯一、非空;depends_on 引用存在;
- 无环:Kahn 拓扑排序,残留节点即环(报错列出参与环节点 id);
- 无孤立节点:多节点时,无 depends_on 且无后继者=孤立(单节点
  工作流合法);
- type ∈ {sql, http, condition, loop, transform, agent};各类型
  必填配置键校验(sql: sql;datasource_key 可空=http: url/method;
  condition: left/op/right;loop: items_ref+body[子节点数组]+
  max_iterations;transform: template;agent: instruction)。

### D3: 执行引擎(engine.py,零框架)

状态机:`context: dict[node_id → {"outputs": dict}]`(深拷贝隔离,
节点写入即冻结历史);引用解析 `{{node_id.outputs.a.b}}`(点路径,
不存在→空串,不炸);拓扑序逐节点:

- sql:SqlRunner 端口(=internal execute 适配,触发者身份)——
  verdict≠PASS 即节点 FAILED;rows 进 outputs;
- http:HttpRunner 端口——SSRF 判定:URL 解析 host →
  getaddrinfo → 任一解析结果落入私网/回环/链路本地/元数据
  (169.254.0.0/16)/保留段即拒(node FAILED,不发出请求);
  响应 1MB 截断+15s 超时;outputs={status, body 截断 64KB};
- condition:手写比较(left/right 经引用解析后按数值优先/字符串
  兜底比较;contains=子串);outputs={result: bool};
  **选路**:后继节点的 depends_on 可满足性含条件门——v1 简化:
  condition 为 False 时,其直接后继中 config.on="true" 的跳过、
  on="false" 的执行(缺省 on="true";非条件后继不受影响);
- loop:items_ref 解析为数组 → 对每项顺序执行 body 子节点段
  (子段内上下文以 loop.item 暴露);迭代超 max_iterations(硬顶
  100)即 FAILED;
- transform:template(字符串或嵌套 dict/list)递归替换 {{ref}};
- agent:AgentRunner 端口(019 回调;tools=触发者权限过滤集,
  channel="workflow");outputs={summary, sql, rows 截断 100};

节点失败(异常/verdict/SSRF 拒)→ run FAILED fail-fast;
全局:asyncio.wait_for(默认 120s)+ 步数上限(默认 200 节点执行
计数,循环迭代计入);超限 status=TIMEOUT/STEP_LIMIT,已完成
节点快照照常落 run。

### D4: 端口与组合根

ports:SqlRunner/HttpRunner/AgentRunner/WorkflowRepo(定义+run 存储)/
OperLogger。组合根(router):SqlRunner=internal.service.execute 适配
(同 026 ExecuteRunner 异常翻译);HttpRunner=httpx 实现(getaddrinfo
经 loop.run_in_executor);AgentRunner=agent query_react 适配
(tools=filter_specs(触发者权限点集))。

### D5: 端点

- POST /system/workflow(创建/新版本;保存即校验)
- GET /system/workflow(列表,租户内最新版本)
- GET /system/workflow/{key}(详情+版本列表)/ GET .../versions/{v}
- POST /system/workflow/{key}/enable|disable(仅最新版本)
- DELETE /system/workflow/{key}(整 key 全版本)
- POST /system/workflow/{key}/run {inputs, version?}(workflow:execute;
  trigger_source=MANUAL[web]/API[带 X-Api-Key?——v1 统一 JWT,
  API 触发即外部系统持 JWT;api-key 通道随 032])
- GET /system/workflow/{key}/runs?page=(system:workflow:manage)

### D6: 测试策略

- 纯函数:validate_dag 全分支(环/孤立/类型/必填);引用解析;
  模板替换;condition 比较;拓扑序。
- 引擎(mock runner 三端口):条件选路/循环上限/全局超时/步数
  上限/上下文不可变(节点写不进历史)/fail-fast 快照。
- SSRF 矩阵(测试先行):127.x/10.x/172.16/192.168/169.254.169.254/
  localhost/0.0.0.0/IPv6 ::1 全拒;公网域名放行(mock runner 层
  判定函数直测)。
- HTTP 链路:定义 CRUD/版本冻结/404 同形/权限点/run 历史分页/
  sql 节点行级复校(SELF 用户)/agent 节点权限过滤。

## Risks / Trade-offs

- [DAG 定义是管理面代码] → 写定义要 system:workflow:manage;
  执行要 workflow:execute;节点级防御以触发者身份逐层复校(无
  权限放大——触发者不能借工作流获得超过自身的访问)。
- [http 节点 SSRF 残余] → DNS 解析后判定+不接 redirect(follow_
  redirects=False 防 302 跳转内网);TOCTOU(解析后再解析变化)
  为已知残余,挂账。
- [agent 节点成本] → 每节点一次 ReAct 全链;步数上限+超时闸
  兜底;无缓存(v1)。

## Migration Plan

0017 两表;权限点 22/23 只增;存量零影响;回滚=删端点(纯增)。

## Open Questions

- O1:定时/webhook 触发(032);O2:可视化画布(前端域);
  O3:并行分支与子工作流;O4:执行事件流(SSE 监控,随 032
  或画布域);O5:SSRF DNS TOCTOU(连接期 IP 钉扎)。
