// 服务器连接配置。API_BASE 来自 Vite env（构建期注入），缺省指向本地后端。
// WS_BASE 由 API_BASE 派生：http→ws / https→wss，复用同一 host:port。

export const API_BASE = (import.meta.env.VITE_API_BASE ?? "http://localhost:8000").replace(/\/$/, "")

// http→ws、https→wss 一并覆盖（正则只锚定开头的 http）。
export const WS_BASE = API_BASE.replace(/^http/, "ws")

// localStorage key（admin token 持久化，见 SettingsDialog 服务器连接段）。
// API base 取自 VITE_API_BASE，运行时不可编辑（iPad-over-LAN 可编辑地址是后续 follow-up）。
export const LS_TOKEN_KEY = "soundspeed.adminToken"
