import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { api, type NotificationRow } from "@/lib/api";
import { systemNotify } from "@/lib/platform";

/** 轮询后端的待发通知：Tauri 中用 Windows Toast，浏览器开发模式下用 Web Notification / 应用内 toast。 */
export function Notifier({ enabled }: { enabled: boolean }) {
  const seen = useRef(new Set<number>());
  const { data } = useQuery({
    queryKey: ["notifications"],
    queryFn: () => api<NotificationRow[]>("/api/notifications/pending"),
    refetchInterval: 45_000,
    enabled,
  });
  useEffect(() => {
    (async () => {
      for (const n of data ?? []) {
        if (seen.current.has(n.id)) continue;
        seen.current.add(n.id);
        const ok = await systemNotify(n.title, n.body).catch(() => false);
        if (!ok) toast(n.title, { description: n.body });
        await api(`/api/notifications/${n.id}/ack`, { method: "POST" }).catch(() => undefined);
      }
    })();
  }, [data]);
  return null;
}
