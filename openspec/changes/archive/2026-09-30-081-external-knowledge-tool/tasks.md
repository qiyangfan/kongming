# 081-external-knowledge-tool 实施任务

## 1. 测试先行(宪法 Article III;信任边界与外部出站类先红)

- [x] 1.1 失败测试:未配置 `KM_SIKU_BASE_URL` → `build_registry` 不含 `knowledge_search`,注入集无该工具
- [x] 1.2 失败测试:`KM_SIKU_BASE_URL` 主机非白名单(远端域名/空主机)→ 拒配不注册,告警记录;服务其余部分正常
- [x] 1.3 失败测试:多租户(seed 两个租户)+ 合法端点 → 工具不注册(单租户闸)
- [x] 1.4 失败测试:mock siku `/api/search` 正常返回 → 工具观察含 chunk 文本与来源锚点(note_path/source_name/anchor);工具审计落一行;无 `sys_llm_usage` 新行
- [x] 1.5 失败测试:siku 拒连 → unreachable 观察(连接层重试恰好一次),agent 不崩不编造;响应 500 → protocol 观察且只调一次
- [x] 1.6 失败测试:调用方授权集不含 `tool:knowledge:search` → 注入集不可见;直接回调被拒并落越权审计

## 2. 实现(全部在 apps/api;ADR-002 信任边界不变)

- [x] 2.1 新增 `kongming/toolcall/knowledge.py`:领域 Protocol(`KnowledgeSearchPort`)+ 结果值对象 + httpx 实现(localhost 调用、`KM_SIKU_TIMEOUT_MS` 超时、连接层重试复用 072 只读分层原语)
      **验证**:1.4、1.5 转绿
- [x] 2.2 `kongming/settings.py`:新增 `KM_SIKU_BASE_URL` / `KM_SIKU_TIMEOUT_MS` + 主机校验纯函数(白名单:127.0.0.1 / localhost / host.docker.internal)
      **验证**:1.2 转绿
- [x] 2.3 `kongming/toolcall/registry.py`:`REGISTRY` 保持静态纯数据,新增 `build_registry(settings)` 装配函数 + `knowledge_search` 的 ToolDef(权限点 `tool:knowledge:search`,write=False);启动接入替换直接引用 `REGISTRY` 的点
      **验证**:1.1、1.3、1.6 转绿;既有工具注入测试零回归
- [x] 2.4 `kongming/toolcall/service.py`:分发新增 `knowledge_search` 分支,经领域端口执行;审计沿用既有工具调用落库;错误映射进既有分发兜底(不裸 500)
      **验证**:1.4、1.5、1.6 全绿
- [x] 2.5 单租户闸接线:启动探测租户数 > 1 → 不注册 + 告警(design D4)
      **验证**:1.3 转绿

## 3. 收尾

- [x] 3.1 `make check` 全量子命令绿(api 侧 ruff+mypy+pytest;agent 侧测试零改动零回归)
- [x] 3.2 手工冒烟:本机 `siku serve`(默认 127.0.0.1)+ kongming api 单租户 + `.env` 配 `KM_SIKU_BASE_URL` → ReAct 提问文档口径类问题 → LLM 选择 `knowledge_search` → 观察喂回 → 答案带来源锚点;原始输出贴 HANDOFF/apply 记录
- [x] 3.3 契约核对:`contracts/openapi.json` 无漂移(预期无变更,核对结果记录);`make contracts` 无需执行
- [x] 3.4 归档前置:specs delta 已按 081 写法就位(本目录 specs/),apply 完成后走 archive 流程

## 实施证据(apply 记录,2026-09-30)

- **3.1 门禁**:`make check` 全量子命令绿——api-check:pytest 1049 passed/0 failed
  (含本变更 20 用例)+ coverage 72.84% ≥ 70%;ruff/mypy/pyright/lint-imports 全过;
  agent-check 绿(agent 面零改动零回归);contracts-check 绿(openapi.json 零漂移,
  工具集是运行期注入数据、非静态契约面);web-check 绿(前端零改动)。
  种子测试两处计数随新权限点 31→32/40→41 同步(预期内变更)。
- **3.2 手工冒烟**(全真链,siku 经 muniu 向量检索):siku ingest+index(1 chunk,
  embedding 走 muniu :8787 text-embedding-v3,mk- key)→ `siku serve` :15200 →
  kongming api :8002(scratch 库 kongming_smoke 单租户,KM_SIKU_BASE_URL 指向 15200;
  启动日志无闸门告警=三道闸放行;对照:dev 库 3 租户时 api :8000 正确告警拒注册)→
  agent :8001(经 muniu qwen-plus)→ admin 登录 → `POST /chat/query
  agent_mode=react`「退款时效几天,以知识库文档口径为准」→ verdict PASS,答案
  "11 天"并引用《refund-policy.md》(11 天为冒烟文档写入值,证明取自真实语料);
  sys_tool_audit 落 EXECUTED 193ms;sys_llm_usage 的 7 行均为 ReAct 自身 LLM
  调用,知识检索零用量(契约)。
- **3.3 契约核对**:export_openapi 重生成后 `git diff --exit-code` 空——无漂移。
- **3.4 归档前置**:specs delta 四件套就位(external-knowledge ADDED + tool-calling
  MODIFIED 含全部既有 4 scenario),`openspec validate --strict` 通过。
- **与提案的偏差登记**(tasks.md 早前写的落点按实际代码修正):①配置模块实为
  `kongming/config.py`(tasks 写的 settings.py 不存在);②design D6「复用 072 重试
  原语」不可行——072 原语在 apps/agent 进程,kongming 双进程禁跨进程 import,api 侧
  按同语义本地实现(连接层一次重试);③冒烟实证修正异常分类:拒连在 macOS anyio
  后端以 httpx.ReadError 浮现(非 ConnectError),连接层族按语义归纳为
  NetworkError 家族 + RemoteProtocolError + TransportError 兜底。
