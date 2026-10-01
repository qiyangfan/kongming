# 085 提案：控件填充令牌与控件边界令牌分离（`--input-fill`）

## Why

084 把 `--input` 从「抄 `--border` 的值」改成达标 ≥3:1 的控件线色，同时写下了两条现在被证伪的判断：

1. **「消费方精确可数：`border-input` 只被 input/textarea/select/checkbox/toggle/button(outline) 用到」**
   ——这句只数了**描边**消费方。同一个 `--input` 在 shadcn 标准件里还喂着**填充**：
   `dark:bg-input/30`（input/textarea/select/checkbox/outline 按钮的暗色内底）、
   `dark:hover:bg-input/50`（select/按钮 hover），以及 `data-[state=unchecked]:bg-input`
   ——**Switch 未勾选轨道的实色，且这一处不带 `dark:`，亮色同样生效**。
   本仓 Switch 有 5 个真实消费方（ChatPage / SemanticPage / BotsPage / GuardrailsPage / status-switch），
   即「启用/停用」这类状态开关。
2. **「不引入新的 token 层：shadcn 已有 `--input` 槽位，再造一个就是第二套事实源」**
   ——这句的前提是「`--input` 只有一个语义」。它有两个，所以拆它不是造第二套事实源，
   而是把一直混在一起的两个语义各自归位。

于是 084 的实际效果超出授权范围：亮色下未勾选开关轨道从 `#dfe3de`（近不可见的浅槽）
变成 `#808c86` 实心灰条——**关闭态看起来像开启/禁用，属状态误读**（UI 闸门 02 出乎意料）；
暗色下输入框内底从「比卡面略亮一档」提到 30% 的黛青，表单像被整体填充高亮。
这两项都不是任何人要求的变化，是我这一批带出来的回归。

判据不变的部分仍然成立：控件边界必须 ≥3:1（WCAG 1.4.11）。要修的是**让填充语义不再被描边比值绑架**。

## What Changes

1. **新增第三个语义 token `--input-fill`**（muniu 侧 `--color-input-fill`），取值 = 084 拆分前
   `--input` 的原值（kongming/jinguan 亮 `#dfe3de`、暗 `#2a363a`；muniu 亮 `#e2e2de`、暗 `#2b302a`）。
   即：填充与轨道**回到 084 之前的逐字节取值**。
2. **11 个填充消费点改指 `bg-input-fill*`**：kongming/jinguan 各 9 处（button/checkbox/input/
   select/switch/textarea），muniu 2 处（select）。`border-input` 一字节不动。
3. **双仓标准件保持逐字节一致**：`ui/{checkbox,input,select,switch,textarea}.tsx` 改后仍 `cmp` 相同
   （`button.tsx` 的既有差异是移动端热区档位的仓内分叉，早于本批，不属本批范围，未动其语义）。
4. **宪法 Article 3 补第三条口径**：分隔线 / 控件边界 / 控件底·轨道填充是**三个**语义、
   三个 token；调 `--input` 前必须 grep `bg-input`；`ui/*.tsx` 出现裸 `bg-input`（非 `-fill`）即视为回归。

## Non-goals

- **不回退 084 的描边值**：`--input` 亮 `#808c86`、暗 `#62767c` 保持，≥3:1 的收益是本批继续成立的前提。
- **不动 `--border`**：分隔线与卡片描边的 hairline 基线零变化。
- **不重定义填充观感**：本批只把它搬回原值，不做「顺手让输入框更好看」的调整——
  填充色要不要变深是独立议题，无需求即不动。
- **不改 `--ring`**：084 design D5 的聚焦环弱项（亮 2.31:1，属品牌表达决策）维持登记不动。

## Impact

- 三仓 `index.css`：各 +2 行 token（亮/暗）+ kongming/jinguan `@theme inline` 各 +1 行映射；
  组件侧 11 处类名替换，零逻辑改动。
- 渲染结果：`--input` 路径（描边）与 084 后一致；`--input-fill` 路径与 **084 之前**一致。
  实测（构建产物 + 真浏览器计算值）：亮态输入框边框 `rgb(128,140,134)`、开关轨道 `#dfe3de`；
  暗态输入框边框 `rgb(98,118,124)`、内底 `oklab(0.324 / 0.3)`（即 `#2a363a` 30%）、开关轨道 `rgb(42,54,58)`。
- 接线风险由构建产物兜住：`bg-input-fill` 若拼错或 token 未在 `@theme` 注册，Tailwind 不会生成该类，
  开关轨道会直接失去底色——已在三仓 `vite build` 产物 CSS 中逐条确认该类存在且解析到 `var(--input-fill)`。
- 门禁：三仓 `typecheck / eslint / stylelint / prettier` 全绿。
- 归档 sync 进 `theme-switch` 主规格（新增一条 Requirement，084 那条原文不动）。
