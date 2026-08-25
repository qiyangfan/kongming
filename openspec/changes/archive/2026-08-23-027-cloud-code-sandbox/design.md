# 027 cloud-code-sandbox 技术设计

## Context

019 ReAct 内核的工具集止于取数(execute_sql 结果集即终点)。NL2Insight
需要通用计算面:LLM 生成 Python 代码在隔离环境执行。约束:ADR-002
信任边界(agent 零凭证——沙箱同理零凭证,执行 100% 回调控制面);
019 六层防御与审计语义不稀释;本机 podman;CI 无容器运行时。

## Goals / Non-Goals

Goals:容器级隔离的 run_python_code 工具;数据经服务端注入(不出
边界);资源硬配额;全量审计;测试先行。
Non-Goals:见 proposal(compose 接线/图片渲染位/扩展库/池化/语言内
沙箱/审计 UI)。

## Decisions

### D1: 执行宿主=podman CLI 子进程

`asyncio.create_subprocess_exec`(参数数组,禁 shell)调
`podman run`,配置项全来自 Settings(镜像名/超时/内存/CPU)。
选 CLI 而非 REST socket:api 容器内无 podman CLI 是 compose 部署的
挂账项,dev/单机宿主形态 CLI 最直接;runner 端口隔离实现,未来换
remote client 不动用例层。

容器参数(全部常量拼装,用户输入零拼接):
`podman run --rm -i --network none --read-only
--tmpfs /tmp:rw,nosuid,nodev,size=16m(MPLCONFIGDIR=/tmp/mpl)
--memory <m> --cpus <c> --pids-limit 64 --cap-drop ALL
--security-opt no-new-privileges --user 1000(镜像内 sandbox 用户)
--stop-timeout 1 --name km-sbx-<uuid8> <image>`

### D2: stdin/stdout 协议

宿主 → 容器 stdin:`{"code": str, "data": {"columns": [...], "rows": [[...]]} | null}`。
镜像内 /runner.py:
1. 读 stdin JSON;data 非空 → `df = pandas.DataFrame(rows, columns=columns)`;
2. `exec(compile(code), {"df": df, "pd": pandas, "np": numpy, "plt": matplotlib.pyplot})`
   ——容器是安全边界,不做语言内沙箱(假安全感);
3. stdout 重定向收集(限长 64KB 截断);异常 traceback 限长收集;
4. `plt.get_fignums()` 非空 → 末图 `savefig` png → base64(上限 500KB,
   超即弃图留注记);
5. 末行打印协议:`__KM_RESULT__{json}`(status/stdout/error/images/
   result_preview=末表达式 repr 截断 2000——v1 不做末表达式求值,
   result_preview 恒 None,留字段位)。

**images 不回灌 LLM**(实施期核实):agent 观察文本截断 2000
(_OBSERVE_TEXT_MAX),500KB base64 只会烧观察预算;回灌 agent 的
工具结果=投影形态(images_count/image_note,不含 base64),完整
SandboxOutcome.images 仅宿主侧存活(渲染位落地前实质随响应结束
丢弃,027 Non-goals 已声明)——契约演练价值在协议链路打通。

宿主解析末行;无协议行(镜像损坏/OOM 直杀)→ 按 exit_code 分类。

### D3: 资源超限与超时分类

`asyncio.wait_for(communicate, timeout)` 超时 → kill 子进程组
(`--rm` 兜底清理)→ status=TIMEOUT。exit_code 137(SIGKILL,典型
cgroup OOM)→ RESOURCE_LIMIT;其余非零 → ERROR(traceback 摘要)。
pids-limit/内存/CPU 由 cgroup 强制执行(内核级,非应用层自觉)。

### D4: 数据注入(data_sql)

handler(toolcall _dispatch 新分支):
1. `data_sql` 非空 → 先经 `self._sql.run`(019 内部执行端口,六层
   防御全量:防火墙/行级/只读池/limit=1000)取 columns/rows;
   verdict≠PASS → 直接返回该结果(沙箱不启动);
2. 序列化体积闸:JSON 化后 >1MB → ERROR「数据注入超 1MB 上限」
   (防内存放大;1000 行常规结果集约数十 KB,1MB 已宽);
3. 工具结果回 LLM 的 injected 说明只含行列数(「已注入 df(1000×5)」),
   不回灌行数据(LLM 上下文保护)。

无 data_sql = 纯计算形态(df 不存在,代码引用即 NameError——runner
globals 不放 df 键,错误原文回传由 LLM 自纠)。

### D5: 审计(sys_sandbox_run,0015)

列:id uuid7 主键 / tenant_id / user_id / trace_id / tool_call 关联
不建( sys_tool_audit 已有 trace 关联,双表经 trace_id 可 JOIN)/
code_text 全文(上限 8000,传输层 maxLength 对齐;与 sys_sql_audit
留 sql_text 同哲学——排障需要)/ data_rows int / data_cols int /
stdout_tail varchar(2000) / result_tail varchar(2000) /
images_count smallint→int / duration_ms int / exit_code int /
status VARCHAR CHECK(SUCCESS/ERROR/TIMEOUT/RESOURCE_LIMIT) /
error_tail varchar(2000) / created_at timestamptz。
独立短事务落库(与 019 AuditWriter 同款:get_sessionmaker 独立会话,
不占用/不阻断请求事务;失败仅日志)——实施期核实:019 审计即独立短
事务形态,design 原稿「同事务」表述据此更正。

### D6: 组合根与降级

`KM_SANDBOX_ENABLED`(默认 false)。enabled 且 `shutil.which("podman")`
→ PodmanSandboxRunner 注入 toolcall service;否则 None:
run_python_code handler 收敛 ERROR「沙箱未启用」(走 toolcall ERROR
审计,不裸 500)。注册表静态含该工具——schema 注入与否仍由权限点
决定(与启用态正交;未授权用户根本看不到工具)。

### D7: 权限点与种子

`("tool:code:execute", "工具-代码执行", 19)`;不进
ROLE_RECOMMENDED_PERMS(显式授予制,公告写工具同哲学);
SUPER_ADMIN 全量补齐逻辑自动获得(既有机制,零改动)。

### D8: 测试策略

- 单测(mock runner):协议解析/超时与 137 分类/审计字段/df 组包/
  1MB 闸/未启用 ERROR/权限点过滤(无权限不注入 schema)。
- podman 集成( shutil.which 守卫,CI 自动 skip):真容器跑
  `df.sum()`/网络探测失败(socket 连接外网 → 异常)/内存爆炸
  (OOM→RESOURCE_LIMIT)/sleep 超时(→TIMEOUT)/即起即销
  (podman ps 无残留)。
- 冒烟(真 LLM):ReAct 问「各分类影片数画柱状图并指出最高」→
  LLM 自主 execute_sql→run_python_code→答案解读;审计行核验。

## Risks / Trade-offs

- [容器逃逸] → cap-drop ALL+no-new-privileges+只读根 fs+非 root+
  无网络;镜像最小化;--rm 即销。残余风险=podman 自身 CVE(与
  宿主运维同级,不放大)。
- [数据外泄] → --network none(比白名单更严);数据只进不出
  (stdin 单向);审计留全文可溯。
- [资源耗尽 DoS] → cgroup 硬限+pids+超时 kill;019 限流按用户/
  租户既有(代码执行与 SQL 执行同闸);1MB 注入闸。
- [podman 不存在的环境] → D6 降级 ERROR;CI 测试全 mock 层。

## Migration Plan

0015 建新表;权限点种子只增;配置默认关闭(滚动重启零影响,
显式开启才接 runner);回滚=关开关(工具 handler 收敛 ERROR)。

## Open Questions

- O1:compose 部署的 podman 接线(socket 挂载+CLI 层或 remote
  client)——随部署域定,挂账。
- O2:result_preview 末表达式求值(ast 末节点 Expr 判定)→ 体验
  增强,随后续。
- O3:images 前端渲染位(artifact image 卡)→ 随 artifact 深化域。
