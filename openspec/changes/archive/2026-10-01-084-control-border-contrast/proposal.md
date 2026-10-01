# 084 提案：控件边界令牌两态可读性（`--input` 与 `--border` 语义分离）

## Why

走查角色表单时量到暗色下复选框边框对弹层底只有 1.49:1。复查发现这**不是暗色问题，也不是配色选浅了**：

- `--input` 在源码里被直接抄成 `--border` 的值（亮 `#dfe3de`、暗 `#2a363a`，两两相同），
  于是「青灰线 hairline」这个**给分隔线用的**刻意低对比，把表单控件的边界一起吃掉了；
- 亮色比暗色更糟：`#dfe3de` 对宣纸白 **1.24:1**、对白卡 1.30:1。未勾选的复选框只有这一条线可辨，
  1.2x 意味着控件近乎隐身；
- 三仓同病（同一次标准件移植带过去的）：kongming/jinguan `--input == --border`，
  muniu `--color-input == --color-border`（亮 1.23:1、暗 1.34:1）；
- 消费方精确可数：`border-input` 只被 `input / textarea / select / checkbox / toggle / button(outline)` 用到，
  卡片、表格、分隔线走 `--border`。所以这不是「改全站视觉基线」，而是**把系统里本来就有的语义分离用回来**。

本仓 ui-quality 闸门与 WCAG 1.4.11 都要求「识别界面组件所需的视觉信息 ≥3:1」，当前值差一个数量级。

## What Changes

1. **kongming**：`--input` 亮 `#808c86`（对宣纸白 3.35、对白卡 3.49）、暗 `#62767c`（对卡面 3.58、对底 3.89）；`--border` 一字节不动。
2. **jinguan**：同调色板 → 同值。
3. **muniu**：自有松烟/灰绿色板，按其底色另算 → 亮 `#7c8781`（3.47/3.72）、暗 `#6b7a6f`（4.06/3.69）。
4. **口径入宪法**：`FRONTEND-CONSTITUTION.md` Article 3 增一条——分隔线与控件边界是两个语义，
   `--input` 不得等于 `--border`，且必须对其所在面 ≥3:1；附三仓现值与「改色板须重算比值」的要求。
   `index.css` 不在 `sync-frontend-standard.sh` 的同步集内（各仓自有色板），故规则靠宪法 + 本 spec 约束，不靠拷贝。
5. **注释留在 token 旁边**：说明为什么两值必须不同，防止后来者「顺手统一」。

## Non-goals

- **不动 `--border`**：hairline 低对比是品牌基线（宣纸白 + 青灰线），把它拉到 3:1 会让每张卡片、每条表格线变成灰框。
- **不动 `--ring`（鎏金）**：亮色下 `#c9a063` 对白底 2.31:1，聚焦环本身也偏弱——但那是品牌点睛色，
  改它属品牌表达决策而非缺陷修复；且聚焦态另有 3px 光环 + 现在 ≥3:1 的静态边框可对照。
  本批只记录，不改值。
- **不引入新的 token 层**（不加 `--control-border` 之类）：shadcn 已有 `--input` 槽位，再造一个就是第二套事实源。
- **不改控件尺寸/圆角/阴影**：只改线色，零布局位移。

## Impact

- 三仓 `index.css` 各 2 行 token 值 + 注释；无组件代码改动（`border-input` 消费方自动受益）。
- 实测（真浏览器、真页面）：kongming 亮 3.35 / 暗 3.58；jinguan 亮 3.49；muniu 亮 3.72。
- 门禁：三仓 `stylelint` + `prettier` 全绿；kongming `make web-check`（tsc/eslint/stylelint/prettier）+ vitest 66/66 全绿。
