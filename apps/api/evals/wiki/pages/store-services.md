---
page_id: store-services
title: 门店服务与押金
version: 1
sources:
- doc: 门店服务规则.md
  doc_sha256: 2bcf80b4afcef8c820a5aed07a24449981d2679f11d62707417858f665dabc00
facts:
- id: t1
  doc: 门店服务规则.md
  parent_seq: 0
  snippet: 门店每日 10:00 至 22:00 营业,法定节假日照常营业
  parent_sha256: dc4ea9c4d3c5f593bf487e6eebab8973a1eb71ede35b2aff6445ddc264d65560
- id: t2
  doc: 门店服务规则.md
  parent_seq: 0
  snippet: 闭店前 30 分钟停止办理新租借,影片归还不受时间限制
  parent_sha256: dc4ea9c4d3c5f593bf487e6eebab8973a1eb71ede35b2aff6445ddc264d65560
- id: t3
  doc: 门店服务规则.md
  parent_seq: 2
  snippet: 非金卡会员租借每部影片押金 30 元,归还验收无误后当场退还
  parent_sha256: f8ae6a1594004e85196a7589ccabb3f78ccfcbffd0732dc102fa756b6678101a
- id: t4
  doc: 门店服务规则.md
  parent_seq: 2
  snippet: 押金不抵扣逾期费,逾期费须另行结清
  parent_sha256: f8ae6a1594004e85196a7589ccabb3f78ccfcbffd0732dc102fa756b6678101a
review:
  status: approved
  reviewer: qoder-agent(owner 授权代理)
  reviewed_at: 2026-11-23
  method: 逐条语义核对 + 机器校验(片段唯一/父块摘要/正文标记)
  delegation: 会话内 owner 明确授权补页重评
  minutes: 5
---
门店每日 10:00 至 22:00 营业,法定节假日照常营业[[t1]];闭店前 30 分钟停止办理新租借,影片归还不受时间限制[[t2]]。非金卡会员租借每部影片押金 30 元,归还验收无误后当场退还[[t3]];押金不抵扣逾期费,逾期费须另行结清[[t4]]。
