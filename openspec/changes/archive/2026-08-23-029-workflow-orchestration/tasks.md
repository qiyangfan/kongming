# 029 workflow-orchestration 实施任务

## 1. 迁移与域包骨架

- [x] 1.1 0017 迁移(sys_workflow_def 版本冻结唯一键+sys_workflow_run
  四终态 CHECK)+conftest 表清单
- [x] 1.2 models:validate_dag 纯函数(环/孤立/类型/必填)+引用解析
  +模板替换+condition 比较;ports(SqlRunner/HttpRunner/AgentRunner/
  WorkflowRepo/OperLogger)+import-linter 契约
- [x] 1.3 权限点种子(system:workflow:manage 22 / workflow:execute 23
  进 DEVELOPER 推荐集)+种子测试计数

## 2. 执行引擎(测试先行)

- [x] 2.1 失败测试:validate_dag 全分支/引用解析/模板/比较/拓扑序;
  引擎(mock 三端口):条件选路/循环上限/全局超时/步数上限/上下文
  不可变/fail-fast 快照
- [x] 2.2 engine.py 实现(深拷贝冻结/逐节点分发/skip 传播/wait_for
  全局预算/步数计数)

## 3. 节点执行器(测试先行:安全类)

- [x] 3.1 失败测试:SSRF 矩阵(127.x/10.x/172.16/192.168/169.254/
  localhost/::1/0.0.0.0 全拒;302 不跟随);sql 节点写语句拦;
  agent 节点权限过滤
- [x] 3.2 http runner(httpx+getaddrinfo 判定+1MB+15s+不跟随重定向)
- [x] 3.3 sql/agent runner 适配(internal execute 异常翻译同 026;
  agent query_react 注入触发者权限过滤集 channel="workflow")

## 4. 端点与历史(测试先行:安全类)

- [x] 4.1 失败测试:CRUD/版本冻结(旧版本不可改)/404 同形/权限点
  403/run 历史分页/行级复校(SELF 用户 sql 节点)
- [x] 4.2 /system/workflow/* 端点族+run 编排(同步执行,超时即返)+
  operlog 审计

## 5. web

- [x] 5.1 /admin/workflow 子页(列表+JSON 编辑器[校验反馈]+启停+
  运行对话框+run 历史分页+节点快照展开)

## 6. 验证与收尾

- [x] 6.1 make check 全绿;make contracts;016 评估 smoke 复验;
  真 LLM 冒烟(建工作流[sql→transform→agent]→运行→历史核验;
  SSRF 冒烟 169.254 拒)
- [x] 6.2 R1 独立评审+修复清零
- [x] 6.3 HANDOFF+挂账(画布/定时触发/并行分支/DNS TOCTOU)+归档
