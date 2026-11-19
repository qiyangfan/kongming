/* 按当前租户保存 Agent 模式；默认使用 recipe，也可选 ReAct。 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

type AgentMode = "recipe" | "react";

interface AgentModeState {
  mode: AgentMode;
  setMode: (m: AgentMode) => void;
  /** 登出/会话终结清态(与 tenant/datasource 同纪律):避免跨账号继承开关 */
  clear: () => void;
}

export const useAgentMode = create<AgentModeState>()(
  persist(
    (set) => ({
      mode: "recipe",
      setMode: (m) => set({ mode: m }),
      clear: () => set({ mode: "recipe" }),
    }),
    { name: "km.agent_mode" },
  ),
);
