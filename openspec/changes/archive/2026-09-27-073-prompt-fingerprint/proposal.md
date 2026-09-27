# 073-prompt-fingerprint 提案:prompt 内容指纹(让评估红线的对比可解释)

## Why

prompt 资产的维护纪律在仓内是好的:`graph/prompts.py` 1–25 行的 docstring
逐条登记了自上游移植以来的每次修订(018/037/043/046/048/057…),
`evals/README.md` 也立了基线纪律——

> 评估方法、提示词、表结构知识或文档增删变更后跑全量记录四组分数并标注原因;
> 宽松(黄金指标)劣化 ≥5pp 必须逐题溯源后才可合入。

问题是这条纪律**完全靠人执行**:改 prompt 的人要记得重跑、记得标注,
看数字的人要知道「这两次跑之间 prompt 有没有变」。且 prompt 资产
**没有机器可读的内容身份**——两次跑出不同分数的 revision,在评估产物里
长得一模一样。

更紧迫的是 065(eval-ci-gate)即将把红线自动化:nightly 与 artifacts 中
上次记录比对,劣化 ≥5pp 即判失败。**在没有 prompt 身份的前提下,自动红线
必然误判**:

- prompt 主动优化后分数下降 6pp(预期内的取舍)→ 红线报回归,
  且没人能证明这不是回归;
- 反之,真回归可以被「prompt 反正也动过了」顺手带过。

065 落地即带上这个洞,而补它的成本极低:一次哈希 + 一个内省端点 + 一条输出行。

## What Changes

1. **prompt 内容指纹**:对生效 prompt 资产全集(`graph/prompts.py`、
   `graph/doc_prompt.py`、`graph/extract_prompt.py`、`graph/react/prompts.py`)
   取确定性密码学哈希(SHA-256 的短十六进制)。纯函数、无 IO、零依赖;
   资产任一字变化即变指纹,资产不变则跨进程/跨次运行一致。
2. **agent 内省端点**:`GET /internal/prompt-fingerprint`,受既有
   `X-Internal-Token` 中间件守门(ADR-002 不变),控制面与评估器可取。
3. **评估产物携带指纹**:评估运行首行打印指纹;`evals/README.md` 基线表
   增加指纹列;**跨指纹比对时显式标注「跨 prompt 版本,通过率不可直接比较」**,
   SHALL NOT 静默套用劣化红线。
4. **契约**:新增内部端点 → `make contracts` 并提交产物(ADR-005)。

## Non-goals

- **prompt 的 DB 热配置 / 管理台编辑 / 骨架唯一槽**(参考项目三层配置:
  Nacos + `qa_step_prompt_config` + `PromptSkeleton`):本仓 prompt 是发版资产,
  热更引入第二真理源与提示词注入面,收益不抵成本;参考项目自己把元层过度设计
  列为坏味道。指纹只标识「当前是哪个版本」,不提供版本编辑能力。
- **指纹随 LLM 用量落库**(生产侧把具体消息归因到 prompt 版本):
  需 `sys_llm_usage` 加列 + `chat_message` 补 trace_id 两处改动,属
  observability 域。**重估条件(写死)**:支持侧出现「这条答案出自哪个 prompt
  版本」的追责需求 ≥2 次。
- **prompt 版本管理 / 回滚 / A-B 分流**:git 已是版本真理源,指纹只做身份标识;
  再造一套版本号是双源。
- **评估集自身的指纹**:gold 集与匹配器由常规测试门禁守护(ai-eval 域
  「匹配器逻辑常规门禁守护」既有 Requirement),065 的红线口径另立;
  本变更不重复造账。

## Capabilities

### Modified Capabilities

- ai-eval:新增「Prompt 版本指纹」Requirement(指纹确定性、随评估产物
  落记录、跨指纹不可比标注);「CI smoke 守护」的基线记录须含指纹。

## Impact

- **落点**:`apps/agent/kongming_agent/`(新增指纹纯函数 + 内省端点)、
  `apps/api/evals/test_eval.py`(取指纹并打印)、`apps/api/evals/README.md`
  (基线表增列 + 跨版本标注示例)。
- **契约**:`GET /internal/prompt-fingerprint` 新端点 → `make contracts`
  并提交产物(ADR-005);CI 漂移门禁兜底。
- **ADR**:不涉及。ADR-002 信任边界不变(端点仍受同一内部令牌守门,
  不新增对外暴露面)。
- **与在流变更的关系**:
  - **065(eval-ci-gate)** 是本变更的直接消费方——065 的红线判定应读指纹
    后再决定「可比/不可比」。两变更的接线点在 065 的 tasks 中,本变更只负责
    把指纹造出来并送进评估产物。
  - **069-chat-quality-observability**(并行提案)在其落库四信号里也含
    `prompt_version`(agent 启动时计算的同一枚指纹),但**不覆盖评估侧**:
    评估以 persist=false 运行、不落 `chat_message`,且指纹不进用户响应,
    故评估链路取不到指纹。两变更共用同一枚指纹纯函数,**落地时以先完成者
    为准、后完成者复用不重写**,不得出现两份指纹算法(见 tasks 4.2)。
  - **无 schema 变更**。
