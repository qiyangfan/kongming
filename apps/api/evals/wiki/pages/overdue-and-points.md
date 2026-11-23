---
page_id: overdue-and-points
title: 逾期费用与积分
version: 1
sources:
- doc: 租赁规则.md
  doc_sha256: ea69a2a5dd412412bb724d2319055556987b8f1aaa41c99d9043acc30165247f
- doc: 积分规则.md
  doc_sha256: c9631e9d16059660543c9726f2806807636596437102e3b8168d5ed9d91f3afb
facts:
- id: g1
  doc: 租赁规则.md
  parent_seq: 0
  snippet: 所有影片基础租期为 7 天,可在到期前办理一次续借,续借延长 3 天
  parent_sha256: 34ed8f4bc7842b94368f4cc01186905495158cd41fe71ea10a5611d8a6ccb9b6
- id: g2
  doc: 租赁规则.md
  parent_seq: 2
  snippet: 逾期归还按每日每部 0.5 元计收逾期费,单部影片逾期费上限为其租金的三倍
  parent_sha256: 5a35c4cbe4c26865a3786de50c0fd340dcb0c2fb51c3aba0e8781eee6878a66d
- id: g3
  doc: 租赁规则.md
  parent_seq: 2
  snippet: 逾期超过 30 天按遗失赔偿处理,赔偿金额为影片定价
  parent_sha256: 5a35c4cbe4c26865a3786de50c0fd340dcb0c2fb51c3aba0e8781eee6878a66d
- id: g4
  doc: 积分规则.md
  parent_seq: 0
  snippet: 每成功租借一部影片记 10 积分,归还验收通过后入账
  parent_sha256: ca4ff26037bdd743e3060b37028dd9e3951d1e4ea967951c150736866ca17fe4
- id: g5
  doc: 积分规则.md
  parent_seq: 0
  snippet: 逾期归还的租借减半计分,办理遗失赔偿的租借不计分
  parent_sha256: ca4ff26037bdd743e3060b37028dd9e3951d1e4ea967951c150736866ca17fe4
- id: g6
  doc: 积分规则.md
  parent_seq: 2
  snippet: 100 积分可兑换一张免逾期费券,或抵扣 5 元租金
  parent_sha256: 1ba71708e3ef75c3b16cb9b3045911b0779076ff000b84c19c4ffdebc3a32778
- id: g7
  doc: 积分规则.md
  parent_seq: 2
  snippet: 积分有效期 24 个月,过期清零,兑换时不足整额部分不找零
  parent_sha256: 1ba71708e3ef75c3b16cb9b3045911b0779076ff000b84c19c4ffdebc3a32778
review:
  status: approved
  reviewer: qoder-agent(owner 授权代理)
  reviewed_at: 2026-11-23
  method: 逐条语义核对 + 机器校验(片段唯一/父块摘要/正文标记)
  delegation: 会话内 owner 明确授权代理签核
  minutes: 30
---
# 逾期费用与积分

基础租期为 7 天，到期前可办理一次续借、延长 3 天 [[g1]]。逾期归还每日每部 0.5 元，单部逾期费上限为租金三倍 [[g2]]；逾期超 30 天按遗失赔偿处理，金额为影片定价 [[g3]]。

积分获取：成功租借每部记 10 分，归还验收后入账 [[g4]]；逾期归还减半计分，遗失赔偿不计分 [[g5]]。

积分使用：100 分可兑换免逾期费券或抵扣 5 元租金 [[g6]]；积分有效期 24 个月，过期清零，兑换时不足整额不找零 [[g7]]。
