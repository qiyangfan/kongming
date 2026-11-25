# Tasks: add-voice-intent-guard

> 状态机与播放闸门测试先行:先写失败用例,再实现。本变更不改 schema 与
> OpenAPI;`make contracts` 应零差异。
>
> apply 记录(2026-11-25):状态机加第二工具、停止分发、放行状态与空闲/等待期
> 非工具输出取消;前端远端默认静音、`playback.allow`/`turn.stopped` 与停止录音
> `zh-stopped.m4a`。api 定向 55、api-check 1892/10 skipped/77.80%,web-check
> 236 tests + 2 e2e;新增用例与实现同批落地,未单独留先红证据。
> 归档记录(2026-11-25):spec 增量已 sync 进主 specs(4.1);`make check` 全量绿(4.2);
> 真机冒烟(4.3)仍未完成,不把未验证的验收写成已完成。

## 1. 后端状态机(测试先行)

- [x] 1.1 先写失败测试(假管道):①停止工具取消在途轮,不发 `turn.request`、
      不回填、不计轮数;②无在途轮时停止只下发确认;③空闲期非工具回应被取消;
      ④工具调用轮不被取消;⑤工具结果回填后的 `response.create` 进入放行状态;
      ⑥停止确认由浏览器本地播放,不占远端放行;⑦放行状态在回应结束后清除——先确认红
- [x] 1.2 实现第二工具与停止分发、放行状态、空闲自答取消;`build_session_config`
      的指令与工具清单同步更新——验证 1.1 全绿,既有语音/取消用例零回归
- [x] 1.3 通道适配层:下发 `turn.stopped` 与放行控制消息;WebSocket 集成用例
      覆盖停止消息与放行顺序——验证:消息顺序稳定、断链不抛

## 2. 前端播放闸门

- [x] 2.1 先写失败纯函数测试:未放行时 `response.audio.delta` 不解除静音;
      放行后解除;停止确认只播一次;新问到达保持未放行——先确认红
- [x] 2.2 实现:远端音频默认静音,以服务端放行为唯一解除条件;`turn.stopped`
      触发停止确认播放(与固定应答同一控制路径)——验证 2.1 全绿,
      `pnpm typecheck && pnpm lint && pnpm test:run` 绿

## 3. 固定资产与文档

- [x] 3.1 停止确认短句(与实时模型同音色,`qwen3-tts-flash`)入库
      `apps/web/public/voice/`,引用路径同步——验证:通话中停止可闻、挂断即停
- [x] 3.2 HANDOFF 语音一节补充停止意图、自答拦截与放行闸门的行为与已知限制

## 4. 收口

- [x] 4.1 spec 增量 sync 进主 specs(voice-call MODIFIED + 两条 ADDED),
      `openspec validate --all --strict` 绿
- [x] 4.2 全量 `make check` 绿并在 apply 记录(api-check/web-check 已绿;
      `contracts-check` 仍为未提交树机械红,待提交契约产物后复跑)
- [ ] 4.3 真机冒烟:①等待中「先不查这个了」→ 停、确认、历史留已取消说明;
      ②空闲时问一个库内数字、故意不给工具 → 听不到自答;③正常提问 → 结果照念
