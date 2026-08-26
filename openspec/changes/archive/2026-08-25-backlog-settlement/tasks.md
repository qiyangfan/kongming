# Tasks:backlog-settlement

## 1. api 面:安全/正确性(A 组)

- [x] 1.1 /mcp 入站 body 闸:端点改收 Request,Content-Length 声明+实测
  双闸 1MB→413,前置于鉴权/notifications 短路/json.loads(测试:声明
  超限 413[httpx 自动带 CL,实测闸与 trigger 同范式]/畸形 JSON
  -32700/正常小 body 链路零回归;chunked 无 CL 形态的价值边界登记
  ——body 已缓冲,属应用层输入约束而非完整 DoS 防护)
- [x] 1.2 mark_dormant 收窄:UPDATE 补 user_id+tenant_id 过滤,
  mark_dormant_lazy 签名加参,service 调用方直传(测试:他人他租户
  应休眠记忆不被翻牌;本人应休眠记忆被翻牌)
- [x] 1.3 workflow 超时来源区分:engine 以 task.cancelled() 判来源,
  内源 TimeoutError 归 FAILED 带「节点内部超时」文案(测试:内源超时
  FAILED/真预算到期 TIMEOUT 两用例)
- [x] 1.4 _should_skip 收紧:仅 condition 类型依赖应用条件门(by_id
  参数);on ∈ {true,false} 校验系 029 R1-J4 既有行为(本次补测试
  on="yes" 定义期拒+spec 补立约;非 condition 前驱 bool result 不触发门)
- [x] 1.5 ReAct 轮次触顶保留部分结果:agent react/nodes.py 触顶分支
  携带 last_execution/generated_sql/stats,success 保持 False、error
  优先(测试:触顶时 rows 保留断言)
- [x] 1.6 doc_name PII 拒绝:上传入口跑 pii_* 规则集命中即 400(测试:
  身份证文件名 400/日期数字文件名不误拒)
- [x] 1.7 bot usage sink 独立短会话:_UsageSinkAdapter 改
  get_sessionmaker() 短会话(照 _BotGuardrailAdapter 范式;既有
  bot 用量落库测试回归)

## 2. api 面:资源/一致性(B 组)

- [x] 2.1 _tenant_id 收口:新建 kongming/tenant.py 顶层纯函数 helper,
  五处复制(chat/feedback repository+memory/semantic/bot router)改调;
  既有各域 403/默认租户测试全量回归
- [x] 2.2 自引用 FK 注入:semantic service+repository 双 guard 放宽为
  非空(测试:单表集合 hint 含自引用形态;多表既有用例回归)
- [x] 2.3 billing by_model calls FILTER:count(*) FILTER (WHERE
  status='SUCCESS'),tokens SUM 保持全量(测试:ERROR 行不计 calls、
  ERROR 带 usage 的 tokens 计入)
- [x] 2.4 http_runner 补 h11 响应头依赖注释(不修项登记固化)

## 3. api 面:用量页租户筛选(C1)

- [x] 3.1 llmusage page 端点加 tenant_id 参数,repo 经 u.tenant_id
  直滤(测试:按租户筛选返回该租户全部行含 unknown 展示行)
- [x] 3.2 make contracts 重生成,契约 diff 仅 +1 可选参数

## 4. agent 面

- [x] 4.1 LLM 注册表池缓存:deps.py 模块级缓存(指纹+event loop 维度),
  变更时旧实例逐 client aclose;parse_registry 按 JSON 串 lru_cache;
  reset_llm_cache() 暴露(测试:同指纹同实例/换指纹重建+aclose/不同
  loop 各自实例)
- [x] 4.2 收集器早返携带:main.py 三处早返(AttachmentError×2+
  LlmError)统一挂 usage_records(测试:部分成功后失败断言已收集条目
  随响应携带)
- [x] 4.3 收集器 e2e 测试:FakeLlm 补 usage 属性,collect_usage=True
  跑穿 ReAct 图,断言 usage_records 长度/seq 递增/reporter 零调用

## 5. web 面

- [x] 5.1 endExclusive 抽共享 util,ToolsPage 改引,AuditPage 两处
  替换(结束日含当天)
- [x] 5.2 AuditPage sql 区加租户 Select 筛选(tenant_id 参数已备)
- [x] 5.3 useDebounced 抽 src/hooks/use-debounced.ts,UsagePage 改导入
- [x] 5.4 UsagePage 加租户 Select 筛选(消费 3.1 参数,queryKey 带上)

## 6. D 组:深挖清偿(收敛指令追加)

- [x] 6.1 循环子节点归因:engine _run_loop 逐子节点记 NodeRun
  (`<loop>.<i>.<sub>` 标记),失败子节点按自身 id 归因(测试:第 2
  轮子节点失败快照含迭代标记 FAILED 行;成功循环逐次记入)
- [x] 6.2 run 详情端点:repo get_run(不剥 outputs)+service 复用
  _assert_visible+router GET 详情(response_model 精确化),契约
  重生成(测试:详情含 outputs/可见性同口径/跨租户 404)
- [x] 6.3 web run 详情 Dialog:WorkflowsPage 历史行「详情」按钮+
  node_runs 含 outputs 折叠渲染
- [x] 6.4 QueryErrorAlert 共享组件:抽 src/components/query-error.tsx,
  全站 grep 消费面出清单后批量替换缺错误态页面(含 UsagePage 就地
  实现收敛;web-nav spec 立约)

## 7. 验证与收尾

- [x] 7.1 make check 全绿(api/agent ruff+mypy+lint-imports+pytest +
  web tsc+eslint);受控行为变更的既有断言全部对齐
- [x] 7.2 冒烟:/mcp 超限 413、PII 文件名 400、usage 页租户筛选 UI、
  审计页结束日含当天、run 详情 Dialog、断服务错误态渲染抽查
  (Playwright)
- [x] 7.3 openspec validate + 归档(archive/backlog-settlement)+ sync
  七域主 spec(tool-calling/mcp-interop/agent-memory/workflow-
  orchestration/rag/observability/web-nav)
- [x] 7.4 HANDOFF 更新:本变更记录+「已知 TODO」各段按四类终态重构
  (明确关闭/条件触发登记/产品登记/已销账)——**开放挂账清零**
