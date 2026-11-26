# Tasks: improve-sql-block-rendering

## 1. 依赖与高亮管线

- [x] 1.1 `apps/web` 增加 `refractor@5` 依赖(pnpm),`pnpm-lock.yaml` 入库
- [x] 1.2 `src/lib/rehype-sql-highlight.ts`:薄 rehype 插件(仅 `language-sql`
      节点高亮,其余原样),配纯函数单测(命中/非 sql/无 className/空内容)
- [x] 1.3 `MarkdownText` 接入该插件;`index.css` 增加代码高亮 token 组(亮暗
      双值)并把 `token *` 类名映射到变量;`theme-tokens.test.ts` 复跑

## 2. SQL 卡形态

- [x] 2.1 SQL 卡默认只读:展开后渲染 ```` ```sql ```` 只读块 + 复制;「编辑」
      作为次级入口,进入编辑态用既有 Textarea 与「重新执行/还原」,退出回只读
- [x] 2.2 组件测试:默认只读无 textarea、进入编辑、还原/收起回只读、执行调用
      rerun 且失败停编辑态、键盘焦点回到触发按钮
- [x] 2.3 亮暗两态 + 375/768/1280 走查(文档卡代码块与 SQL 卡,记录截图结论):
      2026-11-26 本地 eval-run/sakila 实测,1280/768/375 × 亮暗全部通过;代码行超宽
      按既有 `overflow-x-auto` 滚动,375 编辑态可用;补代码高亮 token 对比度测试
      (注释色修正为 `#5f6b75`/`#84969e`,≥4.5:1);截图未入库

## 3. 门禁与收尾

- [x] 3.1 `make web-check`(typecheck / lint / vitest / size-limit / e2e)
- [x] 3.2 宪法自检 grep(无 hex 色值、无原生控件、无内联 style、无窗口弹窗)
- [x] 3.3 HANDOFF 验收记录追加(体积实测值与形态变化);如 size 数字变化超出
      预算,调整配置并登记理由
