// 桌面壳能力：在 Tauri 里用原生插件，在浏览器开发模式下优雅降级。
import { isTauri } from "@tauri-apps/api/core";

export const inTauri = (() => {
  try {
    return isTauri();
  } catch {
    return false;
  }
})();

/** 开机自启：走 Rust 端自定义 command（autostart_get / autostart_set），不额外引入 npm 包 */
export async function getAutostart(): Promise<boolean> {
  if (!inTauri) return false;
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<boolean>("autostart_get");
}

export async function setAutostart(enabled: boolean): Promise<boolean> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<boolean>("autostart_set", { enabled });
}

export async function openExternal(url: string) {
  if (inTauri) {
    const { openUrl } = await import("@tauri-apps/plugin-opener");
    await openUrl(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

let permission: boolean | null = null;

export async function ensureNotifyPermission(): Promise<boolean> {
  if (permission !== null) return permission;
  if (inTauri) {
    const n = await import("@tauri-apps/plugin-notification");
    permission = await n.isPermissionGranted();
    if (!permission) permission = (await n.requestPermission()) === "granted";
    return permission;
  }
  if (typeof Notification === "undefined") return (permission = false);
  if (Notification.permission === "granted") return (permission = true);
  if (Notification.permission !== "denied") permission = (await Notification.requestPermission()) === "granted";
  else permission = false;
  return permission;
}

/** 发送系统通知：Tauri 下走 Windows Toast（notification 插件），浏览器下走 Web Notification。 */
export async function systemNotify(title: string, body: string): Promise<boolean> {
  if (!(await ensureNotifyPermission())) return false;
  if (inTauri) {
    const { sendNotification } = await import("@tauri-apps/plugin-notification");
    sendNotification({ title, body });
    return true;
  }
  new Notification(title, { body });
  return true;
}
