# 040-theme-switch 任务清单

## 1. 主题状态与解析(D1/D2/D4)

- [x] 1.1 新建 `src/lib/theme-store.ts`:zustand store,解析契约「localStorage 手动值 → prefers-color-scheme → 亮色」(key `km-theme`,存取 try/catch,注释与 index.html 内联脚本互指);含 matchMedia change 监听,仅无手动值时实时跟随
- [x] 1.2 `index.html` head 内联防 FOUC 脚本:首帧前按同契约挂/不挂 `.dark` 到 `<html>`(注释互指 theme-store);`.dark` 下 `color-scheme: dark`、`:root` 亮色对应写入 `index.css`

## 2. 切换入口与全站生效(D1/D5)

- [x] 2.1 `user-menu.tsx` 增加主题切换单项:图标随当前态(太阳/月亮),点击翻转并持久化,零网络请求
- [x] 2.2 验证 Radix portal 浮层(下拉/对话框)在 `.dark` 挂 `<html>` 下主题正确(popover/card token 命中)

## 3. 图表主题联动(D3)

- [x] 3.1 主题联动以 key 重挂实现:`chart-view` 壳接入 `useTheme()` 重挂实现组件(activeType 状态提升至壳,切主题不丢用户手动选择);admin 用量/反馈页各抽趋势图子组件(`UsageTrendChart`/`FeedbackTrendChart`)同机制;echarts 按需注册单源 `lib/echarts.ts` 注入 core 包

## 4. 公开页跟随策略(D6)

- [x] 4.1 `BotPublicPage` 复核:无切换入口,主题由内联脚本解析(系统偏好),无 store 写路径

## 5. 验证与收尾

- [x] 5.1 `make check` 全绿(web 侧 tsc + eslint)(2026-08-29 收官复核绿后补勾)
- [x] 5.2 浏览器实测(spec 全场景):首次跟随系统 / 手动切换即时生效并持久化 / 暗色刷新无白闪 / 切换后聊天图表与 admin 图表换色 / 公开页暗色渲染且无入口
- [x] 5.3 `make contracts` 确认零契约漂移(纯前端 UI,预期无 diff)
