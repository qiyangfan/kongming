# 025 custom-agent 实施任务

## 1. Schema 与种子

- [x] 1.1 alembic 0014:sys_bot_definition + sys_api_key(design D1)
- [x] 1.2 权限点 system:bot:manage(排序 19,超管全局);种子断言

## 2. bot 域包(测试先行:权限继承/审核)

- [x] 2.1 失败测试:创建越权工具子集拒/权限回收运行时失效(求交)/提示词 injection 拒发布/他人 Bot 404
- [x] 2.2 失败测试:api-key 生命周期(创建一次明文/哈希落库/撤销 401/scopes 越集合拒)
- [x] 2.3 实现 models/ports/repository/service;import-linter 第 13 契约
- [x] 2.4 管理端点(CRUD/启停/分享/keys)+公开端点 /bot/{key}/chat + /v1/chat/completions

## 3. agent 侧

- [x] 3.1 QueryReactIn 纯增 bot_system_prompt;REACT_SYSTEM 拆身份段+纪律后缀;注入组装
- [x] 3.2 agent 测试:Bot 注入纪律保留/空零回归

## 4. web 与契约

- [x] 4.1 make contracts;/bot/{key} 公开页;/admin/bots 子页(列表/编辑/启停/分享/key 创建一次展示)

## 5. 验证与收尾

- [x] 5.1 真 LLM 冒烟(全过):建 Bot→公开页免登录答 1000 部电影(ReAct 自主编排)→api-key 创建(明文一次+哈希落库)→/v1/chat/completions 答 200 演员(choices 形态)→权限回收/越权/护栏审核测试全绿;评估 smoke 5/5 零劣化
- [x] 5.2 R1 独立评审:3 高(H1 model 死配置穿透+校验/H2 datasource 校验/H3 bot_key 全局唯一)+5 中(M1 OpenAI created/model/M2 确认工具剔除/M3 agent 故障收敛/M4 审计 channel 平铺/M5 scopes 冗余修订)+8 低全处置;直证 +4 用例;全量绿
- [x] 5.3 HANDOFF + 挂账 + 归档
