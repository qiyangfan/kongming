# Design

## Context

检索管线现状:document-rag 检索(`apps/api/kongming/rag/documents_service.py`)
为「子块向量 top-K ∥ 子块全文 top-K → RRF 融合 → 按 parent_id 回填 top-5 父块」,
排序止步于 RRF 名次。嵌入调用已在控制面(api 持 pgvector 与模型 key),embedding
不可用降级纯全文路的先例在 `rag/service.py`(降级不阻断、计入日志)。部署级
条件注册的既有范式=websearch(094:`config.py` 中 provider/key/timeout 组合校验,
残缺 prod fail-closed、dev 告警降级;未配 key=域从未存在)。
信任边界(ADR-002)与出站协议框架(ADR-008)均不变:rerank 出站全部发生在
api 控制面,agent 零改动。

## Goals / Non-Goals

**Goals:**

- RRF 融合后插入可选精排级,启用时候选池扩量重排、未启用时逐字节零回归。
- 供应商适配按 Cohere 形态 `/rerank`(query+documents→scores,事实标准)归一,
  起步两适配器:硅基流动、百炼(本地已有百炼 key 即真实生效)。
- 精排失败降级 RRF-only 不阻断,降级原因可观测。

**Non-Goals:**

- 表说明召回(rag 三路)精排、跨通道重排、租户级管理面 CRUD、本地 rerank
  推理、score 下发公共契约(见 proposal Non-goals)。

## Decisions

**D1 精排级落在融合后、父块回填前(而非两路召回各自精排)。**
候选池=RRF 融合后的子块并集(扩量至可配上限,缺省 20),对子块文本打分后
按「分数最高的父块集合」截断 top-5 回填。替代方案=各路召回内精排:两路各自
打分后仍需融合,多一次调用且语义与「融合排序」定位重叠,弃。子块粒度打分
(非父块)因为子块才是向量/全文的命中单元,父块是回填产物;同父多子命中取
最高分为父块分。

**D2 供应商注册复用 websearch 范式,单供应商部署级配置。**
`KM_RERANK_PROVIDER`(siliconflow | bailian)+ `KM_RERANK_API_KEY` +
`KM_RERANK_MODEL` + `KM_RERANK_POOL_TOPK`(缺省 20)+
`KM_RERANK_SCORE_THRESHOLD`(缺省 0)+ `KM_RERANK_TIMEOUT_MS`。组合残缺
校验进 `config.py` 既有 `websearch_misconfiguration` 同谱系(prod 残缺拒启
或剔除告警,与 websearch 口径对齐:配了 provider 缺 key=告警降级不生效,
非法 provider 值=启动校验拒绝)。单供应商(非多路由)与 websearch 一致:
检索是每问必经路径,供应商选择属部署事实而非按问路由。key 掩码同谱系
(日志/审计不落明文)。

**D3 出站走统一出站通道,出站主机进既有白名单体系。**
适配器两段式(请求装配/响应归一)镜像 websearch(094)的供应商注册表;
出口主机命中既有出站白名单(099/089 语义),白名单外条目剔除并告警。归一
响应只取 `scores`(与 documents 顺序对齐),供应商报错归一为超时/限流/不可用
三类,统一触发降级。

**D4 降级语义对齐 embedding 先例,粒度=单次检索调用。**
精排异常(网络/超时/5xx/限流)→ 记 warning + 可观测计数,RRF 序直接截断
继续;不做连续失败熔断(rerank 是增强路径而非依赖路径,每次降级成本=一次
失败调用,无需状态)。这与 ruoyi-ai 的「3 次失败禁用 5 分钟」不同:其熔断
对象是工具面(每次失败代价高),本域降级代价低,简单优先。

**D5 引用键与净化零改动。**
068 稳定引用键由父块标识确定性派生、与名次无关——精排只改名次,键空间
不变;编造净化取「截断前完整命中集」为合法键集合,精排后命中集即截断集,
语义自洽,零代码改动。阈值过滤后空命中走既有「文档未命中」收口
(knowledge-qa 纯数据答),同样零改动。

**D6 计量与审计复用既有面,内部契约零变化。**
内部检索接口(`/internal` 检索路径)请求/响应形态不变(仅顺序可能变);
rerank 调用作为模型出站记入既有用量/出站审计面(calls/latency 维度,
无 token 字段——rerank 无 completion 概念,计量口径在实现层对齐
multi-model-routing 域既有字段,不新立 spec)。

## Risks / Trade-offs

- [精排延迟叠加在每次知识态检索上(一次额外出站,~200-800ms)] → 检索路径
  仅知识态/融合态走文档通道;超时上限收紧(缺省 3s)+降级不阻塞;延迟计入
  既有 trace 面可评估后调整部署配置。
- [扩量池=20 子块的单次 rerank 载荷膨胀(文档长文本)] → 传参截断子块文本
  上限(实现层,缺省沿用子块 ~200 字符形态,天然有界)。
- [两家起步供应商的方言漂移(Cohere 形态细节不一)] → 两段式适配器隔离方言;
  归一层只依赖 `results[i].index/score` 最小公共面。
- [阈值误配导致知识态频繁空命中] → 缺省 0(不过滤);阈值属部署调优项,
  文档(HANDOFF)记录建议值与观测口径。

## Migration Plan

纯增量、零迁移:无新表、无契约变更、无 agent 改动;未配置环境变量时行为
与现状一致,回滚=删除环境变量。启用步骤:配置 `KM_RERANK_*` → 重启 api →
知识态问句验证 trace 中精排层出现与引用键稳定。

## Open Questions

(无)
