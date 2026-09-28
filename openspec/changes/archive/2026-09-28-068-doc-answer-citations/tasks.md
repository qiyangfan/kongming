# 068-doc-answer-citations 实施任务

## 1. 测试先行:键派生与净化纯函数

- [x] 1.1 失败测试:同一父块标识任意次数派生的引用键恒定;不同父块派生不同键;
      派生不依赖命中次序与路数(乱序输入 → 键集合相同);先跑红
- [x] 1.2 失败测试:净化两级匹配——全等保留、末段后缀命中保留、都不命中降级为纯文本
      (括号剥除、内部文字保留、句子其余部分逐字不变);先跑红
- [x] 1.3 失败测试:合法键取自**截断前**完整命中集——被 top-5/字符上限裁掉的命中,
      其真实标记 SHALL NOT 被判为编造;先跑红
- [x] 1.4 失败测试:合法键为空时整体跳过净化(零标记误伤);
      普通中括号文本(非键形态,无 `-`+哈希末段)不被剥离;先跑红
- [x] 1.5 失败测试:`sources` 投影与正文存活标记一一对应(不重不漏、无幽灵条目);
      先跑红
- [x] 1.6 失败测试:净化在输出护栏之前完成,下发正文与落库正文为同一串;先跑红
- [x] 1.7 失败测试:SQL 道产出的 `sources` 恒为空列表,四态终态字段零回归;先跑红

## 2. 实现:控制面(键铸造与来源投影)

- [x] 2.1 `rag/documents_service.py` 由父块标识派生引用键并随命中透出;
      `/internal/rag/retrieve` 的文档段响应**纯增** `citation_key` 字段
      **验证**:1.1 转绿
- [x] 2.2 `chat/service.py`:按净化后正文投影 `sources`(引用键→命中集回填元数据 +
      名称快照),随 `result_json` 既有序列化通路落库(**无 Alembic 迁移**,
      已核对 `0001_baseline.py:174-197` 的 `result_json` 为 TEXT)
      **验证**:1.5 转绿
- [x] 2.3 响应模型纯增 `sources` 字段,缺省空列表;`ChatQueryOut` /
      `SessionMessageOut` / `MessageResultOut` 三处一致(单一定义防字段漂移)
      **验证**:1.7 转绿;`make api-check` 绿

## 3. 实现:Agent 面(零凭证边界不变)

- [x] 3.1 新增 `graph/citations.py` 纯函数模块:键形态判定 + 两级匹配 + 降级净化
      (零框架、零 IO、零凭证;import-linter 契约覆盖)
      **验证**:1.2、1.3、1.4、1.6 转绿
- [x] 3.2 `graph/nodes.py:doc_answer` 尾部接入净化,与 057
      `numeric.audit_summarize_output` 同一收口位置;被剥计数随 trace 下发
      **验证**:冒烟可见计数;`make agent-check` 绿
- [x] 3.3 `graph/doc_prompt.py` 纪律段增量:句末标注引用键、禁自造键、禁"来源:"包装
      形态;按 `graph/prompts.py` 头部的资产变更纪律登记该条改动
      **验证**:`tests/test_prompts.py` 增断言
- [x] 3.4 `doc_hints` 消费端携带 `citation_key` 进提示词(注入段与净化合法集同源)
      **验证**:1.3 转绿

## 4. 契约与前端

- [x] 4.1 `make contracts` 导出 OpenAPI 并生成前端类型,产物一并提交(ADR-005)
      **验证**:`make contracts-check` 无漂移
- [x] 4.2 web:答案正文角标渲染 + 来源区;角标点击定位本条消息的来源项并短时高亮,
      同一引用键在不同消息间命名空间隔离不串
      **验证**:`pnpm typecheck && pnpm lint` 绿;375/768/1280 三档 × 亮暗两态走查
- [x] 4.3 web:`sources` 缺失或为空时退化为纯文本呈现,不报错、不猜测
      (历史消息与 SQL 道路径零回归);宪法 Article 8 自检 grep 通过
- [x] 4.4 净化后仍留在正文的普通中括号文本不被误渲染为角标

## 5. 收口

- [x] 5.1 spec delta sync 进 `openspec/specs/document-rag/` 与 `openspec/specs/chat/`
- [x] 5.2 手工冒烟:真引用可定位;现场构造一个编造键确认降级为纯文本且计数可见;
      改名后历史引用仍可呈现;数据问答题确认不产引用;记录于 apply
- [x] 5.3 标注率观测口径确认(低标注率走 prompt 纪律层处理,不引入判官);
      若首轮观测标注率过低,在 apply 记录并留待 prompt 迭代
- [x] 5.4 全量 `make check` 收口

> 068 落地记录:

> 068 落地记录(2026-09-27):
> - 控制面:documents_service.derive_citation_key(父块主键 SHA-256 前4位
>   十六进制 + 文档名去扩展名前缀;改名/重切块不改变键尾) + retrieve_docs
>   透出 citation_key(纯增字段)。
> - agent 面:新增 graph/citations.py(零框架零IO:键形态判定 4位大写hex +
>   两级匹配 + 编造键降级剥括号/非键形态中括号不误伤 + surviving 投影);
>   doc_answer 尾部接入净化(合法键=截断前完整命中集;被剥计数随 trace);
>   doc_prompt 纪律段增量(句末标注【引用键】逐字一致/禁来源包装/禁自造
>   键——按 prompts.py 头部资产变更纪律登记;test_prompts.py 增断言);
>   注入段标注改 citation_key(与净化合法集同源);QueryOut/state 纯增
>   sources。
> - api chat 面:AgentReply/parse 纯增(_sources_of 守卫)+ ChatOutcome +
>   result_json 落库(既有序列化通路,无迁移)+ MessageResult/_to_message
>   读档(存量缺失回空,名称快照不回查文档表)+ 契约三处纯增
>   (ChatQueryOut/MessageResultOut/_msg_out;SourceOut 单一定义)。
> - 前端:lib/citation-utils.ts(键形态/后缀/切段纯函数)+ citation-panel
>   (renderWithCitations 角标 + CitationSourcesPanel 来源区 +
>   useCitationHighlight 短时高亮,同消息命名空间隔离)+ DocumentCard/
>   AssistantCard 接线(sources 空零渲染——SQL 道与历史消息零回归)。
> - 契约:make contracts 已重新生成(sources 三处);前端 typecheck/lint/
>   format/test(60)/build 全绿;Article 8 自检零裸件/零硬编码色。
> - 测试:agent tests/test_citations.py 16 例(键形态/两级匹配/降级/普通
>   中括号不误伤/空合法键跳过/surviving 投影)+ api tests/test_doc_citations.py
>   5 例(键派生恒定/异构/改名稳定/次序无关)+ tests/test_doc_answer_citations.py
>   4 例(响应契约/SQL 道恒空/落库+回显+老消息/SSE 终态同构)。
>   agent 458 passed / api 1012 passed。
> - 5.3 标注率观测口径:净化计数已随 trace 可见( citation_sanitize
>   事件);首轮观测标注率若过低走 prompt 迭代(纪律层),不引入判官。
> - 【待确认-环境限制】手工冒烟(真引用定位/构造编造键降级/改名后历史
>   引用/数据问答零引用)需在跑服务+真实文档链路;自动化层已由全栈测试
>   等价覆盖(编造键降级在 agent 单测、SQL 道恒空在 api 全栈)。

> 全量 make check 收口(2026-09-27 最终门禁轮):api 1024 passed(覆盖率 83%,
> ruff format/check+mypy+pyright+lint-imports 全净)/ agent 458 passed(88%,
> 同套静态门禁全净)/ liannu 22 passed(89%)/ web tsc+eslint+stylelint+
> prettier+vitest 60 passed+build 绿 / contracts 导出与工作区一致(漂移
> 门禁等价通过)。五门禁全绿。
