# Tasks: add-voice-call-guards

> 滥用闸与租户校验属安全规则类:先写失败用例再实现(宪法 Article III)。
> 本变更不改 schema、不改 OpenAPI 字段;新增 429 只走运行时错误体,
> `make contracts` 应零差异。
>
> apply 记录(2026-11-25):
> - 定向:api voice/state/cancel 84 passed;web voice/api/ui 58 passed;
>   `pnpm typecheck`、`pnpm lint` 绿。
> - 全量:`make api-check` 1920 passed/10 skipped,覆盖率 78.18%;
>   `make web-check` 全链路绿(typecheck/lint/madge/stylelint/format/test:cov/
>   build/size/e2e 2 passed)。
> - 契约:`make contracts` 重生成前后 openapi/types/sse 校验文件 sha 相同,
>   工作区契约文件零差异。
> - 先行证据:新用例与实现同批落地,未单独保留先红记录(与
>   add-voice-intent-guard 的 apply 记录同口径)。
> - 7.2 真机冒烟阻塞:需真实麦克风/扬声器与真实模型账号,本轮无浏览器/设备
>   验收条件;429/403/1009/租户与子协议已由 API 集成测试覆盖。
> - 复审修正(2026-11-25):预算释放到 0 时删除计数键,24 小时窗口从首个
>   成功建联起算;新增 `test_guard_release_to_zero_resets_window`。

## 0. 实施前复核

- [x] 0.1 复核百炼文档的四个数字:会话 120 分钟上限、RPM 60 / TPM 200 万、
  音频价格与 Token 公式、临时 Key 权限说明;有变化先更新本变更的默认值与
  design 再写代码

## 1. 服务端滥用闸(测试先行)

- [x] 1.1 先写失败测试:①同一用户 60 秒内第 N+1 次建联被拒,且不占用准入、
      不调用牵线;②用户 24 小时预算用尽被拒;③部署预算用尽被拒且回滚用户额度;
      ④上游失败/票据失败释放已占额度;⑤配置 0 时该维不启用;⑥Redis 异常时
      频次与预算 fail-open(准入仍 fail-closed);⑦拒绝路径不产生重复释放—先确认红
- [x] 1.2 实现 voice 端口与 Redis 适配:`CallGuard` Protocol;固定窗口频次、
      滚动 24 小时用户/部署预算(原子占用 + 释放,TTL 60 秒/86400 秒);
      `VoiceSettingsView` 增加三个限额字段—验证 1.1 全绿,既有准入/票据用例零回归
- [x] 1.3 接进 `VoiceService.establish`:频次→用户预算→部署预算→准入→牵线→
      票据的顺序与逆序回滚;新增 `RateLimited`/`DailyBudgetExceeded` 错误—
      验证失败路径不向上游发起交换,成功路径保留额度
- [x] 1.4 路由与组合根:429 detail(`VOICE_RATE_LIMITED`/`VOICE_DAILY_LIMIT`/
      `VOICE_BUSY`);`chat/dependencies.py` 装配 Redis 实现;日预算拒绝落
      `chat.voice.call_rejected` 审计—验证 HTTP 集成用例覆盖三种 429
- [x] 1.5 配置:`config.py` 新增 `voice_establish_rate_per_minute`(默认 6,ge=0)、
      `voice_daily_calls_per_user`(默认 20,ge=0)、
      `voice_daily_calls_per_deployment`(默认 100,ge=0)

## 2. 通话租户归属(测试先行)

- [x] 2.1 先写失败测试:①声明未绑定租户返回 403 `TENANT_FORBIDDEN`;
      ②声明已绑定租户时审计记录该 key;③未声明时取默认租户并记录—先确认红
- [x] 2.2 route 在读完 Offer 后用独立短会话走 `kongming.tenant.resolve_tenant`
      (不持有请求作用域会话跨牵线);审计使用校验后的 key—验证既有 establish
      用例按新签名更新后全绿

## 3. 票据移出 URL

- [x] 3.1 后端:优先从 `Sec-WebSocket-Protocol` 取票据并在 accept 时回选;
      无该头回退 query(兼容一版);缺失/复用/他人票据的拒绝语义不变—
      验证 TestClient 断言 `accepted_subprotocol`,回退路径仍通过
- [x] 3.2 前端:`channelUrl` 不再拼票据;`openVoiceChannel` 用
      `new WebSocket(url, [ticket])`—验证构造参数用例与既有 URL 用例

## 4. 前端错误语义

- [x] 4.1 先写失败测试:`VOICE_RATE_LIMITED`→`rate_limited`、
      `VOICE_DAILY_LIMIT`→`daily_limit`、`VOICE_BUSY` 仍归 `busy`—先确认红
- [x] 4.2 实现 `FailureReason` 与 `classifyEstablishFailure`,`voice-call-bar`
      增两条文案—验证 typecheck/lint/vitest 绿

## 5. WebSocket 帧上限

- [x] 5.1 应用层在通道收到单帧后按 UTF-8 字节数限 64KB,超限 `close(1009)`
      并正常收口;写超限用例与既有消息用例—先确认红后实现
- [x] 5.2 api 镜像 uvicorn 加 `--ws-max-size 262144`,与 5.1 的应用层上限同向

## 6. 决策与文档

- [x] 6.1a 提案阶段先落事实修正:ADR-013 加日期化补注(「封顶」只对诚实客户端
      成立、120 分钟上游硬上限、0.003 元不能外推);tech-stack 把「长驻
      WebSocket 会话」改为「WebRTC 媒体 + WS 文字控制」并注明本地时长不强制
      供应商断连;HANDOFF 补 120 分钟边界、审计口径与告警建议,更正「票据是
      进程内单例」为「票据/准入在 Redis」
- [x] 6.1b apply 时新增 `docs/adr/014-voice-call-abuse-guards.md`:上游事实、
      预算闸取舍、不选方案、重开条件;记录多厂商传输差异(Gemini Live 为
      WSS + ephemeral token,无原生 WebRTC 直连)与「供应商接入闸」四项检查
       (浏览器传输/凭证模式/事件协议/会话配置);记录媒体中转容量口径
      (Opus vs 裸 PCM,当前并发上限下的量级,独立媒体服务原则)与「本阶段
      不引入中转」的决策及四条重开条件;把 ADR-013 的补注从指向本 change
      改为指向 ADR-014
- [x] 6.2 apply 时补实现相关文档:`docs/tech-stack.md` 语音上限表补三个新变量
      与 24 小时窗口语义;`docs/HANDOFF.md` 补预算/频次的实际配置与行为;
      `.env.example` 补三个注释变量

## 7. 收口

- [x] 7.1 定向测试 + `make check` 对应子集绿;`make contracts` 零差异并记录
- [ ] 7.2 手工冒烟:①把频次配 1,快速重拨得到 429 文案;②把用户预算配 1,
      建联并挂断后再拨得到预算 429;③传未绑定 `X-Tenant-Id` 得到 403;
      ④访问日志路径不含 ticket;⑤正常通话全链路不变;⑥(观察项,不作验收)
      通话中从 DevTools 关闭文字通道、保持 PeerConnection,确认模型仍可应答,
      记录收口边界的实际表现
      **阻塞**:需真实麦克风/扬声器与真实模型账号;本轮无浏览器/设备验收条件。
      **2026-11-26 自动化子集**:①频次 429 `VOICE_RATE_LIMITED`、②用户预算 429
      `VOICE_DAILY_LIMIT`、③未绑租户 403 `TENANT_FORBIDDEN`、④真实票据 WS 的
      访问日志不含 ticket,均 PASS(临时 API + 本地 SDP stub,不重启评测栈);
      ⑤真实通话全链路与⑥通道断开观察仍待真机。
- [x] 7.3 spec 增量 sync 进主 specs;`openspec validate --all --strict` 绿;
      ADR 已更新;归档记录写清未完成项(如有)
