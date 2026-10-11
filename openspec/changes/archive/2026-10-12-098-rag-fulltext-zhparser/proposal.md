# Proposal

## Why

全文检索路的分词配置硬编码 `simple`:中文问句在 simple 配置下整句成 token,
对中文表说明几乎必然零命中(「查询所有客户」无法命中含「客户」的表说明),
中文场景全文路形同虚设,召回全压在向量路单腿上。产品待办已登记「中文全文
zhparser」;中文数据问答是首发王牌场景,全文路应成为中文命中的第二支撑腿
(精确表名/别名词面修正排序正是全文路的定位)。

## What Changes

- 全文检索文本配置部署级可配(缺省 `simple`,零回归):写入(to_tsvector)与
  检索(plainto_tsquery/ts_rank)同配置单源;配置值为白名单枚举
  (`simple` / `zhparser`),MUST NOT 透传任意标识符。
- 配置目标不可用(pg_ts_config 不存在,如 zhparser 扩展未安装)SHALL fail-closed
  拒启并明示缺项——检索配置降级会与存量 tsv 错配,不存在安全的降级形态。
- 配置变更后存量全文向量 SHALL 经既有 reindex/重扫链路重算生效;检索上下文
  缓存 SHALL 不跨配置复用(避免新旧配置静默混用窗口)。
- rag 表说明全文路与 document-rag 文档全文检索面同配置统一(一处配置单源,
  同库不分词两态)。
- 文档(README/HANDOFF)补 zhparser 环境安装配方(pg18 localenv)。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `rag`:三路混合召回 requirement 中的全文路——分词配置部署级可配、写读同配置、
  不可用拒启、配置变更生效路径与缓存边界。
- `document-rag`:文档全文检索面同配置单源(与 rag 全文路共用同一部署配置,
  不出现第二套分词语义)。

## Impact

- `apps/api`:rag/repository.py 与 rag/documents_repo.py 全文 SQL 的配置名
  参数化(白名单校验后绑定);配置读取与启动自检(config 单点);检索缓存键
  或版本纳入配置维度。
- schema:零变更(tsv 列形态不变,应用层维护;无新表新列)。
- 部署环境:启用 zhparser 需 pg 扩展安装(localenv 配方进文档);未配置保持
  `simple` 零回归。
- contracts:零变更(无端点变化)。
