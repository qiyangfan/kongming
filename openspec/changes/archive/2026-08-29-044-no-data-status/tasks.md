# Tasks: 044-no-data-status

## 1. 测试先行

- [x] 1.1 api:agent verdict=NO_DATA → _finalize 产出 status=NO_DATA、
  summary=说明文案、error=None、产物卡空、落库行 status=NO_DATA
- [x] 1.2 api:既有三态映射回归(逐字不变)
- [x] 1.3 迁移:0028 up 后 CHECK 接受 NO_DATA(dev+TEST_DB 实证;
  down 语义对称,遇存量 NO_DATA 行失败属 Alembic 常规行为)

## 2. 实现

- [x] 2.1 agent main.py:verdict 判别链加 no_data 档(判别置最前——自愈轮
  短路时残留上轮 ERROR execution 不得遮蔽最终形态)+ QueryOut 注释
- [x] 2.2 迁移 0028:chat_message status CHECK 加 NO_DATA(drop+re-add)
- [x] 2.3 chat/service.py:_finalize 四态映射 + NO_DATA 分支
  (summary 承载文案/error=None/跳过产物派生)
- [x] 2.4 web ChatPage 映射扩档 + assistant-card NO_DATA 中性分支
  (信息图标+summary;反馈区保留)

## 3. 门禁与契约

- [x] 3.1 make contracts(verdict/status 类型同步;status 宽 string 既有形态)
- [x] 3.2 make check 全绿(api 861+agent 190+web tsc/eslint)
- [x] 3.3 端到端实测:default 源同题 → verdict=NO_DATA 中性文案零执行
  (API 层;GUI 渲染由 tsc 类型+分支保证,浏览器眼见为实留用户随手确认)

## 4. 评审循环(/goal R0+R1)

- [x] 4.1 文案漂移实锤修复:同一说明文案全角/半角两形并存——考证仓库 UI
  文案惯例为半角(agent 既有「LLM 服务暂不可用,请稍后重试」亦半角,
  prompt 资产全角纪律仅管 LLM 文本),五处统一半角并逐字节终验
- [x] 4.2 剔除候选七项(逐条溯源):api-types 宽 string 既有形态/
  toSyntheticMessage 字段齐全/service 无三态残留/evals 零 verdict 消费/
  FeedbackPage-ChatPage 无关消费/迁移 downgrade 常规行为/自愈轮残留无静默丢数
- [x] 4.3 流式链路 verdict 透传闭合(result 帧=ChatQueryOut 全量,
  toSyntheticMessage 唯一合成点覆盖流式与非流式)
