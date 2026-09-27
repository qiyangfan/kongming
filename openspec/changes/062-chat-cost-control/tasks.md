# 062-chat-cost-control 实施任务

## 1. 测试先行:速率与配额闸(宪法 Article II/III;安全/成本规则先红后绿)

- [ ] 1.1 失败测试:`/chat/query` 达分钟限额后返回 429 `CHAT_RATE_LIMITED`,
      且 mock agent 客户端零调用(证明未烧 token);先跑红
- [ ] 1.2 失败测试:`/chat/query/stream` 同款闸;先跑红
- [ ] 1.3 失败测试:公开嵌入端点在 (bot_key, ip) 维度超限返回 429,
      且同一 IP 换 bot_key 不受限、同 bot_key 换 IP 不受限;先跑红
- [ ] 1.4 失败测试:限流存储注入异常时放行(速率闸 fail-open);
      配额读数注入异常时拒绝并落审计(配额闸 fail-closed);先跑红
- [ ] 1.5 前置核对:`sys_llm_usage` 是否已有 `(tenant_id, created_at)`
      类索引;若无,先补迁移再写配额计数查询(ADR-004/006 一次建全)

## 2. 登录用户问答面接线

- [ ] 2.1 `toolcall/ratelimit.py` 的维度键泛化(不变现有工具限流键),
      供 chat 面复用同一实现
      **验证**:既有 `test_toolcall.py` 限流用例零回归
- [ ] 2.2 `chat/router.py` 两个查询端点前置速率闸 + 配额闸,超额走统一
      429 响应(`CHAT_RATE_LIMITED` / `CHAT_QUOTA_EXCEEDED`)
      **验证**:1.1、1.2、1.4 转绿
- [ ] 2.3 配额"已用量"从既有 `sys_llm_usage` 按窗口 count 派生,不新增
      用量表(避免与 055 对账重复建账)
      **验证**:单测覆盖窗口边界(日/月切换)与空窗口

## 3. 公开嵌入面接线

- [ ] 3.1 `bot/router.py` 公开端点加 (bot_key, 可信客户端 IP) 限流;
      IP 取自 `ClientIpMiddleware` contextvar,禁止读请求自带头
      **验证**:1.3 转绿;断言伪造 `X-Forwarded-For` 不改变限流键
- [ ] 3.2 `Settings.chat_result_max_rows`(缺省 1000)取代
      `chat/router.py:151` 的 inline `limit=1000`
      **验证**:grep 无 inline 魔数;既有执行用例零回归

## 4. 契约与前端

- [ ] 4.1 `make contracts` 重新生成并提交产物(ADR-005);CI 漂移门禁本地预演
      **验证**:`git diff contracts/openapi.json` 无意外差异
- [ ] 4.2 前端 `ERROR_CODE_LABELS` 补 `CHAT_RATE_LIMITED` /
      `CHAT_QUOTA_EXCEEDED` 两条中文文案(走既有错误转译单源,页面零分支)
      **验证**:`pnpm typecheck && pnpm lint` 绿;429 冒烟文案可读

## 5. 收口

- [ ] 5.1 spec delta sync 进 `openspec/specs/developer-portal-billing/`
- [ ] 5.2 手工冒烟:配置低限额 → 连续提问至 429 → 确认 agent 无新增
      `sys_llm_usage` 行(零 token 消耗);公开嵌入页同款验证;记录于 apply
- [ ] 5.3 全量 `make check` 收口

> 062 落地记录:
