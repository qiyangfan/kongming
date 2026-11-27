# Tasks

## 1. 控制面语音状态机

- [x] 1.1 补回归测试:工具参数缺失/畸形时回退听写原话、无听写保持拒绝、
      单值结果不追加屏幕提示、多行结果仍追加、断句阈值配置;`cd apps/api &&
      uv run pytest tests/test_voice_state_machine.py -q` 通过(28 passed)
- [x] 1.2 在状态机缓存听写事件,收到说话开始或成功发起问答后清空;验证工具
      参数 JSON 解析失败、非对象、缺 `question` 三类输入均走同一兜底
- [x] 1.3 结果回填按 `rows` 行数决定是否追加「详细数据已显示在屏幕上」;
      单值/单行结果不追加,多行结果保持原行为
- [x] 1.4 收紧会话提示词为每次语音输入都必须调用 `ask_kongming`;断句阈值
      设为 0.4,静默下限保持 2000ms;更新注册表断言并通过测试

## 2. 网页通话交互

- [x] 2.1 补回归测试:连接中挂断后再次拨号可以重新申请麦克风;旧拨号迟到
      完成不能清掉新拨号的锁;定向 vitest 通过(9 passed)
- [x] 2.2 拨号锁按请求序号释放:挂断立即释放当前锁,旧 `finally` 只在序号
      匹配时释放;验证重复点击与新拨号互不阻塞

## 3. 规格与验收

- [x] 3.1 补 `voice-call` 规格增量:听写回退、单值结果播报口径、建立中挂断
      后重拨;`openspec validate fix-voice-turn-resilience --strict` 通过
- [x] 3.2 `make api-check` 通过(1928 passed, 10 skipped, 覆盖率 78.02%)
- [x] 3.3 `make web-check` 通过(typecheck/lint/format/build/size + 256 tests
      + 3 e2e)

## Workflow follow-up

- 归档本变更并在归档后重跑 `openspec validate --all --strict`。
- 重写 `docs/HANDOFF.md` 的当前状态、下一步与验收记录。
- 由用户用真实语音复测播报结束后新问题、挂断后重拨、短句打断、单值播报与
  结果表滚动;未复测前不把这几项写成已验收。
