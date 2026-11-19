# ruoyi-ai 对齐研究:能力对照与吸收裁决

> **本文定位**:回答一个问题——开源企业 AI 平台 ruoyi-ai(Java,~/workspace/java/ruoyi-ai,
> Spring Boot 3 + LangChain4j 单体,GitHub 万星级)与孔明对照,哪些值得吸收、哪些是反面
> 教训、哪些孔明已领先。调研日期 2026-10-18,结论基于两边仓库代码与 spec 实证(jar/源码级
> 三路并行调研),非印象。
>
> **一句话结论:孔明在平台治理面(迁移/凭证/测试/信任边界/引用溯源/计费配额)全面领先,
> ruoyi-ai 的真实净贡献是两项——rerank 精排(当日立项 add-doc-rerank)、MCP 运行时熔断
> (小项立项);另有四项否决登记(短剧流水线、SSE 跨节点广播、MCP 市场导入、xlsx 单独立项)。
> (2026-10-18 owner 再裁:原第三项「音视频生成」随 media-generation 域否决一并落空,
> 登记于 roadmap 否定裁决表。)**

---

## 1. 背景

ruoyi-ai 是 RuoYi-Vue-Plus 衍生的单体 AI 平台(ruoyi-admin 单进程打包),能力面:
对话/13 供应商模型管理/RAG(Milvus/Weaviate/Qdrant)/MCP/Supervisor 多 Agent/
langgraph4j 可视化工作流/WarmFlow 审批流/coding harness(类 Claude Code 子系统,
300+ 类)/短剧生成流水线/多租户(Sa-Token+TenantLine)。本次以其实际代码为清单
逐项对照孔明 `openspec/specs/`(46+ 域)。

## 2. 能力对照(ruoyi-ai → 孔明现状)

| ruoyi-ai 能力                                                      | 孔明对应                                                  | 状态                                                                                   |
| ------------------------------------------------------------------ | --------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| 模型管理(厂商+模型两层,13 供应商,OpenAI 兼容默认)                  | multi-model-routing + ADR-008 多协议出站                  | ✅(孔明部署级注册制,ruoyi DB CRUD 制,形态不同、同构覆盖)                               |
| RAG:向量+全文双路 RRF、多向量库                                    | rag 三路召回 + document-rag 父子块 + 098 zhparser 配置化  | ✅(pgvector 单栈;多向量库规模化=Milvus 已立项,见 ai-platform 调研)                     |
| RAG:rerank 精排(3 供应商+扩量+阈值)                                | —(RRF 融合后直接截断)                                     | ❌ **净缺口①,本日立项**                                                                |
| 对话记忆(MessageWindow 20 条+落库)                                 | agent-memory                                              | ✅ 领先(ruoyi 摘要记忆有配置无实现,死配置)                                             |
| MCP client(BUILTIN/LOCAL/REMOTE+健康检查+熔断+市场导入)            | mcp-interop(109 resources/prompts、113 真实 server 集成)  | ✅~ 运行时熔断缺(净缺口②);市场导入否决(见 §5)                                          |
| 工具/Skills 四层体系                                               | tool-calling + tool-gateway(089)+ skill-pack              | ✅                                                                                     |
| Supervisor 多 Agent(langchain4j-agentic,固定 5 子 Agent 硬编码)    | multi-agent-orchestration(091 分工名册、115 圆桌对等)     | ✅ 领先(名册可配置 vs 硬编码;ruoyi Agent 阶段无流式)                                   |
| 可视化工作流(langgraph4j,9 节点,SSE 节点事件)                      | workflow-orchestration + 107 拖拽画布                     | ✅(ruoyi 无版本管理/DSL/断点续跑,检查点仅内存;孔明 add-graph-checkpoint 在途)          |
| coding harness(durable journal/checkpoint/双审批/上下文压缩)       | task-kernel(八态状态机/审批门/检查点)+ cloud-code-sandbox | ✅ 同构覆盖(ruoyi 全仓质量最高子系统,但未反哺其工作流引擎)                             |
| SSE(双索引+心跳+Redis 跨节点广播)                                  | query-streaming(双进程)                                   | ✅~ 单节点形态;跨节点广播否决(§5)                                                      |
| 多租户(TenantLine 插件+租户套餐)                                   | multitenant(schema 约定级)                                | ✅                                                                                     |
| 认证(Sa-Token+JWT+社交登录)                                        | auth + ADR-003/006 联邦连接器                             | ✅                                                                                     |
| 计费/会员/支付                                                     | —(ruoyi 开源版亦无,属其商业版)                            | ➖ 双方开源面均无;孔明 payment-gateway(mock 闭环)+ developer-portal-billing 领先       |
| 限流配额(仅登录/验证码 4 处防刷,模型调用零配额)                    | 103 用户/TEAM 维度配额                                    | ✅ 领先(ruoyi 公网 demo admin/admin123,模型调用裸奔)                                   |
| 异步任务(SnailJob 全套表+服务端拖入但零业务用例)                   | task-kernel                                               | ✅ 领先                                                                                |
| 可观测(自研 trace 表+SBA 监控)                                     | observability(038)                                        | ✅                                                                                     |
| OSS 多厂商抽象(S3 SDK)                                             | 附件落盘管线                                              | ➖ 形态不同,不吸收(见 §5)                                                              |
| 媒体生成(图/音频/视频/音乐+异步轮询)                               | —                                                         | ❌ 域整体否决(owner 2026-10-18 裁决,roadmap 否定裁决表;原拟 add-media-generation 已删) |
| 知识图谱(实体+CRUD 空壳,neo4j 依赖零引用)                          | —                                                         | ➖ 半成品,不吸收                                                                       |
| 短剧流水线(剧本→分镜→图→TTS→音乐→FFmpeg)                           | —                                                         | ❌ 否决(§5)                                                                            |
| 文档解析(PDF/Word/Excel/Markdown/代码;Csv/Json/Github/Folder 空壳) | document-rag(.md/.txt/.pdf/.docx)                         | ✅~ xlsx 属域内小 delta(§5)                                                            |

## 3. 孔明已领先面(反向确认,无需动作)

治理与工程纪律是本次对照最悬殊的差距方向,全部为 ruoyi-ai 实证短板:

1. **凭证安全**:ruoyi 模型 `api_key` 明文落库、Excel 导出可致密钥泄出、JWT 默认弱
   密钥与代码同仓;孔明数据源凭证 AES-GCM(008 D2)、HTTP 连接器 auth_secret_enc、
   sys_api_key 仅存哈希、prod 缺配 fail-closed——既有纪律的价值实证。
2. **数据库迁移**:ruoyi 手工 SQL(全量 dump+17 个按日期增量脚本,人肉升级);
   孔明 Alembic baseline 制。
3. **引用溯源**:ruoyi 引用止步于 prompt 元数据,前端拿不到结构化引用;孔明 068
   稳定引用键+正文标注+编造净化,完整闭环。
4. **测试门禁**:ruoyi 全仓 103 个测试文件,aiflow/workflow/system 模块零测试,
   RAG 主链路仅 1 个;孔明 `make check` 全量门禁+安全规则测试先行。
5. **架构纪律**:ruoyi 单进程单体+JVM 本地缓存(memoryCache/MCP/embedding 缓存均
   不可横扩)+ChatServiceFacade 977 行+小程序 WebSocket 重复实现一套聊天逻辑;
   孔明双进程信任边界(ADR-002)+域包分层+import-linter+Redis 单源。
6. **半成品管控**:ruoyi 主线躺着一堆空壳(图谱、PPT 枚举、Dalle3/FaqExtractor
   节点配置即 NPE、摘要记忆死配置、未注册的 TokenTextSplitter);孔明「登记不背债+
   spec 同步可信」纪律直接免疫此类债。

## 4. 吸收裁决:三项立项

排序按工程依赖序与价值密度(owner 既有裁决口径:学习展示价值即立项依据):

1. **add-doc-rerank(本日立项)**:document-rag 检索精排层(rerank)。检索质量工程
   的行业标准一环,问答智能体方向(ADR-010)直接受益;部署级条件注册零回归,
   与 094 web 检索出站形态同构,增量成本低。四工件已过校验。
2. **MCP 运行时熔断(小项,排序 Milvus 之后)**:kongming mcp-interop 现状=同步期
   断连降级;ruoyi 的「连续失败 N 次自动冷却」补齐调用期韧性(server 宕机时不再
   每问重复付超时延迟)。开工时立 mcp-interop 域 delta。
3. ~~音频/视频生成(media-generation 域扩展,排序图像落地后)~~:**落空(2026-10-18
   owner 再裁)**——承载域 media-generation 被直接否决,音/视频生成作为其扩展
   一并落空,登记 roadmap 否定裁决表;TTS 输出侧若将来单独立项再议。

## 5. 否决登记(理由防反复重提)

| 项                                   | 裁决           | 理由                                                                                                                        |
| ------------------------------------ | -------------- | --------------------------------------------------------------------------------------------------------------------------- |
| 短剧流水线(剧本→分镜→配图→配音→合成) | 否决           | 方向外:ADR-010 五方向无内容创作归口,「数据为刃」禁止横向摊薄;重依赖 FFmpeg/音频合成,学习边际价值低于同等投入的问数/协作深化 |
| SSE 跨节点广播(Redis pub/sub)        | 否决           | 单节点部署形态下无场景;孔明双进程 relay 已覆盖现有拓扑,属「为不存在的规模付复杂度」                                         |
| MCP 市场一键导入                     | 否决           | ruoyi 自身的供应链高危面(外部市场元数据一键导入即得 Stdio 执行权);孔明人工注册+出站白名单是更优形态,不引入该风险面          |
| xlsx 知识文档解析单独立项            | 否决(搭车注记) | 域内小 delta(document-rag 格式清单+1),单独立项价值不足;随该域下次变更搭车                                                   |
| 多厂商 OSS 抽象(S3 SDK/MinIO)        | 否决           | 部署形态=本地盘+既有落盘管线已覆盖;引入对象存储栈无对应场景,与 Milvus 同属「规模化前置」反模式                              |

## 6. 与 ai-platform 调研的关系

本文与 `docs/ai-platform-alignment.md`(2026-10-18)互补:彼文回答「自建姊妹项目
要不要冻结」,本文回答「外部开源标杆吸收什么」。两文净缺口不重叠(ai-platform
三项=图检查点/媒体生成/Milvus,其中媒体生成已被 owner 否决、仅图检查点与
Milvus 维持立项;ruoyi-ai 净吸收两项=精排/熔断)。孔明 spec 面经两轮外部对照后,
剩余登记面见 roadmap 第四梯队(单源)。
