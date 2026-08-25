## Purpose

为 ReAct 内核提供容器级隔离的 Python 执行面:LLM 生成的数据分析代码
在即起即销的沙箱中运行,数据经服务端取数注入(不出边界),资源硬配额
与全量审计兜底,支撑"取数→计算→解读"的 NL2Insight 闭环。

## Requirements

### Requirement: 沙箱隔离执行

系统 SHALL 为每次代码执行启动独立容器(podman),执行结束(成功/失败/
超时)即销毁,无状态残留;沙箱 MUST 无网络(--network none)、只读
根文件系统、非 root 运行、摘除全部 capabilities、禁提权
(no-new-privileges);沙箱进程 MUST NOT 持有任何凭证(数据库/API key
均无,数据仅经 stdin 注入)。

#### Scenario: 即起即销无残留

- GIVEN 一次执行完成(任意终态)
- WHEN 检查容器列表
- THEN 无该次执行的容器/卷/文件残留

#### Scenario: 网络隔离

- GIVEN 沙箱代码尝试 socket 连接任意外网地址
- WHEN 执行
- THEN 连接失败(无网络命名空间),错误回传

#### Scenario: 零凭证

- GIVEN 沙箱执行环境
- WHEN 代码枚举环境变量/凭证文件
- THEN 无任何数据库凭证与 API key

### Requirement: 资源配额硬限制

系统 SHALL 对每个沙箱强制内存(默认 256MB)/CPU(默认 0.5 核)/pids
(64)/执行时长(默认 30s)上限,超限 MUST 强制终止并按
RESOURCE_LIMIT/TIMEOUT 分类落审计;cgroup 强制(内核级),不依赖
代码自觉。

#### Scenario: 内存超限终止

- GIVEN 代码分配远超配额的内存
- WHEN 触达 cgroup 上限
- THEN 容器被终止,status=RESOURCE_LIMIT,返回资源超限错误

#### Scenario: 执行超时终止

- GIVEN 代码执行超过配置时长
- WHEN 超时
- THEN 强制终止,status=TIMEOUT,容器即销

### Requirement: 数据分析环境

沙箱镜像 SHALL 预装 pandas/numpy/matplotlib;代码全局命名空间 SHALL
预置 pd/np/plt 别名;matplotlib 当前图 SHALL 自动收集为 png base64
(单张 ≤500KB,超即弃图留注记)随结果回传;stdout SHALL 限长收集
(64KB 截断)。

#### Scenario: 分析栈可用

- GIVEN 沙箱执行
- WHEN 代码 import pandas 并操作 df
- THEN 库可用,无需安装步骤

#### Scenario: 图表收集

- GIVEN 代码用 plt 画图
- WHEN 执行结束
- THEN 结果含 images 数组(base64 png)

### Requirement: 数据服务端注入

工具参数 data_sql 非空时,系统 SHALL 先经 019 内部执行端口取数
(六层防御全量:防火墙/行级权限/只读池,limit=1000),结果以 df
变量(pandas DataFrame)注入沙箱;注入载荷 JSON 序列化超 1MB SHALL
拒绝(ERROR,不启动沙箱);data_sql 取数未过检 SHALL 直接返回该
结果(沙箱不启动);回灌 LLM 的注入说明 SHALL 只含行列数,不含行
数据。无 data_sql 时纯计算,代码引用 df 得 NameError 原文。

#### Scenario: 取数注入闭环

- GIVEN LLM 传 data_sql="SELECT ..." 与引用 df 的 code
- WHEN 执行
- THEN data_sql 经六层防御取数,df 在沙箱可用,工具结果含
  「已注入 df(行×列)」说明

#### Scenario: 注入超限拒绝

- GIVEN data_sql 结果 JSON 化超 1MB
- WHEN 调用
- THEN ERROR「数据注入超上限」,沙箱不启动

#### Scenario: 行级权限同构

- GIVEN 行级权限 SELF 用户的 data_sql
- WHEN 取数
- THEN df 只含本人可见行(与 execute_sql 工具同语义)

### Requirement: 作为 ReAct 工具接入

系统 SHALL 将沙箱注册为内置工具 run_python_code(019 注册表唯一
真理源),权限点 tool:code:execute(显式授予制,不进开发者推荐集);
调用 SHALL 走 019 六层防御与按用户/租户限流;沙箱未启用(配置关闭
或运行时不可达)SHALL 收敛 ERROR「沙箱未启用」并落审计,不裸 500;
code 参数上限 8000 字符。

#### Scenario: 未授权不注入

- GIVEN 用户无 tool:code:execute 权限
- WHEN 进入 ReAct 模式
- THEN 注入工具集不含 run_python_code;直接回调该工具按越权拒绝

#### Scenario: 未启用友好降级

- GIVEN KM_SANDBOX_ENABLED=false
- WHEN LLM 调用 run_python_code
- THEN 返回 ERROR「沙箱未启用」,落工具审计,问答链路继续

### Requirement: 执行审计

每次沙箱执行 SHALL 落 sys_sandbox_run(与 sys_tool_audit 同款独立短事务,不占请求事务,失败仅日志不阻断):
user_id/tenant_id/trace_id/code_text 全文(≤8000)/data_rows/
data_cols/stdout_tail(≤2000)/result_tail(≤2000)/images_count/
duration_ms/exit_code/status(SUCCESS/ERROR/TIMEOUT/RESOURCE_LIMIT)/
error_tail(≤2000)/created_at。

#### Scenario: 全终态落审计

- GIVEN 成功/失败/超时/超限各一次执行
- WHEN 查询 sys_sandbox_run
- THEN 四行 status 各异,耗时与 exit_code 如实
