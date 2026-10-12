# Proposal

## Why

求职项目的演示完整性:图片问数已有(030 vision 链路),语音问数是
多模态输入的最后一块——对着麦克风说话出图表,演示冲击力直接;同时
「本地语音模型推理(api 面零凭证形态)」是新的学习点。2026-10-12 勘误
裁决:此前「无消费场景」的否决在求职项目语境不成立(无需求方,场景等
不来),翻案立项。

## What Changes

- 新增语音转写端点 `POST /chat/stt`(multipart 音频,复用 chat:execute
  权限点):音频 → 本地 faster-whisper 转写 → 返回 `{text}`。
  **即转即弃**:音频不落盘、不建附件行(与附件「一次性消费」同隐私哲学,
  转写产物=问题文本,非附件内容,语义不同故不走附件链路)。
- 推理引擎=本地 faster-whisper(small int8,中文可用,PyAV 解码容器
  格式 webm/opus/mp3/wav/m4a):api 面运行,零凭证、零外部调用,不触
  ADR-002 信任边界(dashscope paraformer 需经桥=跨项目变更,否)。
- 可选启用形态:模型在位即可用;模型/依赖缺失时端点 503 明示未启用
  (缺省零成本部署哲学;模型下载配方进 HANDOFF)。
- 前端:聊天输入框麦克风按钮(MediaRecorder 采集 → 停止 → 上传 →
  转写文本填入输入框,用户可修正后发送;录音中可视状态+取消)。
- 限制:单次音频 ≤15MB 且转写文本受既有问题长度上限约束;权限复用
  chat:execute,零新权限点。

## Capabilities

### New Capabilities

(无)

### Modified Capabilities

- `multimodal-input`:新增「语音转写输入」requirement(端点契约/即转
  即弃/可选启用形态/限制)——与图片附件同族(cohesive boundary:多模态
  输入)。

## Impact

- api:`kongming/stt/`(router/service/ports/engine,新小域)+ main 挂载
  + pyproject main 依赖加 `faster-whisper`。
- web:聊天输入组件加录音交互;`api-types.gen.ts` 经 make contracts 再生。
- 测试:端点单测(mock 引擎 Protocol;503/权限/超限形态)+ 真实转写
  测试(模型在位才跑,缺模型 skip)。
- 契约:openapi 再生;无 schema 变更(零持久化)。
