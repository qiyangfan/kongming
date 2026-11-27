import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";

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

function packageName(id: string): string | undefined {
  const nm = id.lastIndexOf("node_modules");
  if (nm < 0) return undefined;
  const parts = id.slice(nm + "node_modules/".length).split(/[\\/]/);
  const [head, second] = parts;
  if (!head) return undefined;
  return head.startsWith("@") ? `${head}/${second ?? ""}` : head;
}

function vendorChunk(name: string): string | undefined {
  if (REACT_VENDOR_PACKAGES.has(name)) return "react-vendor";
  if (name === "radix-ui" || name.startsWith("@radix-ui/")) return "radix";
  if (name.startsWith("@tanstack/")) return "react-query";
  if (MARKDOWN_PACKAGES.has(name) || MARKDOWN_PREFIXES.some((p) => name.startsWith(p))) {
    return "markdown";
  }
  return undefined;
}

export default defineConfig({
  plugins: [react({ compiler: true }), tailwindcss()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  server: {
    port: 5174,
    strictPort: true,
    proxy: {
      // 管理台与后端同源:浏览器只访问 /api,不开放跨域 CORS
      "/api": {
        target: process.env.VITE_API_PROXY_TARGET ?? "http://127.0.0.1:8000",
        rewrite: (p) => p.replace(/^\/api/, ""),
        ws: true,
      },
    },
  },
  build: {
    target: "es2022",
    chunkSizeWarningLimit: 1500,
    rolldownOptions: {
      output: {
        manualChunks(id: string): string | undefined {
          const name = packageName(id);
          return name === undefined ? undefined : vendorChunk(name);
        },
      },
    },
  },
});
