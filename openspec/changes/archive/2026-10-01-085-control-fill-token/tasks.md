# 085 实施任务：控件填充令牌分离

## 1. 取证

- [x] 1.1 grep 三仓 `bg-input` 列出全部填充消费点（kongming/jinguan 各 9 处、muniu 2 处），确认 084 的影响面枚举只覆盖了 `border-input`
- [x] 1.2 确认 Switch 未勾选轨道 `data-[state=unchecked]:bg-input` 不带 `dark:`，亮色同样受影响；本仓 Switch 有 5 个真实消费方（含 status-switch 的启用/停用语义）

## 2. 定值与实施

- [x] 2.1 kongming `index.css` 新增 `--input-fill`（亮 `#dfe3de` / 暗 `#2a363a`）+ `@theme inline` 映射 `--color-input-fill`，token 旁留「第三语义」注释
- [x] 2.2 jinguan 同值同注释（同调色板），`@theme inline` 映射一并补
- [x] 2.3 muniu `@theme`/`.dark` 补 `--color-input-fill`（亮 `#e2e2de` / 暗 `#2b302a`，按其色板独立算）
- [x] 2.4 11 处填充类改 `bg-input-fill*`，`border-input` 与 `--input` 值不动
- [x] 2.5 `cmp` 双仓标准件：`ui/{checkbox,input,select,switch,textarea}.tsx` 改后仍逐字节一致
- [x] 2.6 `FRONTEND-CONSTITUTION.md` Article 3 补第三条口径（三分离 + 调 `--input` 前必须 grep `bg-input` + 裸 `bg-input` 视为回归）
- [x] 2.7 把「裸 `bg-input` 即回归」从 spec 文本变执法面：新增 `apps/web/src/lib/control-token-split.test.ts` 静态守卫 4 用例（两态 `--input`≠`--border`、`--input-fill` 存在且≠`--input`、`@theme` 已注册 `--color-input-fill`、src 全量 `.tsx` 无裸 `bg-input`）；**红灯实证**：临时把 select 的 `bg-input-fill/30` 改回 `bg-input/30` 并删掉暗色 `--input-fill` → 2 用例分别报 `expected [ 'components/ui/select.tsx' ] to deeply equal []` 与 `暗色缺 --input-fill`，复原后 4/4 绿、双仓标准件 `cmp` 仍逐字节一致

## 3. 验证

- [x] 3.1 三仓 `vite build` 产物 CSS 逐条确认 `.bg-input-fill*` 规则存在且解析到 `var(--input-fill)` / `var(--color-input-fill)`，token 亮暗两值均已输出
- [x] 3.2 真浏览器计算值复核（kongming 构建产物本地托管）：亮态输入框边框 `rgb(128,140,134)`、开关轨道 `#dfe3de`；暗态边框 `rgb(98,118,124)`、内底 `#2a363a`@30%、轨道 `rgb(42,54,58)`——描边随 084 达标、填充随 084 之前
- [x] 3.3 三仓 `pnpm typecheck && pnpm lint && pnpm lint:css && pnpm format:check` 全绿
- [x] 3.4 全仓 grep 确认 `ui/*.tsx` 无裸 `bg-input`（非 `-fill`）残留
- [x] 3.5 守卫覆盖缺口如实登记：jinguan 无 `@types/node`、muniu 无 vitest——镜像同一条守卫会分别卡在它自己的 `tsc` 与「无测试架」上。两仓 `ui/` 标准件由 `scripts/sync-frontend-standard.sh` 以 kongming 为源 `cmp`（类名漂移可见），唯 `index.css` 不在同步集内（各仓自有色板），其令牌取值靠宪法 Article 3 现值表人工核对。**裁决：不为一条镜像断言改两仓构建面**（加 `@types/node` 触 Article 4 依赖白名单，加 `vite-env.d.ts` 属为测试补脚手架）

## 4. 记录

- [x] 4.1 明示顶替 084 的两条判断（影响面只数了描边消费方 / 「不引入新 token」的前提是单语义），错因与证据写进本件 proposal Why
- [x] 4.2 归档 sync 进 `theme-switch` 主规格（084 那条 Requirement 原文不动，本批 ADDED 新条）；三仓改动停在未提交状态（git 写操作需 owner 逐次指令）
