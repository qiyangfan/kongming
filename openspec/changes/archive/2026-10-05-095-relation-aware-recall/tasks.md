# 095-relation-aware-recall 实施任务

## 1. 测试先行(先红)

- [x] 1.1 失败测试:`relations_touching` 查询——源侧命中、目标侧命中均返回;
      两端均在集合外的关系不返回;tenant + datasource 双过滤(跨租户/跨源零命中)
- [x] 1.2 失败测试:扩展纯函数——一跳、去重(已在召回集内的表不补)、
      上限截断、扩展条目尾附加(顺序断言)
- [x] 1.3 失败测试:扩展表进 schema_hint 后,`/internal/retrieve` 的
      relations_hint 覆盖扩展表的 JOIN path(收窄基准已扩展,而非仅主召回两两)
- [x] 1.4 失败测试:端口未注入 / 关系查询抛异常 → 降级为无扩展,
      召回主链路不阻断、不抛 500、错误终态不出现
- [x] 1.5 失败测试:关系写路径(introspection upsert / 手工新增 / 手工删除)
      后,该 tenant+datasource 的 ctx 缓存被失效(写后同问句再召回即见新关系)
- [x] 1.6 失败测试:kill switch 关停时,输出与开启前逐字一致(零回归锁)
- [x] 1.7 失败测试:`recall-probe` 透出扩展明细(补入表/经由关系/扩展后集合);
      无 system:rag:manage → 403;试跑前后缓存/配置/审计零写入
- [x] 1.8 失败测试:关系查询的租户与数据源隔离——A 租户的关系不参与 B 租户扩展

## 2. 实现(api 面)

- [x] 2.1 `semantic/repository.py` 增 `relations_touching`(既有
      `rag.table_relations`,任一端在集合内即命中;复用 `relations_within` 的
      参数化与双过滤口径)
- [x] 2.2 `rag/ports.py` 增 `RelationExpansionPort` Protocol;
      `rag/models.py` 增扩展纯函数(入参=召回键序列 + 关系行 + 上限)
- [x] 2.3 `rag/service.py` recall 在闸门之后、`fetch_entries` 之前插入扩展;
      配置一票短路早于任何查询(关闭形态零 SQL)
- [x] 2.4 `rag/router.py` 组合根注入适配器(经 `SqlSemanticRepo`);
      `config.py` 增 `rag_expand_enabled`(缺省 True)/ `rag_expand_max`(缺省 2)
- [x] 2.5 `semantic/router.py` 三个写入口(introspection / 关系增删 / 级联清理)
      提交后经窄端口失效 ctx 缓存;`semantic/ports.py` 声明
      `RecallContextEvictor` 窄端口
- [x] 2.6 `recall_probe` 透出扩展明细;trace 记录本轮扩展表与经由
      关系(复用 observability 既有通道,零新采集)
- [x] 2.7 apps/web 知识管理页探针结果展示扩展明细(SHOULD,无契约变更)(2026-10-14 补勾:已由 097 召回试跑面板兑现,关系扩展明细在案)
      **阻塞(2026-10-05)**:任务前提不成立——web 前端不存在「探针结果展示区」,
      `RagPage.tsx` 无任何 recall-probe 消费(064 建端点时即 API-only,全前端
      仅生成类型里出现该端点)。完成本项须从零新建探针面板(问句/数据源输入 +
      三路命中 + 闸门 verdict + 扩展明细渲染),超出本项「展示扩展明细」的字面
      范围,按执行纪律不臆测扩界;待 owner 裁决是否单独立项补建探针 UI。
      **验证**:1.1–1.8 转绿;`make api-check` 绿(覆盖率 fail_under=70 不降)

## 3. 契约、文档与评估

- [x] 3.1 `make contracts` 跑一遍,确认 `contracts/openapi.json` 零漂移
      (试跑响应为既有 `dict[str, object]` 信封);若有漂移,提交产物(ADR-005)
- [x] 3.2 全量评估跑一轮(`pytest evals -m full`),随 096 归因记录
      SELECT/JOIN 类计数与 Execution Match 变化,写入 `evals/README.md`
      (作为本变更的收益证据;无历史基线则记为「首轮基线」)
      **实测(2026-10-05)**:同指纹 f05c8028 开/关两轮——关=宽松 50/50、严格
      35/50、表命中 48/50(与 08-30 基线逐项一致,关停零回归);开=宽松 50/50、
      严格 37/50(+4pp)、表命中 49/50(+1)。两轮宽松均满分,失败归因计数两侧
      皆空(无失败样本);基线行与注记已入 `evals/README.md`。

## 4. 收尾

- [x] 4.1  Constitution 自检:新增代码无框架 import 进领域层
      (`make api-check` 的 lint-imports 已守护,确认无新增违例)
      (lint-imports Success,206 文件零违例;rag/models 纯函数与
      semantic/ports 仅标准库,service 零 fastapi import)
- [x] 4.2 `make check` 全量绿 + 手工冒烟:上传表说明 → 试跑端点看扩展 →
      真实问答看 JOIN 生成
      (五段独立跑全绿:api-check 1123 passed/覆盖率 73.78%、agent 459、
       liannu 22、web、contracts-check EXIT 0。冒烟 sakila 租户:配
       customer→rental 关系 → recall-probe 透出扩展明细(customer 不在主召回
       、经关系一跳补入)→ 真实问答生成 `customer INNER JOIN rental`,
       verdict=PASS 结果正确。评估栈进程与一次性账号 eval096 已清理)
