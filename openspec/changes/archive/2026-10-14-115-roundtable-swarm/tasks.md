# 实施任务

每项按不超过两小时拆分,完成即跑 make check 对应子集并记录证据。
安全与失控防护一律测试先行:先写失败测试再实现。

## 1. 宿主集成与失控防护(测试先行)

- [x] 1.1 先写失败测试:圆桌无游离入口(roundtable 拓扑同样必须挂 RUNNING 步骤且已声明拓扑)、参与者授权不提权(未授权工具在圆桌查证中照拒并落审计)、零工具参与者 fail-closed 不派发(与 bot 域同口径)。验证:api 侧 test_task_collab 扩展用例红转绿 + agent 侧结构断言(编排核心无 router/api 面、授权签名不携带凭证)。
- [x] 1.2 先写失败测试:轮次硬顶触顶即止、预算池耗尽止(发言与查证同池,预算式 = 参与者数 × 轮次上限,声明推导 + 部署钳制)、名册运行期锁定(增减参与者被拒)、连续全员弃权提前收口且不改标共识。验证:agent 侧剧本化失败测试先行,实现后全绿。
- [x] 1.3 Alembic 新迁移:`sys_agent_collab_turn`(UUID v7、VARCHAR+CHECK kind、jsonb content/evidence/usage、trace_id、(run_id, round_no) 索引),无 UPDATE 路径。验证:dev 库 up/down 回环实证;ADR-004 baseline 核对记录在 apply。

## 2. 圆桌编排核心(agent 面)

- [x] 2.1 圆桌编排核心(零 api 句柄):轮次循环、黑板追加式转写管理、受控快照下传(截断纪律)、结构化发言解析(立场/论据/证据引用)、弃权语义。验证:剧本化测试(mock 参与者固定剧本)覆盖收敛/分歧保留/触顶/预算尽/弃权链至少 8 例,`uv run pytest apps/agent` 全绿。
- [x] 2.2 主席判定:结构化收敛裁决(consensus + 理由),计 token 不占执行预算池;轮次硬顶为误判确定性兜底。验证:剧本化测试含真共识、假共识识别(诱导附和用例)、漏收敛触顶兜底三例。
- [x] 2.3 wiring 装配:参与者 = 受限 ReAct(人格注入 Bot/内置章程、工具求交)、逐 turn 账本回写(尽力而为语义沿用)、参与者独立 trace 父子关联、用量按参与者归集;开账 fail-closed 复用。验证:agent 侧全链装配测试 + 开账被拒不执行断言。
- [x] 2.4 收口映射:共识/未共识/触顶/预算尽 → 既有协作运行账诚实语义(PARTIAL/PASS/FAILED),未竟事项披露;步骤终态由收口结论驱动。验证:四通道收口映射用例各一,与 091 收口语义回归不破。(api collab_run_status 映射回归不破[test_task_collab];核心剧本:共识 PASS/触顶保留分歧/预算截断 PASS+ERROR 双态/全员弃权;wiring:共识 dissent=False、e2e 实证 run DONE→宿主步 DONE、首跑未共识 run FAILED→步 FAILED 诚实收口)

## 3. 观测、契约与治理

- [x] 3.1 圆桌生命周期事件落任务事件流(开桌/每轮完成/判定/收口,沿用 collab_* 事件族);api /internal 协作回调扩展(turn 上账);collab-runs 响应扩展 turns;`make contracts` 重生成并核对既有契约零漂移(ADR-005)。验证:api 全链测试 + 导出前后 md5 一致。
- [x] 3.2 部署配置面:轮次上限 api 归一钳制 + agent 执行兜底(KM_AGENT_COLLAB_MAX_ROUNDS,缺省 3),更新 docs/tech-stack.md「任务/协作部署级上限」节。验证:钳制越界用例 + 文档落笔。

## 4. 前端与验收

- [x] 4.1 任务详情协作视图扩展圆桌形态:轮次转写时间线(参与者/立场/证据/弃权/判定,按序回放),标准件 + DataView 双形态,契约类型由生成获得。验证:组件测试 + 亮暗两态。
- [x] 4.2 `pnpm typecheck && pnpm lint` + 宪法 Article 8 自检 + 375/768/1280 走查。验证:门禁全绿 + 走查记录。(tsc/eslint/vitest 134 全绿;Article 8 grep 零命中[硬编码色/emoji/inline 间距/新增 dark:];Playwright+系统 Chrome 走查 9/9:圆桌形态标签+轮次转写回放在场、375/768/1280 横向溢出 0px、亮暗背景翻转[rgb(251,250,247) vs rgb(14,20,22)])
- [x] 4.3 端到端冒烟:真实 LLM 栈圆桌双场景(正常收敛收口 / 轮次触顶保留分歧);make check 全量记录证据。验证:e2e 脚本扩展运行留痕。(apps/api/scripts/e2e_roundtable_smoke.py 实跑 EXIT:0:场景一 3 参与者圆桌 run DONE/PASS、共识判定收口、8 轮次行/事件齐、用量归集;场景二取消收敛 run CANCELLED;另首跑观测未共识触顶诚实 FAILED 收口;断言口径=圆桌运行终态+账本诚实,任务 PAUSED 属 090 步级预算语义)

## 5. 收尾

- [x] 5.1 judge 层 RoundtableRubric v1(共识质量/分歧诚实/证据接地/收敛效率)+ 对象取数扩展 turns;基线登记(劣化 ≥5pp 门禁)。验证:judge runner 对圆桌对象出分,基线落库。(rubric/取数/CHECK 迁移 0055 落地;test_evalj 五类齐备 17 绿;judge 抽检层实跑:eval116 自建账号+五类取数管线通,本环境无 DONE 产出→「对象全部空缺如实记录」分支实测[092 同款];基线落库=条件触发:评估账号名下出现首个收口圆桌 run(DONE/PARTIAL,取数口径=运行终态、不要求宿主任务 DONE——评审单元是 run,决策与坑见 HANDOFF 115 节)时由定时门禁自然落基线,机制已就绪)
- [x] 5.2 独立代码审查(授权不提权/终止协议/黑板收口/预算池为重点),缺陷修复后复跑门禁。验证:审查记录 + 修复后 make check 全绿。(独立子代理全 diff 审查:P0=0/P1=1/P2=7/细节=3,全部处置——P1-1 每轮完成事件改按实派末位判定[剔除场景事件丢失];P2-1 转写行内携带参与者标签[web 归属错标];P2-2/3 绑定解析失败与载荷缺位改 fail-closed 剔除[授权侧不沿用 091 降级口径];P2-4 弃权判定收紧防吞实质发言;P2-5 spec 查证措辞收敛;P2-6 门禁破口修复;P2-7 预算截断裁决断言钉死;细节=索引收敛/主席用量归集/顶层 bot_key 拒绝声明;修复后各侧门禁复验绿)
- [x] 5.3 归档前 sync delta 进主 specs;roadmap 第四梯队多智能体行更新选型指引(并行分治→分工拓扑,立场对辩→圆桌拓扑)。
