# 设计:spec-strict-wording-debt

## D1 只动措辞与结构,不动行为

strict 失败三类的定性与修法:

1. **关键词缺收(WARNING 级,44 条)**:正文已是规范陈述,只是没有 RFC 2119 关键词。
   修法=在既有规范句上升格(「不改写」→「SHALL NOT 改写」、「必须」→「MUST」),
   关键词落在语义本位上,不做全文重写;否定式优先用 `SHALL NOT` 而不是「不得」。
2. **缺 Scenario(ERROR 级,chat 两条)**:两条需求正文(076/068 落地时写入)本身
   已完整含 SHALL 语义,只是没有场景块。修法=从正文逐句派生两个场景/条,不引入
   正文没有的新断言(防止借机扩权)。
3. **缺 Purpose(ERROR 级,runtime-resilience)**:根因是 074 归档时把 delta 文件
   (`# … spec delta` + `## ADDED Requirements`)原样放进了主规格目录。修法=转成
   主规格规范形态(`# runtime-resilience 能力域` + `## Purpose` + `## Requirements`),
   需求与场景原文逐字保留;Purpose 只写域定位,不复制需求内容。

## D2 手工同步,不写 delta 工件

修正直接落主规格(apply 期手工同步),change 不携带 specs/ delta,归档走 `--skip-specs`。
理由:纯措辞/结构治理不构成行为变更,12 域 × MODIFIED delta 的成本全部是「同一文本
双份维护」的漂移风险;本仓纪律(2026-09-28 实证)本就以 apply 期手工同步为 specs 終态。

## D3 冻结边界

债项清单=2026-10-02 validate 输出的 12 域;修完后逐域复验,不新增评审面。INFO 级
「超长文本」不处理(不影响 strict 判定;拆需求属行为评审,违冻结裁决)。
