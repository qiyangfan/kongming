# Tasks

## 1. api:stt 小域

- [x] 1.1 `kongming/stt/`:ports.py(WhisperEngine Protocol)+ engine.py(faster-whisper 懒加载单例,Lock 防并发双载;PyAV 解码)+ service.py(转写用例:大小/时长校验→引擎→text)+ router.py(POST /chat/stt,multipart,chat:execute,15MB 上限,503 STT_UNAVAILABLE 形态)
- [x] 1.2 main 挂载 router;pyproject main 依赖加 faster-whisper;uv lock 同步

## 2. 测试

- [x] 2.1 端点单测(mock 引擎:成功转写/空结果/引擎缺失 503/超 15MB/无权限 403);操作审计 action 断言(正文不含转写内容)
- [x] 2.2 真实转写测试(模型在位才跑,缺失 skip):合成/样本音频 → 非空文本

## 3. web 前端

- [x] 3.1 聊天输入框麦克风按钮:MediaRecorder 录音态(变色+计时/Escape 取消)→ 停止上传 → 文本填入输入框;`api-types.gen.ts` 经 make contracts 再生

## 4. 收尾

- [x] 4.1 门禁(api ruff/mypy/pytest + pyright 全仓 + web typecheck/lint);HANDOFF 补模型预下载配方
- [x] 4.2 变更归档(openspec archive)
