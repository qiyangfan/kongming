import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [
    // 启用 React Compiler，在构建时自动优化组件更新。
    react({ compiler: true }),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      // ESM 没有 __dirname；从 import.meta.url 解析项目路径。
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5173,
    // 端到端测试通过 cloudflared 或 ngrok 暴露本地可信域名。
    allowedHosts: [".trycloudflare.com", ".ngrok-free.app", ".ngrok-free.dev"],
    proxy: {
      // 可通过 VITE_API_PROXY_TARGET 指向其他 API 地址；默认使用本地 8000 端口。
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://localhost:8000",
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  build: {
    // 与 tsconfig 的目标版本一致。
    target: "es2022",
    // 允许较大的按需加载块，避免把合法分包误报为构建问题。
    chunkSizeWarningLimit: 1500,
    // 使用 Rolldown 的分包配置，确保图表依赖保留在动态加载的块中。
    rolldownOptions: {
      output: {
        // 按库名拆分依赖；不要将 ECharts 或 zrender 提到首屏块。
        // src/lib/echarts-boundary.test.ts 检查图表引擎仍按需加载。
        manualChunks(id: string): string | undefined {
          // pnpm 虚拟目录可能包含多段 node_modules，取最后一段之后的包名。
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
