import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import type { IncomingMessage, ServerResponse } from "node:http";
import { fileURLToPath, URL } from "node:url";
import { defineConfig, type Plugin } from "vite";

const REACT_VENDOR_PACKAGES = new Set([
  "react",
  "react-dom",
  "react-router",
  "scheduler",
  "@remix-run/router",
  "cookie",
  "set-cookie-parser",
]);

const MARKDOWN_PACKAGES = new Set([
  "react-markdown",
  "remark-breaks",
  "remark-gfm",
  "unified",
  "trough",
  "devlop",
  "bail",
  "zwitch",
  "longest-streak",
  "ccount",
  "github-slugger",
  "character-entities",
  "decode-named-character-reference",
  "property-information",
  "space-separated-tokens",
  "comma-separated-tokens",
  "html-void-elements",
  "html-url-attributes",
  "is-plain-obj",
]);

const MARKDOWN_PREFIXES = [
  "remark-",
  "rehype-",
  "micromark",
  "mdast-",
  "hast-",
  "unist-",
  "vfile-",
  "estree-util-",
];

// pnpm 虚拟目录可能包含多段 node_modules，取最后一段之后的包名。
function packageName(id: string): string | undefined {
  const nm = id.lastIndexOf("node_modules");
  if (nm < 0) return undefined;
  const parts = id.slice(nm + "node_modules/".length).split(/[\\/]/);
  const [head, second] = parts;
  if (!head) return undefined;
  return head.startsWith("@") ? `${head}/${second ?? ""}` : head;
}

// 按库名归组依赖；ECharts/zrender 不归组，保持按需加载。
function vendorChunk(name: string): string | undefined {
  if (REACT_VENDOR_PACKAGES.has(name)) return "react-vendor";
  if (name === "radix-ui" || name.startsWith("@radix-ui/")) return "radix";
  if (name.startsWith("@tanstack/")) return "react-query";
  if (MARKDOWN_PACKAGES.has(name) || MARKDOWN_PREFIXES.some((p) => name.startsWith(p))) {
    return "markdown";
  }
  return undefined;
}

/* 旧入口 308:/admin、/admin/* → 管理台同路径;深链不 404、不渲染旧管理页。
   生产由反代执行同规则(见 HANDOFF);本地 dev/preview 由此插件承担。 */
function adminRedirectPlugin(): Plugin {
  const target = process.env.VITE_ADMIN_ORIGIN ?? "http://127.0.0.1:5174";
  const handle = (req: IncomingMessage, res: ServerResponse, next: () => void): void => {
    const url = new URL(req.url ?? "/", "http://localhost");
    if (url.pathname === "/admin" || url.pathname.startsWith("/admin/")) {
      const rest = url.pathname.slice("/admin".length) || "/";
      res.statusCode = 308;
      res.setHeader("Location", `${target}${rest}${url.search}`);
      res.end();
      return;
    }
    next();
  };
  return {
    name: "admin-redirect",
    configureServer(server) {
      server.middlewares.use(handle);
    },
    configurePreviewServer(server) {
      server.middlewares.use(handle);
    },
  };
}

export default defineConfig({
  plugins: [
    // 启用 React Compiler，在构建时自动优化组件更新。
    react({ compiler: true }),
    tailwindcss(),
    adminRedirectPlugin(),
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
        // 语音通话的文字事件通道走 WebSocket(同 /api 前缀)
        ws: true,
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
          const name = packageName(id);
          return name === undefined ? undefined : vendorChunk(name);
        },
      },
    },
  },
});
