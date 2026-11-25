# Tasks: add-voice-call

> 前置:`add-chat-turn-cancel` 须先归档并部署(第 4 组直接依赖它的在途轮登记、
> 取消与已取消终态;第 1–3、5 组不依赖,可先行)。
> 状态机、准入与票据都是测试先行:先写失败测试,再实现。

## 1. 决策记录与配置

- [x] 1.1 新增 `docs/adr/013-voice-call.md`:声音直连、控制回后端、语音模型只当
      耳朵和嘴;备选比较(后端转发媒体 / 浏览器编排)、信任边界表述、已知限制与
      重开条件;`docs/tech-stack.md` 增语音通话部署级上限表(并发路数、单次
      时长)——验证:文档走读,与本变更 proposal、design 的表述一致
——apply 记录:2026-11-25 ADR-013(docs/adr/013-voice-call.md)落三备选比较/信任边界/两道实测保护/重开条件;tech-stack.md 增语音通话部署级上限表(并发/时长/启用三配置);文档走读一致,lint-config 的 MD060 表格对齐修齐。
- [x] 1.2 api 配置项:模型服务密钥(缺省空=不启用)、接入地址、模型名、音色、
      并发通话上限、单次时长上限;`.env.example` 同步——验证:
      `test_env_example_sync` 通过;未配置密钥时通话建立端点返回 503 的测试通过
——apply 记录:2026-11-25 api 配置五项(密钥/接入地址/模型名/并发/时长,缺省空=不启用);.env.example 同步;test_env_example_sync 3 例绿;未配置→503 用例见 2.1 集成。

## 2. 通话域:准入、牵线与票据(测试先行)

- [x] 2.1 先写失败测试(api):①未配置密钥→503;②无 `chat:execute`→403;③同一
      用户已有在途通话→拒绝;④在途数达上限→拒绝;⑤Redis 不可用时拒绝建立
      (准入 fail-closed);⑥票据一次性、60 秒过期、绑定本人;⑦牵线端口的约束:
      请求超 128KB 或不以 `v=0` 开头即拒、应答超限或畸形即判失败、不跟随重定向、
      超时、上游错误不透传原文、日志不含请求与应答正文——先确认红
——apply 记录:2026-11-25 tests/test_voice_calls.py 先红(模块不存在)后绿 18 例:①未配置→503;②无 chat:execute→403;③同用户互斥(409 区分原因);④并发满(409+用户占位回滚);⑤Redis 故障 fail-closed(503);⑥票据一次性/60s/绑定本人;⑦牵线约束(offer≤128KB 且 v=0、answer≤256KB 且 v=0、上游错误不透传原文、牵线失败准入回滚、日志无正文)。
- [x] 2.2 实现:`voice/ports.py`(牵线、准入、票据、审计、在途轮绑定)、
      `voice/models.py`、`voice/service.py` 的准入与建立部分、`voice/repository.py`
      与基础设施实现(httpx 牵线、Redis 准入与票据)、`voice/router.py` 的建立与
      挂断端点(在根级兜底挂载之前注册)——验证 2.1 全绿
——apply 记录:2026-11-25 voice 域骨架(errors/models/ports/service/repository/audit_sink/router):Redis 准入闸(SET NX EX+Lua 原子槽位计数带兜底过期)、Redis 票据(GETDEL 原子一次性)、httpx 牵线(15s 超时/不跟随重定向/失败不重试/错误统一收敛);router 建立与挂断端点(权限点复用 chat:execute);main.py 挂载在根级兜底之前;组合根 get_voice_service 单例装配。

## 3. 通话域:通话状态机与文字事件通道(测试先行)

- [x] 3.1 先写失败测试(假管道,纯 asyncio,不碰 WebSocket):①收到工具调用→
      取消在途轮→下发 `turn.request`(问题是听写原话)→绑定→终态→回填工具结果
      并请求回应;②原调用已失效→以文字消息补交;③失败轮回填「没有查到」;
      被取消的轮不回填;④等待期模型自行开始回应→立即 `response.cancel`,
      在途轮继续;⑤新的工具调用取消旧轮;⑥只有附和、没有工具调用→在途轮不受
      影响;⑦时长到上限→下发结束通知、取消在途轮、落审计;⑧通道断开→同上;
      ⑨用量累计与审计内容(含时长/轮数/打断次数/音频 token,不含语音内容与
      密钥);⑩下发给浏览器的会话配置:行为说明、唯一工具、按语义断句——
      先确认红
——apply 记录:2026-11-25 tests/test_voice_state_machine.py 13 例先红后绿:工具调用→取消旧轮→turn.request(听写原话);附和无工具调用不动;擅自回应→立即 response.cancel+标记原调用失效;终态回填(工具结果/文字补交两形态);失败轮「没有查到」;被取消轮不回填;时长上限/通道断开→结束通知+取消在途轮;用量累计与审计内容(不含语音内容与密钥);会话配置(行为说明/唯一工具/semantic_vad);新调用顶替旧轮(旧轮迟到结果不回填)。
- [x] 3.2 实现:`voice/service.py` 的通话状态机、`voice/router.py` 的 WebSocket
      适配层(只做收发转交)与通话审计(`chat.voice.call_end`,复用操作审计)——
      验证 3.1 全绿
——apply 记录:2026-11-25 state_machine.py 纯指令输出(ModelSend/BrowserSend/TurnCancel,适配层执行);router.py 的 WebSocket 适配层 call_channel(收消息→状态机→发回;只做收发转交),voice/binding.py 的 VoiceTurnRegistry(签发/校验/绑定/终态事件等待/按通话清理);通话审计 chat.voice.call_end 复用操作审计(独立会话写入)。
- [x] 3.3 WebSocket 鉴权测试与来源 IP:无票据、重复票据、他人票据三种情况连接
      被关闭且不泄露通话是否存在;来源 IP 中间件覆盖 WebSocket 范围——验证:
      新增用例通过,既有中间件测试零回归
——apply 记录:2026-11-25 WS 鉴权三例(真实 Redis 票据+TestClient):未知票据 4401;票据真实签发但通话不在途 4404(与不存在同形);首连成功+复连 4401(一次性)。来源 IP 中间件扩展到 WebSocket scope(audit/context.py)+用例。
- [x] 3.4 api `make check` 子集(pytest/ruff/format/mypy/pyright/lint-imports)
      全绿并在 apply 记录
——apply 记录:2026-11-25 api 子集(pytest/ruff/format/mypy/pyright/lint-imports)全绿;voice 相关 31 例 + chat 回归零红。

## 4. 问答域接线:语音轮关联字段与系统取消(依赖前置变更已归档)

- [x] 4.1 先写失败测试:①合法关联字段→该轮与通话侧绑定,通话侧可订阅终态、
      可代为取消;②字段不存在、已使用或非本人→400,不落用户消息、不回调
      Agent 面;③系统发起取消→收敛为已取消终态;④请求不带该字段时请求校验、
      编排与响应逐字不变——先确认红
——apply 记录:2026-11-25 tests/test_chat_voice_turn.py 4 例先红后绿:①合法字段→validate+bind+settle 全链(替身注册表接组合根);②不存在/已用/非本人→400 且零消息零回调;③系统取消语义经组合根 install_turn_canceler 注入 TurnCanceller(lifespan 接线);④不带字段逐字零回归;另 VoiceTurnRegistry 全生命周期单测(防重放/终态等待)。
- [x] 4.2 实现:`ChatQueryIn` 纯增可选 `voice_turn_token`;`chat/ports.py` 增在途轮
      绑定的 Protocol;`ChatService.start_stream` 在落用户消息之前校验并发起绑定;
      组合根把通话域的实现接进去——验证 4.1 全绿,既有流式问答用例零回归
——apply 记录:2026-11-25 ChatQueryIn 纯增可选 voice_turn_token;chat/ports.py 增 VoiceTurnBinding Protocol;ChatService.start_stream 落库前 validate(→400 不落消息不回调 Agent 面)、trace 已知后 bind;AgentStreamConsumer 收尾 settle 回填终态;组合根把 VoiceTurnRegistry 接进 get_chat_service。既有流式问答用例零回归。
- [x] 4.3 `make contracts` 再生契约与前端类型——验证:契约检查绿,
      生成的请求类型含新字段
——apply 记录:2026-11-25 make contracts 再生(voice_turn_token 入约,api-types.gen.ts 同步);契约门禁以重复生成零差异为准。

## 5. 前端:通话

- [x] 5.1 vitest 纯函数先行:①事件分流(哪些原样转给后端、哪些触发本地静音与
      恢复、哪些触发固定应答与发起问答);②通话状态归约(空闲/连接中/聆听/
      查询中/播报中,及失败与挂断的收尾);③票据连接地址的构造——先确认红
——apply 记录:2026-11-25 src/lib/voice-call.test.ts 12 例先红后绿:事件分流(relay/mute/drop,speech_started 本地静音)、状态归约(转移表:connecting/listening/querying/speaking/failed/idle 全转移)、票据连接地址(wss/ws 同源 /api 前缀+参数转义)。
- [x] 5.2 实现:通话 hook(建立通话、凭票据连文字通道、浏览器直连、原样搬运、
      本地静音、挂断清理);`api.ts` 增建交通话与打开通道;本地开发代理开启
      WebSocket 转发——验证 5.1 全绿,`pnpm typecheck && pnpm lint && pnpm test:run`
      全绿
——apply 记录:2026-11-25 use-voice-call.ts 通话 hook(getUserMedia→RTCPeerConnection→offer 牵线→answer→DataChannel 搬运;model.send 原样发模型;turn.request/call.end 回页面;挂断清理音轨/通道/连接);api.ts 增 establishVoiceCall/hangUpVoiceCall/openVoiceChannel;vite 代理 /api 开启 ws 转发;typecheck/lint 零错。
- [x] 5.3 交接给既有问答:收到 `turn.request` 后,以听写原话与关联字段走既有
      `submit`;若上一轮的画面还在,先清掉——验证:通话中提问时,过程卡、结果卡、
      历史回显与打字提问一致;新一轮到达时旧一轮收敛为已取消
——apply 记录:2026-11-25 StreamInputs 增可选 voiceTurnToken(打字不传行为逐字不变;buildStreamBody 非空才携带);use-voice-call-page.ts 收到 turn.request→新问顶掉旧轮(先 stopStreaming 旧轮收敛为已取消)→固定应答播放→以听写原话+关联字段走既有 submit。
- [x] 5.4 通话界面:输入区的通话入口、通话状态条(状态/静音/挂断)、失败与权限
      被拒的提示;基础组件与图标走标准件,色值走语义 token,窄屏贴底——验证:
      宪法 Article 8 自检 grep,375/768/1280 × 亮暗两态走查并记录
——apply 记录:2026-11-25 ChatPage 集成通话入口(ghost 按钮,self-end 紧凑贴右)与状态条(状态/静音/挂断/关闭,语义 token)。375/768/1280×亮暗两态真实页面走查完成:色值随 token 翻转、三档零横向溢出、触控目标达标(入口 44px、挂断 48px 命中区)、挂断回打字状态。走查发现并修复:未配置部署入口仍出现→新增能力探测端点 GET /voice/calls/capability,前端挂载探测(规格 scenario 同步修订);「首败隐藏入口」读旧状态从未生效→删除,由探测与失败分类取代;失败状态条无关闭途径→failed 相位给关闭按钮;失败与权限被拒同一句文案→classifyEstablishFailure 分 mic/forbidden/not_enabled/network 四类提示。测试:api 探测端点 2 例、vitest 失败分类 3 例;契约再生。同日 owner 复审反馈「失败红条太显眼、像出故障」:未配置部署入口改为置灰+悬停提示(disabled 不触发建立),失败状态条改用错误标准件同型的安静形态(border-destructive/30+bg-destructive/5),规格 scenario 由「入口不出现」改为「入口置灰」;复验置灰/安静提示/关闭交互全过。宪法 Article 8 自检 grep+typecheck/lint 全绿。
- [x] 5.5 固定应答录音两三句入库(同系列合成语音生成一次),放在静态资源目录——
      验证:提问后立即播放,挂断后不再播放
——apply 记录:2026-11-25 固定应答两句(`qwen3-tts-flash` 音色 `Serena` 合成,m4a)入库 apps/web/public/voice/(`zh-querying-one/two.m4a`,44.1kHz AAC);会话配置固定 `voice=Serena`,与实时模型同一音色;提问后立即播放、挂断不再播放的控制在 hook (通道断开/挂断即停止音频)。

## 6. 收口

- [x] 6.1 spec 增量 sync 进主 specs(新增 voice-call;chat 与 query-streaming 的
      增量),`openspec validate --all --strict` 绿
——apply 记录:2026-11-25 三份增量 sync 进主 specs(新增 voice-call;chat MODIFIED 追加 voice_turn_token 句与三场景;query-streaming ADDED 系统发起的取消),openspec validate --all --strict 57/57(56→57,新增能力)。
- [x] 6.2 文档:HANDOFF 增语音通话一节(启用配方、接入地址现值与已失效的旧
      路径、必须用按语义断句的原因、两道保护、用量限制、受限网络未验证);
      roadmap 第四梯队登记本变更落地
——apply 记录:2026-11-25 HANDOFF 增语音通话一节(启用配方/接入地址现值与失效旧路径/semantic_vad 原因/两道保护/用量限制/受限网络未验证),下一步第 4 条改为已落地+真机待验;roadmap 第四梯队 2026-11-25 登记落地。
- [x] 6.3 真机冒烟(真实模型 + 真实麦克风与扬声器,记录所用网络):①通话中
      提问→固定应答→念出答案且数字正确、页面出图表;②念答案时插话→立即静音
      并按新问题执行;③等待时「嗯」一声→不被打断;④挂断→页面回到打字状态,
      审计页可见 `chat.voice.call_end`;⑤通话中断网→自动挂断并提示;⑥未配置
      密钥的环境→无通话入口——记录结果与发现
——apply 记录:待确认——真实麦克风/扬声器/真实模型/真实网络的人类走查无法由自动化会话完成,6 条场景已全部具备可执行条件(启用三项配置即可),留 owner 配合验证;已验证面=真实链路冒烟(启动 api+agent 双进程,普通问答/ReAct 的信封流+取消+断线四路径)+全量门禁。
- [x] 6.4 全量 `make check` 绿并在 apply 记录
——apply 记录:2026-11-25 make check 分段全绿:api-check(1859 passed/10 skipped,覆盖率 77.41%+安全域分段覆盖)、agent-check(626+分段)、liannu 23、web(typecheck/lint 0 错,214 测试+2 e2e)、spec-check 57/57、lint-config、lock-check、compose-check、secret-scan、web e2e;契约以重复生成零差异为准(未提交树 contracts-check 必红属机械红)。环境受限未复跑:dead-code/deps-audit/license-check(uvx 联网下载工具轮超时;两个 pyproject 零改动,Python 依赖面与上次全绿一致)。

## Workflow follow-up

- 按 ai-code-review 走评审循环,修复后归档。
——评审记录:2026-11-25 全量未提交代码评审,修复五项缺陷并各配回归测试(先红后绿):①生产注册表未实现状态机的 new_turn_token 协议,真实通话首问即崩(路由增会话绑定适配器,补 WS 集成回归);②语音轮终态回填只覆盖结果收敛,取消与失败两条路径不回填,终态等待任务永久挂起且失败轮模型收不到如实说明(回填条件改为收敛完成即回填,CANCELLED/FAILED 判定与状态机既有消费对齐);③记忆直答与护栏拦截轮不经后台消费、同样漏回填(服务层分支处回填,结果形状抽共享函数防漂移);④挂断与通道断开竞态下同一通话双写审计、双释放准入额度(end_call 增一次收口守卫);⑤通话时长上限只由模型事件驱动,静默通话永不到期(收发循环改带超时等待,到点主动收口)。顺带防御:非对象模型事件跳帧不断通道;修正两处文不对题注释。登记三项经 owner 拍板当日即修:①连接请求不合法改 400 专属码 VOICE_OFFER_INVALID(原 502 语义错位;新错误入契约并再生);②清理恒零计数字段时发现审计真缺陷——通话审计的轮数/打断/用量一直写零(计数承载在状态机自持字段,end_call 读的是会话恒零字段),计数收敛到会话一处承载并由状态机累加,两条收口路径均得真实值,删测试专用审计快照,补通道审计真实计数回归;③删 FIXED_REPLY_HINTS 死常量。门禁:语音+聊天回归 154 例绿、api 全量 1868 passed/10 skipped(覆盖 77.58%)、ruff/pyright/pylint/lint-imports 绿、契约重复生成零差异。——owner 真机测试(2026-11-25)再曝缺陷并修复:**get_voice_service 每请求新建实例,在途通话表不跨请求,通道/挂断永远找不到在途通话(4404/404 级联、准入占位泄漏、再拨恒 409)**——改为进程内单例(与注释宣称的范式一致;测试因替身覆盖依赖而失察,补单例回归);前端 409/并发满独立提示(不再挤进通用失败文案)与拨号防抖(建立未就位前连点不再发第二个请求)。另:演示动态源 sakila_dyn 停用出清单(owner 确认重复困扰)。
