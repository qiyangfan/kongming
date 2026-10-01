# external-knowledge Specification

## Purpose
把个人本地知识库(siku)收编为孔明 agent 的一条**检索工具**:LLM 在 ReAct 循环中可自主检索个人文档口径,结果以带来源锚点的结构化观察进入上下文。知识库是外部系统,经控制面出站调用;agent 面不持有也不感知该端点的存在形态之外的一切(零凭证纪律不变,ADR-002)。

本能力 v1 只服务单租户个人部署:个人 vault 没有租户语义,多租户可见同一 vault 是数据越界。

## Requirements

### Requirement: 控制面收口执行

`knowledge_search` 的执行 SHALL 收口于控制面(与其余内置工具同一回调路径),Agent 面 MUST NOT 直连知识库端点。执行入参为 `query`(必填,非空)与 `top_k`(可选,缺省 5,上界由实现定);控制面 MUST 以超时(部署级可配)调用知识库检索端点;结果 MUST 转为结构化观察:每个命中包含 chunk 文本与来源锚点(`note_path`/`source_name`/`anchor`);观察 MUST NOT 包含知识库主机的凭证形态信息。

#### Scenario: LLM 自主调用并消费观察

- GIVEN 已注册工具,用户提问「退款时效的口径在哪份文档」
- WHEN LLM 选择调用 `knowledge_search`,知识库返回 3 条命中
- THEN 工具观察包含 3 条文本与各自来源锚点,LLM 的最终答案引用其中至少一条锚点

#### Scenario: 空结果如实回喂

- GIVEN 知识库检索返回 0 条命中
- WHEN 工具执行完成
- THEN 观察明确表达「未检索到相关内容」,供 LLM 决定改走其他工具或据实回答

### Requirement: 失败降级为观察

检索失败 SHALL 降级为结构化错误观察(分类至少含 unreachable/protocol),Agent 面 MUST NOT 因知识库不可用而崩溃、MUST NOT 编造检索结果;降级观察中 MUST NOT 暴露内部堆栈或端点地址细节。连接层未送达类失败 SHALL 按只读分层重试至多一次;响应类错误(4xx/5xx/协议错误)MUST NOT 重试。

#### Scenario: 知识库未启动

- GIVEN 工具已注册但 siku 服务未监听
- WHEN LLM 调用 `knowledge_search`
- THEN 返回 unreachable 观察(含一次连接层重试),agent 据实说明「个人知识库暂不可用」,本轮问答不失败崩溃

#### Scenario: 响应错误不重试

- GIVEN 知识库返回 500
- WHEN 工具执行
- THEN 只调用一次,返回 protocol/unreachable 分类观察,不进入重试循环

### Requirement: 审计与授权

`knowledge_search` SHALL 带权限点参与调用方授权过滤(未授权调用方注入集不可见);每次执行(成败均记)SHALL 落既有工具调用审计,记录工具名、参数摘要、成败分类与耗时;审计行 MUST NOT 记录知识库端点地址之外的连接细节。知识检索不产 token 消耗,MUST NOT 落用量记录(非 LLM 调用)。

#### Scenario: 未授权调用方看不到工具

- GIVEN 调用方授权集不含 `tool:knowledge:search`
- WHEN 发起 ReAct 问答
- THEN 注入集不含该工具;直接回调执行亦被拒绝并落越权审计

#### Scenario: 执行留痕

- GIVEN 工具已注册且授权
- WHEN 执行一次检索(无论成败)
- THEN 工具审计表新增一行(工具名/成败分类/耗时),无 `sys_llm_usage` 新行

### Requirement: 部署级注册与多租户共享面

系统 SHALL 提供部署级内置工具 `knowledge_search`,其注册 MUST 满足两个条件,缺一不注册:

1. 控制面配置了知识库端点(`KM_SIKU_BASE_URL` 非空)。
2. 端点主机 MUST 为 `127.0.0.1`、`localhost` 或 `host.docker.internal`;其余主机(含任意远端 IP/域名)MUST 拒配并以告警记录原因,不静默忽略。

未注册时,该工具 MUST NOT 出现在任何调用方的工具注入集、工具清单或审计枚举中——对调用方是完全不存在的工具,零回归口径。

个人知识源没有租户语义:多租户部署下所有租户 SHALL 共享同一检索面(同一 vault 内容对全部授权调用方可见)。系统 SHALL 在启动探测到多租户时输出告警明示该共享面,MUST NOT 以此拒绝注册——本能力 v1 定位个人单租户部署,租户级知识源隔离(每租户各配各的端点)不在本能力范围内。

#### Scenario: 未配置端点时工具不存在

- GIVEN 未配置 `KM_SIKU_BASE_URL`
- WHEN 任意用户发起 ReAct 问答
- THEN 注入集不含 `knowledge_search`,行为与引入本能力前逐字节一致

#### Scenario: 非 localhost 端点拒配

- GIVEN 配置 `KM_SIKU_BASE_URL=http://knowledge.example.com:9000`
- WHEN 控制面启动(或配置重载)
- THEN 工具不注册,产生一条告警记录端点主机非法;服务其余部分正常

#### Scenario: 多租户部署告警但注册

- GIVEN 已配置合法端点,且系统中存在 2 个及以上租户
- WHEN 控制面启动
- THEN 工具正常注册(授权调用方注入集可见),并产生一条告警明示所有租户共享同一个人知识源检索面
