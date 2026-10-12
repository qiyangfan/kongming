# Design

## Context

030 已落地图片问数(vision 描述注入),语音输入是多模态最后一块。
约束:ADR-002(api 面零 LLM 凭证;ASR 云 API 的 key 属同类凭证,muniu 桥
只有 LLM/embedding 出口,扩 ASR=跨项目变更);轻部署哲学(缺省零成本)。

## Goals / Non-Goals

**Goals:**

- 对麦克风说话→文字进输入框,演示闭环;本地推理零凭证
- 未配置环境零成本(依赖在但不加载模型、端点明示 503)
- 即转即弃,不碰附件持久化语义

**Non-Goals:**

- 不做实时流式转写(按住说话→松开转写,一次成型;流式后续再说)
- 不做说话人分离/热词定制
- 不经 muniu 桥/云 ASR(跨项目变更+凭证面,均否)
- 不建 sys_attachment 行(转写产物=问题文本,非附件内容)

## Decisions

**D1 引擎=本地 faster-whisper(small int8)。**中文效果可用、CPU 实时率
可接受(15s 音频秒级转完)、PyAV 自带解码(webm/opus 直读)。备选对比:
dashscope paraformer(准度更高但要 key 过信任边界或改桥,否);
浏览器 Web Speech API(零后端但国内演示环境连不上谷歌服务,不可靠,否);
whisper.cpp(要系统编译工具链,本机无,否)。模型 small int8 经配置
`KM_STT_MODEL` 可换(base/medium 同接口)。

**D2 可选启用:探测式,不加开关。**依赖随 main 安装(uv 锁定,linux/
macOS 均有 wheel);模型文件惰性探测——进程内首次请求时加载并缓存
(加载失败/模型缺失→503 `STT_UNAVAILABLE` 明示配方)。不加
KM_STT_ENABLED 开关(106 裁决:同类切换不做仪式;模型在=启用,不在=503,
状态自明)。单例引擎(asyncio.Lock 防并发双载)。

**D3 域形态:独立小域 `kongming/stt/`,router→service→engine Protocol。**
引擎接口反转(WhisperEngine Protocol:transcribe(bytes)→str)——单测
mock 注入,真实引擎仅组合根装配;模型加载/解码细节不出域。

**D4 API 形态:POST /chat/stt(multipart file)。**复用 chat:execute
权限(问数输入能力族,零新权限点);上限 15MB(request body 层面);
返回 `{text}`;空转写结果返回空 text(前端提示未识别,不假装成功)。
语音不落盘、无审计正文(操作审计记录 action 即可,不含音频/转写内容——
与附件审计同哲学,内容不进日志)。

**D5 前端交互。**输入框麦克风按钮:点击开始录音(按钮态变色+计时),
再点停止→自动上传→转写文本**填入输入框**(不直接发送——用户可修正,
误触也不产生垃圾问答);Escape 取消。MediaRecorder 优先 webm/opus
(Chrome/Edge/Safari 现代版均支持)。

## Risks / Trade-offs

- **模型下载(~480MB)是首次使用前置**:HANDOFF 记一条预下载命令;
  未下载时端点 503 明示,不静默半可用。
- **专业术语转写偏差**(表名/字段名):转写进输入框由用户确认修正,
  不直接执行——该取舍同时是隐私与准确率的双保险。
- **ctranslate2 依赖体积**(main 依赖):纯 wheel 无系统依赖,CI 可装;
  换取零凭证本地推理的部署简单性。
