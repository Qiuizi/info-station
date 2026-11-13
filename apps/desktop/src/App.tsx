import { useQuery } from "@tanstack/react-query";
import { Loader2, PlugZap } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

import { EmptyState } from "@/components/common";
import { Notifier } from "@/components/notifier";
import { Sidebar, type Page } from "@/components/sidebar";
import { useWindowState } from "@/components/window-controls";
import { api, API_BASE, type Category, type Health } from "@/lib/api";
import { inTauri } from "@/lib/platform";
import { cn } from "@/lib/utils";
import { BriefPage } from "@/pages/brief";
import { DeadlinesPage } from "@/pages/deadlines";
import { FeedPage } from "@/pages/feed";
import { SettingsPage } from "@/pages/settings";
import { SourcesPage } from "@/pages/sources";

function initialPage(): Page {
  const p = new URLSearchParams(window.location.search).get("page") as Page | null;
  return p ?? (localStorage.getItem("info-station-page") as Page) ?? "featured";
}

export default function App() {
  const [page, setPage] = useState<Page>(initialPage);
  const [category, setCategory] = useState<Category | null>(null);
  const { data: health, isLoading } = useQuery({
    queryKey: ["health"],
    queryFn: () => api<Health>("/api/health"),
    refetchInterval: (q) => (q.state.data ? 30_000 : 2_000),
    retry: false,
  });
  useEffect(() => localStorage.setItem("info-station-page", page), [page]);
  const win = useWindowState();

  let content: React.ReactNode;
  if (!health) {
    content = (
      <div className="grid h-full place-items-center">
        {isLoading ? (
          <Loader2 className="size-6 animate-spin text-muted-foreground" />
        ) : (
          <EmptyState icon={<PlugZap className="size-6" />} title="正在等待本地后端启动…" hint={`连接 ${API_BASE}。开发模式请运行 dev.ps1，或在 backend 目录执行 uv run python run.py`} />
        )}
      </div>
    );
  } else if (page === "featured" || page === "all" || page === "starred") {
    content = <FeedPage view={page} category={category} onCategory={setCategory} />;
  } else if (page === "deadlines") content = <DeadlinesPage />;
  else if (page === "brief") content = <BriefPage />;
  else if (page === "sources") content = <SourcesPage />;
  else content = <SettingsPage />;

  return (
    // 无系统边框的窗口：整窗圆角由这一层画（窗口本身透明），最大化时贴边不留圆角
    <div
      data-tauri-drag-region
      className={cn("app-window flex h-full", inTauri && !win.maximized && "app-window-rounded")}
    >
      <Sidebar page={page} onPage={setPage} category={category} onCategory={setCategory} health={health} focused={win.focused} />
      <main data-tauri-drag-region className="min-w-0 flex-1 py-2 pr-2">
        <div className="h-full overflow-hidden rounded-[14px] bg-surface shadow-surface">
          <AnimatePresence mode="wait">
            <motion.div
              key={page + (category ?? "")}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.18, ease: "easeOut" }}
              className="h-full"
            >
              {content}
            </motion.div>
          </AnimatePresence>
        </div>
      </main>
      <Notifier enabled={!!health} />
    </div>
  );
}
