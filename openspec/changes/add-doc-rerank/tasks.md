# Tasks

## 1. 配置与供应商适配(测试先行)

- [ ] 1.1 失败测试先行:`KM_RERANK_*` 组合校验(未配=零存在感;provider 非法
  值=启动校验拒绝;配 provider 缺 key=告警降级不生效;key 掩码不落日志)——
  镜像 websearch_misconfiguration 测试形态,先红后绿
- [ ] 1.2 `config.py` 增加 rerank 配置组(provider/key/model/pool_topk/score
  _threshold/timeout_ms,缺省值见 design D2)与组合校验;`.env.example`
  补注释条目;验证:1.1 测试转绿
- [ ] 1.3 rerank 供应商适配器两段式注册表(siliconflow + bailian 两适配器,
  Cohere 形态归一 `results[i].index/score`;出口主机进出站白名单校验,
  白名单外剔除告警);验证:适配器单测(方言报文→归一分数)+白名单外条目
  剔除测试

## 2. 检索精排级接入(documents_service)

- [ ] 2.1 失败测试先行:精排启用时融合后候选池扩量→打分→按父块最高分截断
  top-5;未启用时行为与现状逐字节一致(RRF 名次截断);同父多子取最高分;
  引用键与名次无关——先红后绿
- [ ] 2.2 失败测试先行:精排异常(超时/5xx/限流)降级 RRF-only 不阻断,
  降级原因计入可观测计数;阈值过滤(>0)后空命中走既有「文档未命中」语义
  ——先红后绿
- [ ] 2.3 `documents_service.py` 检索管线插入可选精排级(挂接点=RRF 融合后、
  父块回填前;子块文本传参截断上限);验证:2.1/2.2 测试转绿
- [ ] 2.4 rerank 出站调用接入统一出站通道与既有计量/出站审计面(calls/latency
  维度);验证:审计/用量断言测试(调用留痕、key 掩码)

## 3. 端到端与回归

- [ ] 3.1 知识态/融合态端到端冒烟:启用精排(百炼 key 本地已有)走通
  「检索→精排→回填→引用键稳定→正文标注」全链;关闭配置重启后零回归
  (既有 document-rag 全量测试通过)
- [ ] 3.2 `make check` 对应子集(api ruff+mypy+pytest)通过;trace 面可见
  精排层耗时与降级计数;HANDOFF 补 rerank 部署配方与阈值建议值

## Workflow follow-up

- 归档走 `/opsx:archive`(openspec-archive-change 技能)
- roadmap 第四梯队的「表说明召回精排」候选不在本变更内,勿顺手实现
