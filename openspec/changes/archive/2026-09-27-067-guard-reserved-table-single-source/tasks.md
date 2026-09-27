# 067-guard-reserved-table-single-source 实施任务

安全规则类变更(SQL 防火墙/保留表),按 rules.tasks 测试先行:每项先跑红再实现。

## 1. 失败测试先行

- [x] 1.1 失败测试:清单一致性——控制面防火墙、Agent 面预检、已加载引擎三方引用的
      保留前缀/保留精确名/保留 schema/只读根/危险函数为**同一来源**(对象恒等),
      而非内容恰好相等;并断言清单成员含 `skill_` 与 `user_prefs`
      **预期:当前为红**(api 宽、引擎窄)→ 先跑红
- [x] 1.2 失败测试:防回归——在第二处定义清单字面值时该断言必须失败
      (用一份"影子常量"局部构造验证断言真有牙,不是永真)
      **预期**:先跑红或按 1.1 同批转绿
- [x] 1.3 失败测试:`SELECT * FROM skill_pack` / `SELECT glossary FROM
      public.skill_pack` / `SELECT * FROM user_prefs` 三形态 verdict=BLOCKED
      (裸名、显式限定、JOIN 夹带各覆盖),且引擎模式 builtin/shadow/primary
      **三态下结论一致**
      **预期**:primary 态当前为红 → 先跑红
- [x] 1.4 失败测试:业务表普通名不被误伤(含名为 `pack`、`prefs`、
      `user_preferences` 之类近似形态),`skill_` 前缀不误伤非保留 schema 的
      业务同名表判定逻辑不变
      **预期**:先跑红(与 1.3 同批),再实现
- [x] 1.5 失败测试:引擎被降级到不含单源清单的旧版本时,视为引擎不可用 →
      请求级降级内置路径 + 计入 `ENGINE_FALLBACK`,不中断请求
      **预期**:先跑红

## 2. 实现:常量单源化

- [x] 2.1 `packages/liannu/liannu/rules.py` 收编 `skill_` 前缀与 `user_prefs`
      精确名,成为五组常量的唯一定义;包版本号递增
      **验证**:1.1 转绿
- [x] 2.2 `apps/api/kongming/sqlfw/firewall.py` 删除本地字面值,改为从 liannu
      导入并保持符号名(re-export),使 `internal/meta.py`、`rag/repository.py`
      引用面零改动;符号旁注一行说明定义所在
      **验证**:1.1、1.3、1.4 转绿;`make api-check` 绿
- [x] 2.3 `apps/agent/kongming_agent/sqlguard.py` 删除 `_READONLY_ROOTS` 本地拷贝,
      改为引用 liannu;agent 不因此新增任何保留表判定职责(权威仍在 api,
      ADR-002 边界不变)
      **验证**:`make agent-check` 绿;import-linter 契约无违例
- [x] 2.4 引擎可用性自检补成员集合校验(清单窄于权威即降级)
      **验证**:1.5 转绿
- [x] 2.5 双 `pyproject.toml` 的 `liannu==` 声明同步递增(仓内既有版本纪律)
      **验证**:`uv sync` 后两侧导入一致

## 3. 规格与 053 收口

- [x] 3.1 `openspec/specs/sql-execution/` sync 本 delta:清单成为唯一权威枚举
- [x] 3.2 `skill-pack/spec.md` 与 `agent-memory/spec.md` 去重:保留"该表为何敏感"的
      域内理由与拒读场景,删去对清单成员的共同声明(避免第三处枚举)
- [x] 3.3 053 切主前置补一条:归档/切 primary 须 1.1 断言绿;"mismatch 计数归零"
      从充分条件降为必要条件,并在 `docs/HANDOFF.md` 的 053 退出条件同步
      **验证**:053 tasks 或 HANDOFF 文本可见该前置
- [x] 3.4 `liannu/rules.py` 文件头「一比一移植」表述修正为「清单唯一定义于本包」
      (消除注释与代码行为的冲突)

## 4. 收口

- [x] 4.1 手工冒烟:三态模式下 `skill_pack`/`user_prefs` 均 BLOCKED 且落审计;
      业务表正常查询不受影响;记录于 apply
- [x] 4.2 全量 `make check` 收口(含 053 冻结的 121 例对拍夹具复跑不劣化)

> 067 落地记录:
> - liannu.rules 成为五组清单唯一定义(收编 skill_/user_prefs),版本 0.1.3;
>   api firewall / agent sqlguard 改为引用,re-export 保持消费面零改动。
> - guard.startup_self_check 增清单成员校验,窄于权威即降级 builtin。
> - 测试:test_reserved_single_source 10 例(恒等/三态拦截/防误伤/窄清单降级)
>   + agent 恒等 1 例 + liannu 成员 1 例;先红后绿。
> - 规格:sql-execution 同步唯一权威枚举;skill-pack/agent-memory 去重;
>   HANDOFF 053 退出条件补清单一致前置。
> - make check 全绿(api 935 / agent 289 / liannu / web / contracts)。
> - 冒烟:三态下 skill_pack/user_prefs 均 RESERVED_TABLE 拦截,业务表放行。

