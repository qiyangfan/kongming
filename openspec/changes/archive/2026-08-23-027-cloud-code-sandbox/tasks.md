# 027 cloud-code-sandbox 实施任务

## 1. 镜像与运行器

- [x] 1.1 infra/sandbox.Dockerfile(python:3.13-slim+pandas/numpy/
  matplotlib;非 root sandbox 用户;内置 /runner.py 引导脚本:stdin
  协议/df 注入/stdout 限长/plt 收集/协议行输出)
- [x] 1.2 sandbox 域包骨架(models:SandboxResult/status 枚举;
  ports:SandboxRunner Protocol;runner.py:PodmanSandboxRunner——
  asyncio 子进程参数数组禁 shell/容器参数常量拼装/wait_for 超时
  kill/exit 137→RESOURCE_LIMIT/协议行解析)
- [x] 1.3 config 增项(KM_SANDBOX_ENABLED 默认 false/镜像名/超时/
  内存/CPU)+ .env.example

## 2. 审计与迁移(测试先行)

- [x] 2.1 失败测试:sys_sandbox_run 字段全落(四终态 status 分类)
- [x] 2.2 0015 迁移(uuid7/VARCHAR+CHECK/timestamptz)+
  repository(与 toolcall 审计同事务)
- [x] 2.3 sandbox service 编排(取数注入→1MB 闸→执行→审计落库)

## 3. 工具接入(测试先行:安全类)

- [x] 3.1 失败测试:无 tool:code:execute 权限不注入 schema+直调拒绝;
  未启用 ERROR「沙箱未启用」落审计;data_sql 六层防御同构(SELF
  行级);注入超 1MB 拒绝;code 上限 8000
- [x] 3.2 注册表纯增 run_python_code+权限点种子(sort 19,不进
  推荐集)+handler 分发+组合根(enabled 且 which podman 才接 runner)

## 4. podman 集成测试(shutil.which 守卫)

- [x] 4.1 真容器:df.sum()/外网连接失败/内存爆炸 RESOURCE_LIMIT/
  sleep 超时 TIMEOUT/ps 无残留

## 5. 验证与收尾

- [x] 5.1 make check 全绿;016 评估 smoke 复验;真 LLM 冒烟(ReAct
  取数→代码分析→解读;审计行核验)
- [x] 5.2 R1 独立评审+修复清零
- [x] 5.3 HANDOFF+挂账(compose 接线/result_preview/图片渲染位)+归档
