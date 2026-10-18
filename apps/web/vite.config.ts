import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    // React Compiler 1.0(adopt stack-refresh 3.4):构建期自动 memo;违例组件
    // 编译器跳过并告警不阻断。手写 memo 保留叠加(裁决:不删,新代码逐步免写)
    react({ compiler: true }),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // ESM 无 __dirname;官方模板姿势:import.meta.url(不依赖 vite 的内部 shim)
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 放行隧道域名(企微可信域名 e2e 用):cloudflared 快速隧道 + ngrok 免费固定域名
    allowedHosts: [".trycloudflare.com", ".ngrok-free.app", ".ngrok-free.dev"],
    proxy: {
      // 目标可经 VITE_API_PROXY_TARGET 覆盖(冒烟/联调指向非缺省 api 端口,
      // 如容器栈 18080);缺省保持既有 8000 契约不变
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000",
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    // 现代浏览器基线(Vite 默认 "modules"=esnext 过宽;es2022 对齐 tsconfig target)
    target: "es2022",
    // 事前实测:react-vendor ~290kB / markdown ~149kB 合法块超 500kB 默认线属预期,阈值随分包调宽
    chunkSizeWarningLimit: 1500,
    // Vite 8(Rolldown):rollupOptions 更名 rolldownOptions(迁移指南;manualChunks
    // 函数形态尚可用已弃用,066 分包边界由 echarts-boundary.test.ts 静态护栏钉住)
    rolldownOptions: {
      output: {
        // 按库名精确分包(066):粗粒度「node_modules → 单一 vendor 桶」会把 zrender
        // (约 250kB,echarts 渲染引擎)从已懒加载的 echarts 动态块提到首屏,禁——
        // echarts/zrender 不落入任何规则,留给 Rollup 自动共享块(仅动态可达)。
        // 回归护栏:src/lib/echarts-boundary.test.ts 静态扫描钉死该边界。
        manualChunks(id: string): string | undefined {
          // pnpm 虚拟店布局:真实包名在最后一个 node_modules/ 之后
          // (…/node_modules/.pnpm/pkg@ver/node_modules/pkg/…)——取末段
          const nm = id.lastIndexOf("node_modules");
          if (nm < 0) return undefined;
          const rest = id.slice(nm + "node_modules/".length);
          const parts = rest.split(/[\\/]/);
          const name = parts[0].startsWith("@") ? `${parts[0]}/${parts[1]}` : parts[0];
          if (
            name === "react" ||
            name === "react-dom" ||
            name === "react-router" ||
            name === "scheduler" ||
            name === "@remix-run/router" ||
            name === "cookie" ||
            name === "set-cookie-parser"
          ) {
            return "react-vendor";
          }
          if (name === "radix-ui" || name.startsWith("@radix-ui/")) return "radix";
          if (name.startsWith("@tanstack/")) return "react-query";
          if (
            name === "react-markdown" ||
            name === "remark-breaks" ||
            name === "remark-gfm" ||
            name.startsWith("remark-") ||
            name.startsWith("rehype-") ||
            name.startsWith("micromark") ||
            name.startsWith("mdast-") ||
            name.startsWith("hast-") ||
            name.startsWith("unist-") ||
            name.startsWith("vfile-") ||
            name === "unified" ||
            name === "trough" ||
            name === "devlop" ||
            name === "bail" ||
            name === "zwitch" ||
            name === "longest-streak" ||
            name === "ccount" ||
            name === "github-slugger" ||
            name === "character-entities" ||
            name === "decode-named-character-reference" ||
            name === "property-information" ||
            name === "space-separated-tokens" ||
            name === "comma-separated-tokens" ||
            name === "html-void-elements" ||
            name === "html-url-attributes" ||
            name === "is-plain-obj" ||
            name.startsWith("estree-util-")
          ) {
            return "markdown";
          }
          return undefined;
        },
      },
    },
  },
});
