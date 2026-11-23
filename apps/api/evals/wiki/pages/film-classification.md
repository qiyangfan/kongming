---
page_id: film-classification
title: 影片分类
version: 1
sources:
- doc: 影片分类规范.md
  doc_sha256: c978774682eaceea4ac4611c013544a8d24d231d73186b3647f4e55d87475c93
facts:
- id: k1
  doc: 影片分类规范.md
  parent_seq: 0
  snippet: 普通级对所有会员开放
  parent_sha256: 4edd3d1104ac60d8c6dc20126dc888bd07a313f16c28528e0d60e1439c785ca8
- id: k2
  doc: 影片分类规范.md
  parent_seq: 0
  snippet: 家长指导级影片仅向成年会员开放,租借时由柜台核验证件
  parent_sha256: 4edd3d1104ac60d8c6dc20126dc888bd07a313f16c28528e0d60e1439c785ca8
- id: k3
  doc: 影片分类规范.md
  parent_seq: 2
  snippet: 每月初运营部按发行方提供的新片目录更新分类标签
  parent_sha256: 3331804b06b5ccc5d3bc808822a9b7b9644b1960ec61bcec4dc835224f8c18ae
- id: k4
  doc: 影片分类规范.md
  parent_seq: 2
  snippet: 分类变更不影响已租出影片的当次租期与计费
  parent_sha256: 3331804b06b5ccc5d3bc808822a9b7b9644b1960ec61bcec4dc835224f8c18ae
review:
  status: approved
  reviewer: qoder-agent(owner 授权代理)
  reviewed_at: 2026-11-23
  method: 逐条语义核对 + 机器校验(片段唯一/父块摘要/正文标记)
  delegation: 会话内 owner 明确授权补页重评
  minutes: 5
---
影片按内容分为普通级与家长指导级:普通级对所有会员开放[[k1]];家长指导级影片仅向成年会员开放,租借时由柜台核验证件[[k2]]。每月初运营部按发行方提供的新片目录更新分类标签[[k3]];分类变更不影响已租出影片的当次租期与计费[[k4]]。
