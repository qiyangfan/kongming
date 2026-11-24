# Design: share-turn-registry

## Context

动机见 proposal.md「Why」。与本设计直接相关的现状:

- **三跳中继**:浏览器 ←(SSE)← 控制面 ←(SSE)← Agent 面。控制面为每轮起一个
  后台消费任务(断线续存:前端断开不影响该任务),终态帧在 assistant 消息落库
  之后才入队;中继生成器只把队列帧转给浏览器,被取消即退出。
- **在途轮登记是进程内单例**(`chat/dependencies.py` 的 `_TURNS`):取消端点
  先查本地表,未命中再查落库结果。本地表按「用户 + trace_id」隔离,他人同
  标识查不到(归属校验的第一道)。
- **取消编排**(`chat/inflight.py` `TurnCanceller`):置取消标记 → 通知 Agent 面
  (3s 上限)→ 等本轮收敛(5s)→ 超时硬取消本地任务再等(5s)。
- **内部面**(ADR-002):`/internal/*` 由 middleware 以 `X-Internal-Token` 保护,
  按域分 router(如 `kongming/internal/router.py`),全量进 OpenAPI 契约。
- **Redis 既有用途与惯例**:auth 票据家族(`auth:rt:*`)、限流(`km:toolrl:*`)、
  成本闸;结构化值用 JSON,多步原子操作用 Lua;限流器 Redis 不可用时 fail-open
  并记日志。
- **约束**:领域代码零框架/Redis 依赖(宪法 Article II,外部能力经 Protocol 反转);
  契约单一来源(ADR-005);单轮 agent 流上限 = `KM_AGENT_TIMEOUT_SEC`(缺省 120s,
  最坏约 5 个顺序阶段)。

## Goals / Non-Goals

**Goals:**

- 取消请求落到任一 api 实例都生效:经共享登记把取消转给持有该轮的实例,由它
  跑既有编排——编排代码零重复。
- 归属校验不放松:他人轮次在任何路径下都 404 同形。
- 降级可预期:登记不可用/转发失败/陈旧键,一律按落库结果回答,不伪造终态。
- 前端断开后,中继侧不再为无人接收的帧堆内存。

**Non-Goals:** 见 proposal.md(不做 Agent 面跨实例中止、不给 agent 队列设上限、
不建注册体系、不改对外契约)。

## Design

### 一、端口与实现(领域只认 Protocol)

`chat/ports.py` 新增(与 `TurnRegistry` 并列):

```python
class TurnFederation(Protocol):
    """在途轮的跨实例协作:本实例登记持有;取消落在别处时凭登记转发过去。"""

    async def claim(self, *, trace_id: str, user_id: str) -> str | None: ...
    async def release(self, *, trace_id: str, token: str) -> None: ...
    async def cancel_remote(self, *, trace_id: str, user_id: str) -> TurnSettlement | None: ...
```

- `claim`:登记本实例持有该轮,返回注销凭据(nonce);登记不可用返回 `None`
  (该轮退化为仅本实例可见——与今天相同,不更糟)。
- `release`:按凭据注销;凭据不匹配说明同名轮已顶替(客户端自带 trace_id 撞号),
  不删别人的登记。
- `cancel_remote`:定位 → 归属校验 → 转发 → 等结果;返回 `None` 表示「无处可转」
  (不在任何实例/陈旧键/登记不可用/转发失败),由调用方走落库兜底;定位到却属于
  他人时抛 `TurnNotFound`(与不存在同形)。

实现在 `chat/turn_federation.py`(基础设施侧,可 import redis/httpx,不进
import-linter 领域清单,与 `chat/repository.py` 同地位)。

### 二、Redis 键与 TTL

- 键 `chat:turn:{trace_id}`,值 JSON `{"u": 归属用户, "url": 持有实例地址,
  "n": 注销凭据}`,TTL = `KM_TURN_REGISTRY_TTL_SEC`(缺省 900s)。
- TTL 口径:须大于「单轮 agent 流上限 + 收敛余量」。`KM_AGENT_TIMEOUT_SEC`
  缺省 120s,最坏约 5 个顺序阶段(understand/generate/≤2 selfheal/summarize)
  加收敛,900s 留足余量;调大 agent 超时须同步调大本项(与
  `agent_task_timeout_sec ≥ 任务墙钟预算` 同族的配置不变量,写在 .env.example)。
- 注销用 Lua 做值匹配 CAS(`GET` 等于凭据才 `DEL`):并发同名轮下,先结束者
  不会删掉后者的登记。

### 三、转发链路

```
用户 → 实例 B(取消端点)
        ├─ 本地登记命中? ──是→ 既有 TurnCanceller 编排(快路径,零新增延迟)
        └─ 否 → Redis 定位 chat:turn:{trace}
                 ├─ 无键/Redis 故障 ─────────────→ 落库兜底(有行 COMPLETED,无行 404)
                 ├─ 归属用户不符 ────────────────→ 404 同形(TurnNotFound)
                 ├─ url == 本实例地址(陈旧键)──→ 落库兜底(不自我转发,防自环)
                 └─ 否则 POST {url}/internal/chat/turn-cancel
                          {trace_id, user_id} + X-Internal-Token
                          ├─ 200 → 原样回传 outcome/message_id
                          └─ 非 200/超时/网络错 → 落库兜底
```

- **持有侧**(实例 A 的新内部端点)直接调 `svc.cancel_turn(user_id, trace_id)`:
  A 的本地登记命中即跑既有编排;若轮次在转发途中刚结束,自然走到落库兜底,
  404/200 由既有逻辑决定——不需要为内部面写第二套编排。
- **自环防护**:定位到本实例却不在途,只可能是陈旧键(实例重启残留)。此时若
  转发给自己,会无限自环;直接走落库兜底。因此自报地址必须逐实例唯一
  (见下)。
- **转发超时**:持有侧最坏耗时 = 通知 3s + 等收敛 5s + 硬取消后再等 5s,加
  余量 → `FORWARD_TIMEOUT_SEC = ABORT_TIMEOUT_SEC + 2 × CANCEL_SETTLE_TIMEOUT_SEC + 3`
  (=16s)。取消端点整体最坏耗时与单实例相同量级。
- **为什么转发而不是发布订阅**:编排代码零重复(持有侧跑既有 `TurnCanceller`);
  不需要每轮看门任务;Redis 只存一个键。代价是实例间需网络可达(compose 同
  网络/k8s 集群内均满足)与一个唯一自报地址。
- **为什么自报地址而不是查注册表**:宪法 Article I 不设注册中心;一个键里
  带上地址,取消方一次 GET 即可路由。

### 四、自报地址与组合根

- 新配置 `KM_API_SELF_BASE_URL`;缺省 `http://{主机名}:8000`(容器内监听端口
  固定 8000)。**多实例部署必须保证该值在同类实例间唯一且可达**:k8s 用
  pod IP/名称,勿用共享服务名——否则自环防护会把别实例的在途轮误判为陈旧键。
- 组合根(`chat/dependencies.py`):`_FEDERATION = RedisTurnFederation(get_redis(),
  self_base_url=...)`,与 `_TURNS` 同范式传入 `get_chat_service`;消费任务
  (`AgentStreamConsumer`)经构造参数拿到,`consume` 里本地登记之后 `claim`、
  `finally` 里本地注销之后 `release`。

### 五、前端断开后的中继内存卫生

- 现状:`_relay_gen` 被取消(前端断开)后,消费任务照常把后续帧堆进无人读取的
  队列,直到该轮结束。单轮帧量有界(过程帧几十条 + 一个结果帧,结果集大时结果
  帧可达数百 KB),但并发遗弃轮次会叠加,且客户端可主动放大(连开多轮后弃连)。
- 改法:`_relay_gen` 区分「正常收束(终止标记已交付)」与「异常退出」。异常
  退出(取消/关闭/发送失败)时,在退出前就地消费队列直到终止标记并丢弃——
  消费任务的入队因此始终被取走,内存不堆队;落库与终态收敛不受影响(消费任务
  独立于中继)。
- 终止性:消费任务的任何退出路径都必达终止标记(`consume` 的 finally 保证),
  故丢弃循环必然结束;消费任务本身挂死时中继挂死与今天相同(既有语义)。
- **Agent 面为什么保持无界**:其队列唯一消费者是控制面消费任务——前端断开
  不杀它(断线续存),它死亡时 agent 的连接即断、图执行被取消(finally 里
  `task.cancel()`),队列随之作废。即 agent 队列要么被持续读走,要么随任务取消
  回收,不会无限增长;设有界反而在图执行快于读取时把 `_run` 堵死(无人取帧的
  情形下永久挂起)。结论:agent 侧不动,控制面中继侧加「退出即弃」。

### 六、测试策略

- 归属校验跨实例属安全面,先写失败测试(宪法 Article III)。
- 单元(假 Redis/MockTransport):claim/release/locate 语义;值匹配 CAS;
  归属不符抛 `TurnNotFound`;url 指向本实例→不走转发;Redis 故障 fail-open;
  转发的 200/404/超时三态。
- 集成(真 Redis + ASGI):两个 `InflightTurns` + 两个 `ChatService` 共享同一
  federation,模拟 A/B 两实例——B 取消 A 的在途轮得 CANCELLED;他人 trace
  404 同形;A 侧轮已结束时 B 走落库兜底得 COMPLETED。
- 中继丢弃:中继异常退出后队列被排空(终止标记到达即止),消费任务照常落库。

### 七、契约与配置产物

- 新内部端点进 OpenAPI(tag `internal`,与既有内部面一致)→ `make contracts`
  重生产物;取消端点对外契约不变。
- `.env.example` 增两个配置(有 `test_env_example_sync` 把守)。

## Risks

- **自报地址配错**(共享服务名/未配):跨实例取消退化为落库兜底(与今天相同),
  不会更糟,但多实例部署验收须显式核对该项。
- **Redis 故障**:取消退化为落库兜底;单实例部署不受影响(本地快路径不依赖
  Redis)。
- **陈旧键窗口**(持有实例硬崩后最长一个 TTL):该窗口内取消走到转发、连不通
  后落库兜底,最坏多等一个转发超时(16s);TTL 到期后恢复快速 404。
