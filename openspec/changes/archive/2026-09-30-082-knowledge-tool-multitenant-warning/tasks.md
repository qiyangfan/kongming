# 082-knowledge-tool-multitenant-warning 实施任务

## 1. 测试先行:多租户用例翻转(先红)

- [x] 1.1 将 `test_endpoint_gate_multi_tenant_blocked`(断方拒注册)翻转为
      `test_endpoint_gate_multitenant_warns_but_registers`:2 租户 + 合法端点 →
      `_knowledge_endpoint` 返回 URL(注册发生),且 caplog 捕获 WARNING 级告警
      (含租户数与「共享检索面」语义);先跑红
- [x] 1.2 既有 `test_endpoint_gate_single_tenant_ok` / `test_endpoint_gate_non_local_host` /
      `test_endpoint_gate_unconfigured` 三用例语义不变(注册/拒配/缺省),复跑确认

## 2. 实现(单点:main.py 多租户分支)

- [x] 2.1 `kongming/main.py` `_knowledge_endpoint`:多租户分支由 `return None`
      改为 `log.warning(...)` 后 `return url`;docstring 同步(081→082 口径);
      租户计数查询保留(告警内容需要,design D1)
      **验证**:1.1、1.2 转绿
- [x] 2.2 静态:mypy/ruff/pyright 改动文件全过

## 3. 收尾

- [x] 3.1 `cd apps/api && uv run pytest -q`(全量;重点 test_external_knowledge +
      test_auth_seed 零回归)
- [x] 3.2 `make contracts-check`:openapi.json 零漂移(工具注入集在单租户/配置合法
      时与 081 逐字节一致)
- [x] 3.3 `openspec validate 2026-09-30-082-knowledge-tool-multitenant-warning
      --strict` 通过后 `openspec archive`(MODIFIED delta 合入主 specs)
- [x] 3.4 代码评审迭代循环一轮(范围=本变更 + 081 遗留未提交面):无真缺陷方可终验

## 实施证据(apply 记录,2026-09-30)

- **1.1/1.2 测试翻转**:多租户用例由「断言拒注册」翻转为「断言注册 + caplog 共享面
  告警」;发现并修复一类真实的**测试顺序污染**:会话级 alembic upgrade 的
  fileConfig(disable_existing_loggers)把收集期已创建的 "kongming.main" logger
  置 disabled(与 test_sqlfw_engine 同因,该文件 91-93 行有同款先例),文件隔离跑
  侥幸通过、全量必红。修法=仓库既有约定:本文件 autouse fixture 复启该 logger。
  定位过程:全量红 → a/b 半区对半二分均红 → caplog.records=[] → 探针打出
  disabled=True。前半区(a+b)复跑 194 passed 为闸。
- **2.1 实现**:`main._knowledge_endpoint` 多租户分支 return None → 告警后 return
  url;租户计数查询保留(告警内容)。
- **2.2/3.x 静态与门禁**:ruff/mypy/pyright 全过。**pyright 由 12 错清到 0**——
  其中 10 个(_knowledge_endpoint/_SikuKnowledgeClient private usage 4+2、
  registry._ACTIVE_REGISTRY 常量重绑 1、router json 解析 unknown 4)自 081 起
  存在,081 轮 api-check 的 pyright 步骤实际为红,当时只 tail 到版本警告行误判为过
  (门禁误报,登记)。修法均取仓库既有范式:测试文件头 reportPrivateUsage=false、
  可重绑全局改小写名(guard.py 惯例)、json 解析段显式 cast 定型。
- **全量**:1049 passed / 0 failed(coverage 72.64%)。
- **契约**:make contracts-check 零漂移(openapi.json 不动)。
- **3.3 归档前置**:delta 经 REMOVED(旧三闸版整条)+ ADDED(新两条件版「部署级注册与
  多租户共享面」)表达——openspec 1.13 不允许同名 REMOVED+ADDED,亦不允许 MODIFIED
  静默丢 scenario,此为合法重写路径;validate --strict 通过。
- **3.4 评审循环**:见上(测试污染为真缺陷已修;pyright 存量错全清)。
