# 095 设计记录:关系感知召回扩展

## 决策一:扩展在 recall 内,不在 retrieve 端点后

扩展放在 `RagService.recall`(RRF 融合后、`fetch_entries` 前),而不是
`/internal/retrieve` 端点内对 `ctx` 做事后补充。两个理由:

1. **缓存一致性**:recall 的 ctx 缓存(`km:ragctx:*`)是召回输出的唯一缓存点。
   在端点后扩展,缓存命中路径拿不到扩展结果——同一问句首轮有扩展、命中缓存后
   又没有,行为随缓存状态漂移。
2. **试跑同源**:`recall_probe` 与主链路共用同一段代码,试跑看到的扩展就是
   链路的扩展(064 为闸门立下的「判定纯函数单源」同款纪律)。

代价是 recall 需要新端口(见决策二),可接受。

## 决策二:端口 Protocol + 组合根适配(宪法 Article II)

`rag/service.py` 不得 import `kongming.semantic.*`(域间直连)。故在
`rag/ports.py` 声明窄端口:

```python
class RelationExpansionPort(Protocol):
    async def related_tables(
        self, *, tenant_id: str, datasource_key: str
    ) -> list[dict[str, Any]]: ...  # [{source_table, target_table, join_keys}]
```

适配器落在 `rag/router.py` 的组合根 `get_rag_service`(与 047 `suggestion_evictor`
同款:适配层组基础设施,service 只见 Protocol)。端口未注入(=老调用方/测试)时
扩展为空,零回归。

## 决策三:缺省开启 + kill switch,不做影子

见 proposal.md Why 末节。补一条实现约束:关停判定 SHALL 早于任何扩展查询
(配置一票短路,不为关闭形态付一次 SQL)。

## 决策四:一跳 + 尾附加

- **一跳**:`related_tables` 只查「任一端在召回集内」的关系行,不迭代。
  多跳的组合爆炸与稀释风险(见 Non-goals)在此形态下不存在,且判定可被
  单元测试完整覆盖。
- **尾附加**:扩展条目追加在 `schema_hint` 末尾。这不是随意排序——080 的预算
  纪律是「证据段按预筛名次从尾部丢弃、首位豁免」,尾附加使扩展天然落在最低
  优先级:预算超限时先裁扩展,主召回分毫不动。若把扩展插在中间,等于让低置信
  上下文挤占高置信上下文的预算。

## 决策五:缓存失效归属关系写路径

召回输出新增对 `rag.table_relations` 的依赖,因此关系数据的写路径必须失效
ctx 缓存。落在 `semantic/router.py` 的写端点提交之后,经窄端口
(`RecallContextEvictor`,实现=`ContextCache`)调用既有 `evict(tenant_id,
datasource_key)`。三个写入口:introspection upsert、手工关系增删、
datasource 删除级联清理(后者 datasource 域已有级联先例,补上缓存失效)。

不做「按关系指纹细化缓存键」之类优化:TTL 600s + 精确 evict 已能把不一致
窗口压到零(写后立即失效),细粒度键只增加复杂度。

## 决策六:不引图存储

ADR-001 否决清单 + tech-stack 锁栈。`table_relations` 的关系行 + 既有
`relations_within` 形态已能表达一跳遍历;Neo4j/RDF 的查询能力在本场景
(单库、数十表、只走一跳)没有不可替代性,却引入新基础设施、新运维面、
新信任边界问题。

## 已知边界(诚实记录)

1. **关系声明质量决定扩展质量**:无 FK 且无手工关系的库,扩展恒为空(023 既定
   语义,不猜测)。扩展把这个既有短板变得更可见——「配了关系才有扩展」。
2. **`rag_expand_max` 缺省 2 是判断值,非实测值**:取小是为了把提示词稀释面
   压到最小;真实调优依据由 096 的 SELECT/JOIN 归因计数提供,不在本变更内拍。
3. **方向对称**:源侧或目标侧命中即扩展,不区分 FK 方向语义(本仓关系行本身
   不记录方向权重)。
