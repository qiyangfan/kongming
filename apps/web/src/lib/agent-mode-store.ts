/* Agent 内核模式(019 tool-calling):recipe(8 节点预设配方,缺省)/ react
   (ReAct 自主工具调用)。localStorage 单源,登出/会话终结随 tenant 同清。 */

import { create } from "zustand";
import { persist } from "zustand/middleware";

export type AgentMode = "recipe" | "react";

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
