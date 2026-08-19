/* 认证态(zustand):status 三态 + 当前用户;会话恢复单飞幂等。
   fail-closed:任何一步失败都回落 guest,不留半截登录态。 */

import { create } from "zustand";
import {
  api,
  clearTokens,
  getRefreshToken,
  onSessionCleared,
  setTokens,
  tryRefresh,
  type MeOut,
  type TokenPairOut,
} from "./api";
import { useDatasource } from "./datasource-store";
import { useTenant } from "./tenant-store";

export type AuthStatus = "loading" | "guest" | "authed";

interface AuthState {
  status: AuthStatus;
  user: MeOut | null;
  bootstrap: () => Promise<void>;
  acceptTokens: (pair: TokenPairOut) => Promise<void>;
  logout: () => Promise<void>;
}

let booting: Promise<void> | null = null;

export const useAuth = create<AuthState>((set) => {
  // api 层刷新失败清态 → 同步回落 guest + 清租户/数据源上下文(反向解耦:store 不被 api import)
  onSessionCleared(() => {
    set({ status: "guest", user: null });
    useTenant.getState().clear();
    useDatasource.getState().clear();
  });

  async function loadMe(): Promise<void> {
    try {
      const me = await api.get<MeOut>("/auth/me");
      set({ status: "authed", user: me });
      // 登录态就绪后校准租户:持久值失效(如被解绑)则回落首项
      useTenant.getState().calibrate(me.tenants);
    } catch {
      clearTokens();
      set({ status: "guest", user: null });
      useTenant.getState().clear();
      useDatasource.getState().clear();
    }
  }

  return {
    status: "loading",
    user: null,

    bootstrap: () => {
      // 已登录态短路:Protected 随路由重挂载会重复触发 bootstrap,若不短路,
      // 每次都走 tryRefresh 轮换 refresh token——多标签页下另一标签持旧票再刷,
      // 即被后端复用检测吊销全家族(误伤)
      if (useAuth.getState().status === "authed") return Promise.resolve();
      // StrictMode 双挂载/多守卫并发下幂等(单飞)
      booting ??= (async () => {
        if (!getRefreshToken()) {
          set({ status: "guest", user: null });
          return;
        }
        if (await tryRefresh()) {
          await loadMe();
        } else {
          set({ status: "guest", user: null });
        }
      })().finally(() => {
        booting = null;
      });
      return booting;
    },

    acceptTokens: async (pair) => {
      setTokens(pair.access_token, pair.refresh_token);
      await loadMe();
    },

    logout: async () => {
      const rt = getRefreshToken();
      if (rt) {
        await api.post("/auth/logout", { refresh_token: rt }).catch(() => {
          /* 登出幂等,网络失败不阻断本地清态 */
        });
      }
      clearTokens();
      set({ status: "guest", user: null });
      useTenant.getState().clear();
      useDatasource.getState().clear();
    },
  };
});
