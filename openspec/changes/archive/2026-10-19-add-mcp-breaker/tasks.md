# Tasks: add-mcp-breaker

## 1. 熔断状态机(纯领域,测试先行)

- [x] 1.1 `kongming/mcp/breaker.py`:`McpBreaker` 纯类(CLOSED/OPEN/
      HALF_OPEN 三态;allow/record_success/record_failure/reset 同步方法;
      时钟构造注入;OPEN/HALF_OPEN 失败时 log.warning,恢复 info)——
      先写失败测试(计数达阈值转 OPEN、冷却期内拒、期满半开放行单个、
      半开成功复位/失败回 OPEN、成功清零、确定性语义单测)
- [x] 1.2 计入分类常量与 `unavailable` 错误构造(ports.McpCallError
      kind 扩展注释;不新建异常类型)

## 2. 端口装饰器与装配

- [x] 2.1 `BreakerMcpCaller`(包 McpCaller 端口;call/read_resource 拦截:
      allow 拒→`McpCallError("unavailable", …)` 不出站;成功 record_success;
      McpCallError kind∈{timeout,conn} record_failure,其余不计;
      probe/get_prompt 直通)——先写失败测试(stub caller 断言不出站/
      分类计数边界/成功复位)
- [x] 2.2 `build_mcp_caller(settings)` 单点工厂(enabled=False 返回裸
      caller=逐字旧行为);`get_mcp_breaker()` 模块级惰性单例(形态同
      get_cipher 先例)
- [x] 2.3 四组合根装配点替换:mcp/router.py、bot/router.py、
      chat/router.py、toolcall/router.py;McpService 构造器可选 breaker
      注入(缺省 None),`sync` 探测成功后 `reset`

## 3. 配置与文档

- [x] 3.1 config.py 三配置项(mcp_breaker_enabled 缺省 True /
      mcp_breaker_threshold 缺省 3 / mcp_breaker_cooldown_sec 缺省 60,
      ge/le 边界);.env.example 注释段
- [x] 3.2 tests/test_mcp.py 既有 40 例零回归确认(装饰器包装后 stub
      路径行为不变)

## 4. 验收

- [x] 4.1 `apps/api` ruff+mypy+pytest 全绿(含新增熔断用例)
- [x] 4.2 spec scenarios ↔ 测试对照表自查(十 Scenario 全覆盖)
