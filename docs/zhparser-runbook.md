# zhparser 启用 runbook(文档全文分词)

状态(2026-11-23):配方已本地验证,按本语料实测**不启用**。复评条件=真实或更大
语料上出现词面召回缺口,按本 runbook 第 5 节流程重新 A/B。

## 1. 已验证的证据

- 分词差异:同一句「会员卡遗失应立即联系门店挂失,挂失后原卡即时失效」,
  `simple` 输出 2 个短语级 token;`zhparser` 输出 9 个词级 token(会员卡/挂失/失效/门店…)。
- A/B(retrieval-eval 28 题,15 文档 36 父块,ts_rank,精排关):
  `simple` macro Recall@5 1.000 → `zhparser` 0.929(4 题丢跳,full_hop 1.000→0.857)。
  切细后常用词命中变多,ts_rank + RRF 下融合池被噪声稀释。
- `zhparser` + BM25(80 题):两路逐题一致,BM25 未能修复上述噪声,无改善。
- 结论:本语料饱和(基线 1.000),切换只会引入退化;不改变部署默认 `simple`。

## 2. PG 镜像配方(已验证)

基础镜像 `pgvector/pgvector:pg18`;需 SCWS 1.2.3 与 zhparser(scws 默认装到
/usr/local)。生产采用时把 zhparser 固定到具体提交号:

```dockerfile
FROM pgvector/pgvector:pg18
RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc make libc6-dev bzip2 postgresql-server-dev-18 \
 && curl -sL http://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2 -o /tmp/scws.tar.bz2 \
 && tar xjf /tmp/scws.tar.bz2 -C /tmp && cd /tmp/scws-1.2.3 \
 && ./configure >/dev/null && make >/dev/null && make install >/dev/null \
 && curl -sL https://codeload.github.com/amutu/zhparser/tar.gz/refs/heads/master -o /tmp/zhp.tar.gz \
 && mkdir /tmp/zhparser && tar xzf /tmp/zhp.tar.gz -C /tmp/zhparser --strip-components=1 \
 && cd /tmp/zhparser && SCWS_HOME=/usr/local make >/dev/null && SCWS_HOME=/usr/local make install >/dev/null \
 && rm -rf /tmp/scws* /tmp/zhparser* /var/lib/apt/lists/*
```

注意:官方 postgres 镜像不带服务端头文件,必须安装 `postgresql-server-dev-18`,
否则 `postgres.h` 缺失导致编译失败。

## 3. 初始化

```sql
CREATE EXTENSION IF NOT EXISTS zhparser;
CREATE TEXT SEARCH CONFIGURATION zhparser (PARSER = zhparser);
ALTER TEXT SEARCH CONFIGURATION zhparser ADD MAPPING FOR n,v,a,i,e,l,j WITH simple;
```

自建实例还需先建只读角色(见 `infra/pg-init/001-readonly-role.sql`),否则 0001
迁移的 `GRANT ... TO km_readonly` 失败。

## 4. 切换与回滚

1. 配置 `KM_RAG_FULLTEXT_TS_CONFIG=zhparser`(启动时校验 `pg_ts_config` 存在,缺失拒启)。
2. **存量重算 tsv**(切换前必须做,否则查询分词与存量 token 不一致):

   ```sql
   UPDATE rag.document_chunks c
      SET tsv = to_tsvector('zhparser', d.doc_name || E'\n' || c.content)
     FROM rag.documents d
    WHERE d.id = c.document_id;
   ```

   表达式须与写入侧 `index_text`(文档名+块正文)一致,漏掉 `doc_name`
   会让按文档名的词面召回失效。也可用维护脚本一并重算(会连带重算向量):
   `KM_RAG_FULLTEXT_TS_CONFIG=zhparser uv run python scripts/rebuild_embeddings.py
   --table document_chunks`。

   表说明表 `rag.table_descriptions.tsv` 由写入端拼接(表名/别名/注释/列说明)生成,
   重算须按 `kongming/rag/repository.py` 的同款表达式或重新内省,不建议手写 SQL。
3. 回滚=切回 `simple` 并同样重算,双向都无 schema 迁移。

## 5. 全文门槛复标(切换时必须)

`KM_RAG_GATE_FULLTEXT_TOP1/MEAN` 当前为 0(不裁剪)。切换分词后分数分布变化,
须用评测分布重标:正例=retrieval-eval 命中题,负例=语料外问题;取两群分数
分离点作为门槛。本次未执行——本语料下 zhparser 无收益,不进入切换。

## 6. 验证记录

专用实例(`localhost:5434`,镜像 `km-pg-zhparser:dev`)+ alembic 0067;语料经
评测上传;A/B 命令与结果存于评测报告与 `apps/api/evals/README.md` 基线表
(2026-11-23「zhparser」行)。
