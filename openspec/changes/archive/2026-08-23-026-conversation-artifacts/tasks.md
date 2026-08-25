# 026 conversation-artifacts 实施任务

## 1. 派生与契约

- [x] 1.1 derive_artifacts 纯函数(chat/models)+单测(三卡/空数组/部分要素)
- [x] 1.2 ChatOutcome+result_json+_to_message 三路径挂接;make contracts

## 2. rerun 端点(测试先行:安全类)

- [x] 2.1 失败测试:归属 404/写操作拦截/保留表拦截/再执行新消息落库/护栏过检
- [x] 2.2 实现 POST /chat/artifacts/rerun(归属校验+internal 执行复用+新消息)

## 3. web 渲染器

- [x] 3.1 assistant-card artifact 渲染(sql 可编辑+重执行按钮/chart 调参重绘/document 复制)
- [x] 3.2 rerun 调 api + invalidate;老消息兼容形态

## 4. 验证与收尾

- [x] 4.1 016 评估 smoke 复验;真 LLM 冒烟(问答→编辑 SQL 再执行→新消息;chart 调参;刷新回显)
- [x] 4.2 R1 独立评审 + 修复清零 + make check 全绿
- [x] 4.3 HANDOFF + 挂账 + 归档
