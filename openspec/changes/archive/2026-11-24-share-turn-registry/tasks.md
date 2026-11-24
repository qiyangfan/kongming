# Tasks: share-turn-registry

> 顺序即依赖:规格增量 → 端口与存储(测试先行)→ 内部面 → 取消编排 → 组合根与
> 配置 → 契约 → 中继内存卫生 → 收口。归属校验跨实例属安全面,先写失败测试。

## 1. 规格增量

- [x] 1.1 query-streaming「取消端点与归属校验」MODIFIED:补跨实例生效与降级
      路径(登记不可用/转发失败/陈旧键按落库结果回答,不伪造终态),新增场景
      「取消请求落在非持有轮次的实例」「共享登记不可用或持有实例不可达」「陈旧
      登记不自我转发」;既有四个场景名逐字保留——apply 记录:2026-11-24 写完,
      `openspec validate --strict` 通过

- [x] 1.2 规格增量 sync 进主 specs(query-streaming「取消端点与归属校验」追加
      跨实例段与三个场景,既有场景名逐字保留)——apply 记录:2026-11-24 完成,
      `openspec validate --all --strict` 57 项通过

## 2. 端口与 Redis 实现(测试先行)

- [x] 2.1 先写失败测试(单测,假 Redis):claim 登记后 locate 得到归属与地址;
      release 按凭据注销;凭据不匹配(同名轮顶替)不删;归属不符抛 TurnNotFound;
      定位到本实例地址不发起转发(陈旧键);Redis 异常一律 fail-open(返回 None
      或照常登记失败)——apply 记录:2026-11-24 先红(模块不存在/语义不符)后绿
- [x] 2.2 先写失败测试(单测,假转发客户端):200→原样回传 outcome/message_id;
      持有侧 404/500、传输失败→None(落库兜底);转发携带内部令牌、trace_id 与
      user_id,基址取定位结果——apply 记录:2026-11-24 先红后绿
- [x] 2.3 实现:`chat/ports.py` 增 `TurnFederation` 端口(claim/release/locate/
      cancel_remote)与 `TurnLocation`;`chat/turn_federation.py` 实现
      `RedisTurnFederation`(键 `chat:turn:{trace}`、JSON 值含归属/地址/凭据、
      Lua 按 nonce 匹配注销、TTL 走配置、内部面转发带超时)——apply 记录:
      2026-11-24 实现完,2.1/2.2 全绿;ruff/mypy/pyright/pylint/lint-imports 过

## 3. 内部面端点(持有侧)

- [x] 3.1 先写失败测试(集成):`POST /internal/chat/turn-cancel` 无令牌/错令牌
      403;携令牌对本实例在途轮→200 且该轮收敛为已取消(复用既有编排)——apply
      记录:2026-11-24 先红(路由不存在)后绿
- [x] 3.2 实现:chat 域 `internal_router`(prefix `/internal/chat`,tag
      `internal`)挂 `POST /turn-cancel`,body 携 trace_id 与归属用户,直接调
      `svc.cancel_turn`;main.py 挂载——apply 记录:2026-11-24 实现完,3.1 绿

## 4. 取消端点跨实例编排

- [x] 4.1 先写失败测试(集成,两实例模拟):A 持轮在途,B 收到取消→经共享登记
      转发到 A→CANCELLED 且历史恰一条已取消行;他人 trace→404 同形且不影响
      该轮——apply 记录:2026-11-24 先红(转发链路不存在)后绿
- [x] 4.2 实现:`ChatService.cancel_turn` 改三步——本地登记命中走既有
      `TurnCanceller`(快路径);未命中且接有跨实例协作→`cancel_remote`;
      仍无结果→落库兜底。`TurnNotFound` 由既有端点映射 404——apply 记录:
      2026-11-24 实现完,4.1 绿且既有取消用例零回归

## 5. 组合根与配置

- [x] 5.1 消费任务接入:`AgentStreamConsumer` 构造收 `federation`,`consume` 在
      本地登记之后 `claim`、finally 在本地注销之后 `release`(登记失败不阻断
      该轮)——apply 记录:2026-11-24 完成
- [x] 5.2 配置:`KM_API_SELF_BASE_URL`(缺省按主机名推导,注释写明多实例须
      唯一可达)、`KM_TURN_REGISTRY_TTL_SEC`(缺省 900,注释写明与
      `KM_AGENT_TIMEOUT_SEC` 的不变量);`.env.example` 同步登记——apply 记录:
      2026-11-24 完成,`test_env_example_sync` 绿
- [x] 5.3 组合根绑定 `_FEDERATION` 单例并传入 `get_chat_service`(与 `_TURNS`
      同范式)——apply 记录:2026-11-24 完成

## 6. 契约

- [x] 6.1 `make contracts` 重生产物(新内部端点入约);验证「重复生成零差异」
      ——apply 记录:2026-11-24 完成,`/internal/chat/turn-cancel` 已入约

## 7. 中继内存卫生

- [x] 7.1 先写失败测试:`_relay_gen` 异常退出(取消)后,队列被就地排空(终止
      标记到达即止),消费任务照常落库——apply 记录:2026-11-24 先红(退出即
      不再消费,队列残留)后绿
- [x] 7.2 实现:`_relay_gen` 区分正常收束与异常退出;异常退出即弃后续帧
      (不向客户端发送、不在内存堆队)——apply 记录:2026-11-24 完成,既有流式
      用例零回归

## 8. 收口

- [x] 8.1 手工冒烟:双 api 实例(同 DB/Redis、同内部令牌、同 JWT PEM 对、同一
      agent,不同端口与不同 `KM_API_SELF_BASE_URL`)——apply 记录:2026-11-24
      实测,A(8020)起真实模型流、B(8022)取消→`{"outcome":"CANCELLED",
      "message_id":...}`,agent 日志「在途问答已中止 count=1」,A 的流收到
      CANCELLED 终态帧,历史恰一条已取消行;杀掉 A 后 B 取消其陈旧 trace→
      0.02s 返回 404(不环、不挂),Redis 键随轮次释放
- [x] 8.2 `make check` 全量子集绿并在 apply 记录——apply 记录:2026-11-24,
      api-check 1797 passed/10 skipped、web 192 tests + 2 e2e、dead-code/
      dup-check/deps-audit/license-check/lock-check/compose-check/security-cov/
      secret-scan/spec-check(57/57)/lint-config 全绿;契约以「重复生成零差异」
      为准(未提交树上 contracts-check 必红属机械红)
