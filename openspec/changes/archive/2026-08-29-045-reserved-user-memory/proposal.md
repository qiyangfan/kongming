# Feat: 045 保留表收编 user_memory(039 评审上报项处置)

## Why

039 评审期上报(2026-08-29 定性):`public.user_memory`(022 用户长期记忆表)
不在 SQL 防火墙保留表清单,而 datascope SELF 域按 `create_by` 列注入行级过滤,
该表归属列名为 `user_id` 识别不到 → SKIP 安全降级不过滤 → **default 源可 SQL
读全库用户记忆(跨租户、跨用户)**。

这不是 datascope 的缺陷,是保留表防线的清单遗漏:

- spec(sql-execution「控制面保留表拒读」背景)已明文立约:data_scope 对无
  create_by 列的表安全降级不过滤,**不构成防线**——防线是保留表拒读(005 评审
  实证立约:「LLM 可猜中表名直读口令哈希/数据源密文/他人对话」);
- public schema 平台表全景清点:sys_ 前缀 33 张 + chat_ 前缀 3 张(前缀规则
  覆盖)、rag.* 5 张(schema 收编)、alembic_version/user_identities(精确名
  收编)——**user_memory 是唯一漏网**(022 建表时未同步收编)。

风险实证:记忆表含全租户用户私有记忆(content 全文+embedding),经问答链路
一条 `SELECT * FROM public.user_memory` 即整体透出;且 sql-execution spec 的
schema 边界白名单本就包含 public(业务源形态),通路真实。

## What Changes

- `RESERVED_TABLE_NAMES` 收编 `user_memory`(精确名,小写折叠语义):
  裸名/public 限定名/CTE 遮蔽/JOIN 夹带全形态在防火墙层 BLOCKED,先于
  datascope 到达,降级通路闭合;
- 测试先行:保留表拦截用例补四形态;datascope 既有 SELF 降级语义零改动
  (业务表无 create_by 的放行是正确语义,不动);
- spec delta:sql-execution「控制面保留表拒读」精确名清单同步。

## 决策摘要(为什么收编,而不是别的)

- **否决「datascope 识别 user_id 列」**:`user_id` 是业务库极常见列名(订单表
  客户 ID 等),纳入 SELF 归属列识别集必然误伤业务表语义(SELF 用户查订单被
  强制注入 user_id=平台用户 ID,查询被破坏);且识别集永远补不全(fail-open
  对漏识别表恒开放),而保留表清单是有限闭集。
- **否决「挪 schema / 改名 sys_user_memory」**:架构更纯但动存量表+memory 域
  全部引用,收益仅是防未来漏收编——由建表纪律达成即可(新平台表必须 sys_/
  chat_ 前缀,或建表变更同步收编并过防火墙测试),不为存量翻新。
- **ALL 域一并拒读**:管理员看用户记忆不是 NL2SQL 数据面的场景(隐私面);
  记忆的正当通道=022 召回注入 memory_hint + /memory API(自有鉴权),与
  数据面无关。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `sql-execution`:保留表精确名清单增补 `user_memory`(022 用户长期记忆表,
  归属列 user_id 非 create_by,datascope 降级不构成防线)。

## Non-goals

- datascope SKIP 降级语义重构:业务表无 create_by/dept_id 时放行是立约正确
  语义(SELF/DEPT 用户查业务表必须可用),不动;
- 记忆运营/审计控制面(管理员查看记忆的管理端点):无需求触发,不预建;
- user_memory 表结构与归属列形态:022 语义正当(控制面自鉴权通道),不改。
