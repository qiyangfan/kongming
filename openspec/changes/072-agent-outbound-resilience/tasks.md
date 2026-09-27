# 072-agent-outbound-resilience 实施任务

## 1. 测试先行:错误分类与重试语义(宪法 Article III)

- [ ] 1.1 失败测试:瞬时故障(连接建立失败 / 读超时 / HTTP 429)触发重试,
      断言调用次数与退避间隔(用假时钟或注入 sleep 记录,不真等);先跑红
- [ ] 1.2 失败测试:`Retry-After` 大于退避上限时按上限等待,
      断言不穿透 `agent_timeout_sec`;先跑红
- [ ] 1.3 失败测试:非瞬时故障(HTTP 401/400/422)与配置类错误
      (缺 api key、模型名不存在、协议错)**只调一次**;先跑红
- [ ] 1.4 失败测试:任务路由目标瞬时故障耗尽后降级主模型,
      且用量记录的实际模型名是主模型;先跑红
- [ ] 1.5 失败测试:BOT `model_override` 目标瞬时故障耗尽后**不**降级
      (直接 LlmError);先跑红
- [ ] 1.6 失败测试:每次尝试各落一条用量(3 次尝试 = 3 条记录,
      status/latency_ms/seq 正确),不合并;先跑红
- [ ] 1.7 失败测试:总尝试硬上限(任何失败序列下不得出现第 4 次调用);先跑红
- [ ] 1.8 失败测试:回调分层——`fetch_schema`/`rag_retrieve`/
      `attachment_content` 连接层失败重试;`sql_execute` 读超时**不**重试
      (design D2);`report_usage`/`tool_execute` 任何失败都不重试;先跑红

## 2. 实现(agent 面;ADR-002 零凭证纪律不变)

- [ ] 2.1 新增重试原语模块(纯 asyncio;错误分类纯函数 + 尝试循环 + 退避),
      不引第三方重试库(design D6)
      **验证**:1.1、1.2、1.3、1.7 转绿
- [ ] 2.2 `llm.py` 四个出站点(`chat`/`chat_vision`/`chat_with_tools`/`embed`)
      统一接入同一原语,一处分类四处复用;协议翻译(`llm_protocol.py`)
      与 `AnthropicClient` 零改动(重试包在 `client.create` 之外)
      **验证**:1.4、1.5、1.6 转绿;`test_multi_protocol_llm` 既有用例零回归
- [ ] 2.3 降级链实现:`_resolve` 之后包一层 attempt loop;降级条目在启动期
      校验(在注册表内 + protocol 合法 + 主机在白名单),非法则告警并视为
      未配置(不拒启)
      **验证**:1.4 转绿;启动自检用例覆盖「fallback 条目不在注册表」
- [ ] 2.4 `settings.py` 增 4 个可配项:`llm_max_attempts`(缺省 3)、
      `llm_retry_base_ms`(缺省 500)、`llm_retry_max_wait_ms`(缺省 5000)、
      `llm_fallback_entry`(缺省空=关闭);同步登记 `.env.example`
      **验证**:缺省值下行为即本变更新契约;显式调参后行为随动
- [ ] 2.5 `apiclient.py` 按 1.8 的分层接入(用量上报与工具执行不包重试)
      **验证**:1.8 转绿;`test_apiclient*` 既有用例零回归
- [ ] 2.6 import-linter 契约复核:`apps/agent` 仍零 asyncpg/sqlalchemy/redis
      import(重试原语是纯 asyncio)
      **验证**:`lint-imports` 绿;mypy strict + pyright strict 收敛

## 3. 与在流变更的口径核对

- [ ] 3.1 核对 062(chat-cost-control)按次配额语义:确认其「已用量 =
      `sys_llm_usage` 行数」口径与 D5「失败尝试计入」一致,
      若 062 已先行落地,在其 spec 补一句口径说明而不改代码
      **验证**:063 之前的配额计数用例零回归;口径写入本变更 spec delta
- [ ] 3.2 核对 065(eval-ci-gate)红线:确认 nightly 评估期间的重试不会
      被误读为「两次调用两次计费」;eval 走真实链路,重试只增成功率先例
- [ ] 3.3 **核对 069-chat-quality-observability 的用量聚合口径**:其
      design 明文质量汇总的「平均 token」走
      `chat_message ⋈ sys_llm_usage on trace_id`(design.md:36-37)。
      本变更使同一 trace_id 下可能出现 ERROR 行(tokens=0)+ SUCCESS 行,
      **若按行取平均会被 0 值稀释**。正确口径为按 trace_id 分组取
      `SUM(total_tokens)` 作为该轮 token 总量(尝试次数另计)。
      若 069 已先行落地为按行平均,提交一个口径修正而非让两个变更
      各自正确、合起来错误
      **验证**:构造「1 次 ERROR + 1 次 SUCCESS」两行的 trace,
      断言聚合得到的轮次 token 等于 SUCCESS 行实测值、调用数为 2

## 4. 收口

- [ ] 4.1 spec delta sync 进 `openspec/specs/multi-model-routing/`
- [ ] 4.2 手工冒烟:以本地 mock 端点模拟 429(带 `Retry-After`)与 503,
      确认问答成功且 `sys_llm_usage` 含失败尝试行;再模拟主模型不可用 +
      配置 fallback 条目,确认降级生效且用量记录实际模型;记录于 apply
- [ ] 4.3 全量 `make check` 收口
