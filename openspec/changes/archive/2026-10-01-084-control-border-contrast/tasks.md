# 084 实施任务：控件边界令牌可读性

## 1. 取证

- [x] 1.1 量出现状：`--input` 与 `--border` 在 kongming/jinguan/muniu 三仓两两同值；亮色 1.24:1、暗色 1.34~1.49:1（均低于非文本 3:1）
- [x] 1.2 确认影响面：`border-input` 消费方仅 `input/textarea/select/checkbox/toggle/button(outline)`；卡片/表格/分隔线走 `--border`

## 2. 定值与实施

- [x] 2.1 kongming `--input` 亮 `#808c86`、暗 `#62767c`（按宣纸白/黛墨夜与其卡面算得），`--border` 不动
- [x] 2.2 jinguan 同调色板 → 同值
- [x] 2.3 muniu 松烟系色板独立算值 → 亮 `#7c8781`、暗 `#6b7a6f`
- [x] 2.4 三仓 token 旁留注释说明「控件线不得等于分隔线」，防后来者顺手统一
- [x] 2.5 `FRONTEND-CONSTITUTION.md` Article 3 增口径：两语义不得同值 + ≥3:1 + 改色板须重算（`index.css` 不在 sync 脚本同步集，跨仓一致性靠规则不靠拷贝）

## 3. 验证

- [x] 3.1 kongming 真浏览器实测：亮态复选框边框对弹层底 3.35:1；暗态对卡面 3.58:1；亮暗两态角色表单截图复核，无布局位移
- [x] 3.2 jinguan 真浏览器实测（其 dev server 在跑，vite 直接读盘）：登录页输入框边框 3.49:1
- [x] 3.3 muniu 真浏览器实测：控制台输入框边框 3.72:1
- [x] 3.4 三仓 `stylelint`（含 `--fix` 后复检）+ `prettier --check` 全绿；kongming `make web-check`（tsc/eslint/stylelint/prettier）+ vitest 66/66 全绿

## 4. 记录

- [x] 4.1 已知弱项留档不改：亮色聚焦环 `--ring #c9a063` 对白底 2.31:1（品牌点睛色，改它属品牌决策；修复路径是不透明度/内描边而非换色相），见 design D5
- [x] 4.2 归档 sync 进 `theme-switch` 主规格；三仓改动停在未提交状态（git 写操作需 owner 逐次指令）
