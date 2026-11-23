---
page_id: group-rental
title: 团体租借
version: 1
sources:
- doc: 团体租借规范.md
  doc_sha256: f961a4d8334404a85c621086fc9f48b5e01f161d4a2199898fe22c804576f5e1
facts:
- id: r1
  doc: 团体租借规范.md
  parent_seq: 0
  snippet: 一次租借十部以上影片按团体租借办理,享总价九五折
  parent_sha256: 154f453a7ef509b555d7affc24f36b38e8f3a40d1d0ac6f392d9ad3bf679e3e1
- id: r2
  doc: 团体租借规范.md
  parent_seq: 0
  snippet: 团体租借不参与积分累计
  parent_sha256: 154f453a7ef509b555d7affc24f36b38e8f3a40d1d0ac6f392d9ad3bf679e3e1
- id: r3
  doc: 团体租借规范.md
  parent_seq: 0
  snippet: 押金按每部影片 30 元另行收取
  parent_sha256: 154f453a7ef509b555d7affc24f36b38e8f3a40d1d0ac6f392d9ad3bf679e3e1
- id: r4
  doc: 团体租借规范.md
  parent_seq: 2
  snippet: 团体租借须一次性整单归还,不接受部分归还
  parent_sha256: 50a39f5cd224ca28f3e34598738002fe5a79c33ac3d5c7f8fb26721914fe9ad5
- id: r5
  doc: 团体租借规范.md
  parent_seq: 2
  snippet: 团体订单的逾期费按整单统一结算,不拆分到单部影片
  parent_sha256: 50a39f5cd224ca28f3e34598738002fe5a79c33ac3d5c7f8fb26721914fe9ad5
review:
  status: approved
  reviewer: qoder-agent(owner 授权代理)
  reviewed_at: 2026-11-23
  method: 逐条语义核对 + 机器校验(片段唯一/父块摘要/正文标记)
  delegation: 会话内 owner 明确授权补页重评
  minutes: 5
---
一次租借十部以上影片按团体租借办理,享总价九五折[[r1]];团体租借不参与积分累计[[r2]],押金按每部影片 30 元另行收取[[r3]]。团体租借须一次性整单归还,不接受部分归还[[r4]];团体订单的逾期费按整单统一结算,不拆分到单部影片[[r5]]。
