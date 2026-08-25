# 027 cloud-code-sandbox:云端代码沙箱

## Why

当前 ReAct 内核(019)只有取数工具(execute_sql/rag_retrieve),LLM 拿到
数据后只能"说"不能"算"——统计建模、预测、自定义可视化等 NL2Insight
场景无法落地。本变更把受隔离的 Python 执行环境注册为 ReAct 工具
`run_python_code`,让 LLM 自主完成"取数→计算→解读"闭环,同时把隔离
做成构造级(独立容器即起即销、无网络、零凭证),对标 Claude Code
Interpreter 的最小安全形态。这是 Data-Agent 区别于"SQL 翻译器"的
杀手锏能力(上游 cloud-code-sandbox 域的 Kongming 适配)。

## What Changes

- **沙箱执行域包**(api 新 `sandbox/` 域):SandboxRunner 端口 +
  PodmanSandboxRunner 实现(asyncio 子进程调 podman CLI,禁 shell);
  每次执行独立容器即起即销,`--network none`+只读根文件系统+非 root+
  cap-drop ALL+no-new-privileges;内存/CPU/pids/时长硬配额,超限分类
  RESOURCE_LIMIT/TIMEOUT;fail-closed(运行时不可达→ERROR 不裸 500)。
- **数据分析镜像**:infra/sandbox.Dockerfile(python:3.13-slim +
  pandas/numpy/matplotlib 最小栈,内置 runner 引导脚本:stdin 协议
  JSON 读入 code 与数据,df 变量注入,stdout 限长收集,matplotlib
  当前图自动收集为 base64,协议行输出)。
- **ReAct 新工具** `run_python_code`(019 注册表纯增):参数
  `{code, data_sql?}`;**data_sql 由服务端经六层防御取数后注入 df**
  (数据不出边界、LLM 不经手大行数据、行级权限/防火墙全量适用);
  新权限点 `tool:code:execute`(排序 19,显式授予制——不推荐进
  DEVELOPER,与公告写工具同哲学:代码执行面风险高于只读 SQL)。
- **执行审计**:sys_sandbox_run(0015 迁移):user/tenant/trace/
  code 全文/input 行列数/stdout·result 摘要/images 数/duration/
  exit_code/status(SUCCESS/ERROR/TIMEOUT/RESOURCE_LIMIT),与
  sys_tool_audit 同事务落库。
- **配置**:`KM_SANDBOX_ENABLED`(默认 false)/镜像名/超时/内存/CPU
  进 Settings;组合根 enabled 且 podman 可用才接 runner,否则 handler
  收敛 ERROR「沙箱未启用」。

## Capabilities

### New Capabilities

- `cloud-code-sandbox`:沙箱隔离执行、资源配额、数据注入、数据分析
  环境、安全与审计的行为契约。

### Modified Capabilities

- `tool-calling`:内置工具集纯增 run_python_code(注册表/handler
  分发/权限点;六层防御与限流语义不变)。

## Non-goals

- 不做 compose 内 api 容器的沙箱接线(podman socket/remote client
  部署形态挂账;v1 执行宿主=api 直跑宿主 podman CLI,dev/单机
  systemd 形态可用)。
- 不做沙箱产图的前端渲染位(tool result 契约内含 images 字段;
  图片渲染随后续 artifact 深化域;LLM 文本模型本就不消费图)。
- 不做 sklearn/plotly/seaborn(最小栈 pandas/numpy/matplotlib;
  镜像加层即扩展,无架构变化);不做网络白名单(v1 `--network none`
  更严,数据源取数经服务端注入,沙箱无需网络)。
- 不做沙箱池化/预热(即起即销;冷启动 ~1s 级,性能挂账);
  不做语言内沙箱(restricted builtins 之类——容器已是安全边界,
  语言内限制是假安全感)。
- 不做 sys_sandbox_run 管理 UI(SQL 可查;审计可视化随后续域)。
- 不改 agent 进程零代码(工具 schema 由控制面注入,ReAct 内核通用);
  不改 prompts.py(prompt 资产纪律,用法写进工具 description)。

## Impact

- **代码**:api 新 sandbox 域包(models/ports/runner/service/repository)
  + toolcall 注册表/分发纯增 + 组合根接线 + config 增项;
  infra/sandbox.Dockerfile 新文件;agent/web 零改动。
- **Schema**:0015 迁移 sys_sandbox_run(ADR-004 约定:uuid7 主键/
  VARCHAR+CHECK 状态列/timestamptz)。
- **契约**:零端点变化(工具经既有 /internal/tool/execute 与 chat
  链路);权限点种子纯增(只增不改纪律)。
- **ADR 引用**:ADR-002(信任边界延伸:沙箱零凭证,执行回调控制面);
  ADR-004(新表走新迁移)。
- **安全**:安全规则类测试先行(隔离/配额/超时/未授权/未启用);
  podman 集成测试 shutil.which 守卫(CI 无 podman 零炸)。
- **运行依赖**:dev 冒烟需本机 podman 构建 sandbox 镜像
  (infra 脚本一键);CI 不构建镜像(mock runner 层覆盖)。
