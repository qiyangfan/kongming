---
page_id: overdue-process
title: 逾期暂停与申诉
version: 1
sources:
- doc: 逾期处理流程.md
  doc_sha256: 05c9e9f21c2191da272442766bdc024ba1c645c7129df3b6701b5aaab86a4bda
facts:
- id: o1
  doc: 逾期处理流程.md
  parent_seq: 0
  snippet: 逾期费累计超过 20 元时系统自动暂停借阅资格,缴清全部逾期费后次日恢复借阅
  parent_sha256: 89175532404b49e8bcc7616bf3e6d06327dc88c59ca15fcee001ae074879f44b
- id: o2
  doc: 逾期处理流程.md
  parent_seq: 0
  snippet: 暂停期间续借申请一律驳回
  parent_sha256: 89175532404b49e8bcc7616bf3e6d06327dc88c59ca15fcee001ae074879f44b
- id: o3
  doc: 逾期处理流程.md
  parent_seq: 2
  snippet: 对逾期费有异议的会员可在扣费后 7 天内发起申诉,客服核实后多收部分原路退回
  parent_sha256: 5faea2be7b1dba742e62534ddfec4d0c22a68d3219163e04510766f816ae7c7c
- id: o4
  doc: 逾期处理流程.md
  parent_seq: 2
  snippet: 申诉期间借阅资格不受影响
  parent_sha256: 5faea2be7b1dba742e62534ddfec4d0c22a68d3219163e04510766f816ae7c7c
review:
  status: approved
  reviewer: qoder-agent(owner 授权代理)
  reviewed_at: 2026-11-23
  method: 逐条语义核对 + 机器校验(片段唯一/父块摘要/正文标记)
  delegation: 会话内 owner 明确授权补页重评
  minutes: 5
---
逾期费累计超过 20 元时系统自动暂停借阅资格,缴清全部逾期费后次日恢复借阅[[o1]];暂停期间续借申请一律驳回[[o2]]。对逾期费有异议的会员可在扣费后 7 天内发起申诉,客服核实后多收部分原路退回[[o3]];申诉期间借阅资格不受影响[[o4]]。
