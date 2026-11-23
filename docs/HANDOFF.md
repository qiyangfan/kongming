# HANDOFF — 孔明(kongming)

> 四块:当前状态 / 下一步 / 开放决策项 / 验收记录;变更细节归 git log 与 OpenSpec 归档。

## 当前状态(2026-11-23)

- 统一身份:Keycloak 为唯一登录入口(`KM_KEYCLOAK_*`、`/auth/keycloak/callback`);
  授权仍全部本地(角色/租户/`data_scope`/本地 RS256 会话令牌,不消费 Keycloak claim);
  `/auth/login` 已移除,评测登录取现签 access JWT(`KM_EVAL_ACCESS_TOKEN`)。
- 文档检索:向量 + 全文(ts_rank,zhparser 可选)+ 可选实体扩展路 → RRF(k=60)
  → 可选精排 → 父块 top-5。精排与 zhparser 均已验收但**不启用**(证据见评测基线,
  配置与 runbook 就绪)。被索引文本=文档名+块正文(embedding 与 tsv 同源,
  content 与返回引用保持原文);存量用 `scripts/rebuild_embeddings.py
  --table document_chunks` 重建(不重切块)。全文兜底轮零分命中(按全问句
  ts_rank ≈1e-20 的部分命中)不进入融合,词面只贡献 AND 轮正分证据。试跑的
  `parent_top_k` 已贯通到召回级(诊断参数;主检索仍固定 top-5)。
- 评测体系:retrieval-eval 15 文档 36 父块 / 28 题;BM25 正式对照 80 题;
  Wiki 问答对照 26 题。两份评测 change 与块索引/兜底两份 change 已归档,
  规格增量已应用(`openspec/specs/retrieval-eval`、`document-rag`)。
- 实验室规模语料:corpus_scale(45 文档 234 父块,生成器产出)+ 74 题 +
  判别变体 corpus_scale_v1(正文剥离店名)+ 负例 12 条;`KM_EVAL_RETRIEVAL_DATASET`
  切换,逐题附逐路 Recall;`calibrate_fulltext_gate.py` 输出门槛候选(不写配置)。
  真实租户语料到位后按同形态自建 dataset + corpus 目录即可评测。
- 真实公开文档:k8s_eval 30 题挂 corpus_k8s(73 篇/1400 父块,CC BY 4.0,
  NOTICE 归属);题面与 gold 由模型按原文事实编写,程序校验(片段唯一、跨文档
  零重复、多跳跨父块)+ AI 复核,未经人工领域评审;当前基线融合 0.833/全跳
  0.733(向量 0.817/词面 0.150),跨文档多跳全跳 0;zhparser 复测 0.817
  (词面路 0.700,仍不切换)。
- 多模型注册表:人工条目按「模型×端点」编队,别名优先;任务路由
  TITLER/SUGGEST/DESCRIBE→qwen-flash;主模型与 embedding 走 maas 镜像域,
  dashscope 作回滚端点;compose 已透传注册表/路由/白名单三项。
- 门禁:api-check 全绿(1748 passed,10 skipped,覆盖率 77.13%);
  `openspec validate --all --strict` 54/54。

## 下一步

1. 按 `add-doc-query-decomposition` 提案实现文档多查询分解:意图路由同帧产出
   2-3 条子查询,retrieve 原问优先、并行补检、引用键去重合并(上限 8);
   跨文档多跳不承诺,验收口径见提案 design。
2. 观察 CI(contracts-check、eval-nightly、镜像扫描)。
3. 反代部署确认 `KM_AUTH_COOKIE_SECURE`、可信代理头与登录限流口径。
4. eval-nightly 首次真实运行:配置 `KM_AGENT_LLM_API_KEY` 与
   `KM_EVAL_ACCESS_TOKEN`(旧 `KM_EVAL_ADMIN_PASSWORD` 仅兼容旧部署)并建基线。
5. 观察多标签页并发登录的 state cookie 覆盖问题,必要时改多值/按 tab 存储。

## 开放决策项

- 文档精排已验收未启用:链路/降级/延迟达标(旧索引 p95≈1.3s),旧索引语料
  1.000→0.982(唯一退化=012 登记豁免边界题);新索引文本下复跑 0.964→0.893
  (-7.1pp,两模型一致),无非负收益;启用条件=语料扩容后精排出现非负收益。
- zhparser 已验证可建(配方见 `docs/zhparser-runbook.md`),本语料 A/B 退化
  (旧索引 1.000→0.929;新索引 0.964→0.946)、zhparser+BM25 无差异,不切换;
  复评条件=真实语料出现词面缺口。实验室语料显示 zhparser 使词面路自身
  Recall 0.162→0.851(融合仍满分),说明其价值在词面缺口语料,切换决策不变。
- 块索引上下文已落地(文档名+正文用于 embedding 与 tsv):判别语料
  0.291→1.000(+70.9pp);15 文档回归 0.964(-3.6pp,红线内:009/012 各丢
  「租赁规则」一跳,012 为登记豁免边界题),74 题回归 1.000。门槛复标
  (修正向量分方向为相似度):文档通道探针上向量可分(正例 min 0.678 >
  负例 max 0.630,候选 ≈0.67 双 100%);`KM_RAG_GATE_*` 属 schema 通道,
  阈值未写入,接入前须在目标通道复测。若未来收紧红线,
  先逐题评估 009/012 再动索引文本。
- 真实文档基线(K8s 73 篇/30 题):兜底零分剔除后 0.833/全跳 0.733(修复
  s_01/m_02/m_04,m_05 失去一跳跃为兜底零分无序命中);跨文档多跳 6 题
  全跳 0,诊断显示多数 gold 父块不在任一路候选前 8(非融合丢失),现有
  top-5 契约内无低风险修法,登记为已知边界。
- `0066` DOWNGRADE 只恢复 `admin` 账号,不回滚其余影子账号角色/租户绑定;回滚旧二进制需人工评估。
- `/auth/refresh` 仍接受 body refresh_token 且响应回传明文;前端已走 cookie,是否收窄为只回 access 待拍板。
- `sys_user.id` 依赖 Keycloak sub 为 UUID 的既有契约;非 UUID sub 需迁移。
- 登录限流为进程内、每 worker 独立;严格全局阈值需共享后端。

## 验收记录

- 2026-11-23 块索引上下文 A/B:判别语料(正文剥离店名)旧代码 0.291 →
  重建后 1.000(+70.9pp);15 文档回归 0.964(-3.6pp,009/012 各丢「租赁规则」
  一跳,012 为登记豁免边界题),74 题回归 1.000。重建 552 块后父块正文逐字
  一致、子块向量全 2560 维;门槛复标:文档通道向量相似度可分(候选 ≈0.67,
  正负例均 100%),阈值未写配置(schema 通道须复测)。
- 2026-11-23 新索引复跑(块索引上下文生效后):BM25 80 题 keep(两路逐题
  一致,边界失配 03/10/17,p95 11ms);精排 gte/qwen3 均 0.893(无精排
  0.964,-7.1pp,不启用);zhparser 0.946 vs simple 0.964(词面路 0.929,
  不切换)。15 文档同配置复跑与首轮逐题一致(009/012),-3.6pp 属确定性
  形态,按新形态基线接受,不调参。
- 2026-11-23 真实公开文档基线(K8s 中文,AI 编写 + 程序校验 + AI 复核,
  未经人工领域评审):30 题融合 0.783/全跳 0.667(向量 0.817/词面 0.350);
  失配=融合稀释 1 题、同文档多跳缺一父块 3 题、跨文档多跳全跳 0。
- 2026-11-23 全文兜底零分剔除:K8s 30 题 0.783→0.833(+5.0pp,全跳
  0.667→0.733),修复 s_01/m_02/m_04,m_05 失去一跳跃(原为兜底零分无序
  候选);15 文档 0.964、lab 74 与判别语料 1.000 均零回归。zhparser 在 K8s
  语料复测 0.817(词面路 0.700 vs simple 0.150,融合仍低于 0.833),维持
  不切换。
- 2026-11-23 实验室规模语料矩阵(74 题):融合 Recall 恒 1.000(向量路满分);
  zhparser 词面路 0.162→0.851、精排 74/74 无增量、实体路 0.135 无增量;
  初版(条款不含店名)融合 0.818,17 题全路无解,揭示 chunk 标题上下文缺口。
- 2026-11-23 检索评测收口:BM25 80 题 keep(两路逐题无差异,P95 19ms→
  新索引 11ms);wiki 两轮 keep(跨资料 90% 与 raw 持平、引用 <95%);
  精排 A/B 1.000→0.982、zhparser A/B 1.000→0.929,均不启用。
- 2026-11-23 Keycloak 真实联调:demo 登录后 `/auth/me` user_id=Keycloak sub、
  roles/tenant/perms 来自本地;登出 redirect 指向 Keycloak,旧 refresh 401。
- 2026-11-23 全量 api-check 1748 passed/10 skipped、覆盖率 77.13%;归档后
  `openspec validate --all --strict` 54/54。
