import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Layers, Loader2, Moon, Newspaper, RefreshCw, Rss, Settings2, Sparkles, Star, Sun } from "lucide-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { TrafficLights } from "@/components/window-controls";
import { useTheme } from "@/hooks/use-theme";
import { api, type Category, type Health, type Stats } from "@/lib/api";
import { CATEGORY_KEYS, CATEGORY_META } from "@/lib/format";
import { cn } from "@/lib/utils";
import { slotLabel, useScoreSchedule } from "@/lib/scoring";

export type Page = "featured" | "all" | "starred" | "deadlines" | "brief" | "sources" | "settings";

const NAV: { key: Page; label: string; icon: React.ReactNode }[] = [
  { key: "featured", label: "今日精选", icon: <Sparkles className="size-[17px]" /> },
  { key: "all", label: "全部动态", icon: <Layers className="size-[17px]" /> },
  { key: "deadlines", label: "截止看板", icon: <CalendarClock className="size-[17px]" /> },
  { key: "brief", label: "每日简报", icon: <Newspaper className="size-[17px]" /> },
  { key: "starred", label: "收藏", icon: <Star className="size-[17px]" /> },
];

export function Sidebar({ page, onPage, category, onCategory, health, focused = true }: {
  page: Page;
  onPage: (p: Page) => void;
  category: Category | null;
  onCategory: (c: Category | null) => void;
  health: Health | undefined;
  focused?: boolean;
}) {
  const qc = useQueryClient();
  const { theme, setTheme } = useTheme();
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: () => api<Stats>("/api/stats"), refetchInterval: 30_000, enabled: !!health });
  const { data: sched } = useScoreSchedule(!!health);
  const runAll = useMutation({
    mutationFn: () => api<{ ok: boolean; new: number }[]>("/api/sources/run-all", { method: "POST" }),
    onMutate: () => toast.info("正在抓取全部信源…"),
    onSuccess: (r) => {
      toast.success(`完成：新增 ${r.reduce((a, x) => a + (x.new || 0), 0)} 条`);
      qc.invalidateQueries();
    },
  });
  const dark = theme === "dark" || (theme === "system" && document.documentElement.classList.contains("dark"));
  const counts: Partial<Record<Page, number>> = { all: stats?.unread, deadlines: stats?.deadlines_7d };

  return (
    <aside data-tauri-drag-region className="flex w-[232px] shrink-0 flex-col px-3 pb-3 select-none">
      {/* 标题栏区域：红绿灯 + 可拖动，双击最大化 / 还原（和 macOS 一样） */}
      <div className="flex h-[46px] shrink-0 items-center pl-2" data-tauri-drag-region>
        <TrafficLights focused={focused} />
      </div>
      <div className="flex items-center gap-2.5 px-2.5 pb-6" data-tauri-drag-region>
        <img src="/favicon.svg" alt="" draggable={false} className="size-8 drop-shadow-[0_3px_6px_rgb(91_52_214/0.45)]" />
        <div className="leading-tight">
          <div className="text-[14px] font-semibold tracking-[-0.01em]">信息监控站</div>
          <div className="text-[11px] text-muted-foreground">Info Station</div>
        </div>
      </div>

      <nav className="space-y-0.5">
        {NAV.map((n) => (
          <NavItem key={n.key} active={page === n.key && !(n.key === "featured" && category)} onClick={() => { onCategory(null); onPage(n.key); }} icon={n.icon} label={n.label} count={counts[n.key]} />
        ))}
      </nav>

      <div className="mt-7 mb-1.5 px-3 text-[11.5px] font-medium text-muted-foreground/80">分类</div>
      <nav className="space-y-0.5">
        {CATEGORY_KEYS.map((c) => (
          <NavItem
            key={c}
            active={category === c}
            onClick={() => { onCategory(c); onPage("all"); }}
            icon={<span className={cn("m-[5px] size-[7px] rounded-full", CATEGORY_META[c].dot)} />}
            label={CATEGORY_META[c].label}
            count={stats?.unread_by_category[c]}
          />
        ))}
      </nav>

      <div className="mt-7 space-y-0.5">
        <NavItem active={page === "sources"} onClick={() => { onCategory(null); onPage("sources"); }} icon={<Rss className="size-[17px]" />} label="信源管理" />
        <NavItem active={page === "settings"} onClick={() => { onCategory(null); onPage("settings"); }} icon={<Settings2 className="size-[17px]" />} label="设置" />
      </div>

      <div className="mt-auto space-y-2">
        <div className="rounded-2xl bg-foreground/[0.04] p-3">
          <div className="flex items-center gap-2 text-[12px]">
            <span className={cn("size-1.5 rounded-full", !health ? "bg-destructive animate-pulse" : health.llm_paused ? "bg-warm" : "bg-good")} />
            <span className="font-medium">{health ? "后端运行中" : "未连接后端"}</span>
          </div>
          <div className="mt-1 text-[11.5px] leading-relaxed text-muted-foreground">
            {health ? (health.llm_paused ? `模型已暂停：${health.llm_error}，暂用规则分` : health.llm ? "DeepSeek 筛选已开启" : "规则筛选（未配置模型）") : "请先启动 backend"}
          </div>
          {health && sched && (
            <div className="mt-1 text-[11.5px] text-muted-foreground" title="每天一次，在 DeepSeek 谷时批量打分">
              {sched.running ? "正在批量打分…" : <>下次打分 <span className="font-mono tabular">{slotLabel(sched.next_at)}</span>{sched.next_is_peak && <span className="text-hot"> · 峰时</span>}</>}
            </div>
          )}
        </div>
        <div className="flex items-center gap-1 px-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => runAll.mutate()}
                disabled={!health || runAll.isPending}
                className="flex h-8 flex-1 items-center justify-center gap-1.5 rounded-xl text-[12.5px] text-muted-foreground transition-colors hover:bg-foreground/[0.05] hover:text-foreground disabled:opacity-50"
              >
                {runAll.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <RefreshCw className="size-3.5" />}
                立即刷新
              </button>
            </TooltipTrigger>
            <TooltipContent>抓取全部已启用信源</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={() => setTheme(dark ? "light" : "dark")}
                className="grid size-8 place-items-center rounded-xl text-muted-foreground transition-colors hover:bg-foreground/[0.05] hover:text-foreground"
              >
                {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
              </button>
            </TooltipTrigger>
            <TooltipContent>切换深浅色</TooltipContent>
          </Tooltip>
        </div>
      </div>
    </aside>
  );
}

function NavItem({ active, onClick, icon, label, count }: { active: boolean; onClick: () => void; icon: React.ReactNode; label: string; count?: number }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "relative flex h-[32px] w-full items-center gap-2.5 rounded-[8px] px-3 text-[13.5px] transition-colors",
        active ? "font-medium text-foreground" : "text-foreground/75 hover:bg-foreground/[0.035] hover:text-foreground",
      )}
    >
      {active && (
        <motion.span
          layoutId="nav-active"
          className="absolute inset-0 rounded-[8px] bg-black/[0.065] dark:bg-white/[0.09]"
          transition={{ type: "spring", bounce: 0.15, duration: 0.45 }}
        />
      )}
      <span className="relative z-10 flex items-center">{icon}</span>
      <span className="relative z-10 flex-1 text-left">{label}</span>
      {!!count && <span className="relative z-10 font-mono text-[11.5px] text-muted-foreground tabular">{count}</span>}
    </button>
  );
}
