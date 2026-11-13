import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, CheckCheck, Inbox, Search, Sparkles, Timer, Zap } from "lucide-react";
import { AnimatePresence } from "motion/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { EmptyState, PageHeader, Segmented, StatTile } from "@/components/common";
import { ItemCard } from "@/components/item-card";
import { ItemPanel } from "@/components/item-panel";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Category, type ItemList, type Settings, type Stats } from "@/lib/api";
import { CATEGORY_KEYS, CATEGORY_META, greeting, parseDate, weekday } from "@/lib/format";
import { cn } from "@/lib/utils";
import { slotLabel, useScoreSchedule } from "@/lib/scoring";

export type FeedView = "featured" | "all" | "starred";

const TITLES: Record<FeedView, string> = { featured: "今日精选", all: "全部动态", starred: "收藏" };

function dayKey(s: string | null): string {
  const d = parseDate(s);
  if (!d) return "更早";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diff = Math.floor((today.getTime() - new Date(d).setHours(0, 0, 0, 0)) / 86400000);
  if (diff <= 0) return "今天";
  if (diff === 1) return "昨天";
  if (diff < 7) return `${d.getMonth() + 1}月${d.getDate()}日 · ${weekday(d)}`;
  return "更早";
}

export function FeedPage({ view, category, onCategory }: { view: FeedView; category: Category | null; onCategory: (c: Category | null) => void }) {
  const qc = useQueryClient();
  const [read, setRead] = useState<"all" | "unread">("all");
  const [sort, setSort] = useState<"score" | "time">(view === "all" ? "time" : "score");
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  const searchRef = useRef<HTMLInputElement>(null);

  useEffect(() => setSort(view === "all" ? "time" : "score"), [view]);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(q), 250);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => {
    const fn = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        searchRef.current?.focus();
      }
      if (e.key === "Escape") setSelected(null);
    };
    window.addEventListener("keydown", fn);
    return () => window.removeEventListener("keydown", fn);
  }, []);

  const params = new URLSearchParams({
    view: view === "featured" ? "featured" : "all",
    read,
    sort,
    limit: "80",
    ...(category ? { category } : {}),
    ...(debounced ? { q: debounced } : {}),
    ...(view === "featured" ? { days: "14" } : {}),
    ...(view === "starred" ? { starred: "true" } : {}),
  });
  const { data, isLoading } = useQuery({
    queryKey: ["items", params.toString()],
    queryFn: () => api<ItemList>(`/api/items?${params}`),
    refetchInterval: 60_000,
  });
  const { data: stats } = useQuery({ queryKey: ["stats"], queryFn: () => api<Stats>("/api/stats"), refetchInterval: 30_000 });
  const { data: sched } = useScoreSchedule();
  const { data: settings } = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/api/settings") });

  const markAll = useMutation({
    mutationFn: () => api<{ updated: number }>("/api/items/mark-read", { method: "POST", json: { ids: data?.items.map((i) => i.id) ?? [] } }),
    onSuccess: (r) => {
      toast.success(`已标记 ${r.updated} 条为已读`);
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  // 选中即标记已读（像邮件客户端）
  const select = (id: number) => {
    setSelected(id);
    const it = data?.items.find((x) => x.id === id);
    if (it && !it.is_read) {
      api(`/api/items/${id}`, { method: "PATCH", json: { is_read: true } }).then(() => {
        qc.invalidateQueries({ queryKey: ["items"] });
        qc.invalidateQueries({ queryKey: ["stats"] });
      });
    }
  };

  const groups = useMemo(() => {
    const items = data?.items ?? [];
    if (sort !== "time") return [{ key: "", items }];
    const map = new Map<string, typeof items>();
    for (const it of items) {
      const k = dayKey(it.published_at || it.fetched_at);
      map.set(k, [...(map.get(k) ?? []), it]);
    }
    return [...map.entries()].map(([key, items]) => ({ key, items }));
  }, [data, sort]);

  const now = new Date();
  const subtitle =
    view === "featured"
      ? `${greeting(settings?.profile.name ?? "同学")} · ${now.getMonth() + 1}月${now.getDate()}日 ${weekday(now)} · 近两周按重要性排序`
      : view === "all"
        ? "所有信源的最新动态，低于浏览下限的噪声已折叠"
        : "你收藏的条目";

  let idx = 0;
  return (
    <div className="flex h-full gap-3">
      <div className="min-w-0 flex-1 overflow-y-auto">
        <PageHeader title={category ? CATEGORY_META[category].label : TITLES[view]} subtitle={subtitle}>
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              ref={searchRef}
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="搜索标题、正文、摘要"
              className="h-9 w-64 rounded-xl bg-foreground/[0.05] pr-12 pl-9 text-[13px] outline-none placeholder:text-muted-foreground/80 focus:bg-foreground/[0.07] focus:ring-2 focus:ring-ring/40"
            />
            <kbd className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 rounded-md bg-foreground/[0.06] px-1.5 font-mono text-[10.5px] text-muted-foreground">
              Ctrl K
            </kbd>
          </div>
        </PageHeader>

        {view === "featured" && !category && stats && (
          <div className="grid grid-cols-2 gap-3 px-10 pb-6 xl:grid-cols-4">
            <StatTile label="未读" value={stats.unread} icon={<Inbox className="size-3.5" />} accent="bg-primary/12 text-primary" />
            <StatTile label="7 天内截止" value={stats.deadlines_7d} icon={<CalendarClock className="size-3.5" />} accent="bg-hot/12 text-hot" />
            <StatTile label="24 小时新增" value={stats.today} icon={<Zap className="size-3.5" />} accent="bg-good/12 text-good" />
            <StatTile
              label="今日模型调用"
              value={stats.llm_usage_today.calls ?? 0}
              hint={
                sched?.running
                  ? "正在批量打分…"
                  : `${stats.pending_llm ? `${stats.pending_llm} 条待打分 · ` : ""}下次打分 ${sched ? slotLabel(sched.next_at) : "—"}${sched?.next_is_peak ? "（峰时）" : ""}`
              }
              icon={<Sparkles className="size-3.5" />}
              accent="bg-warm/15 text-warm"
            />
          </div>
        )}

        <div className="sticky top-0 z-10 flex flex-wrap items-center gap-2 bg-surface/85 px-10 py-3 backdrop-blur-xl">
          <div className="flex flex-wrap items-center gap-1">
            <FilterPill active={!category} onClick={() => onCategory(null)}>
              全部
            </FilterPill>
            {CATEGORY_KEYS.map((c) => (
              <FilterPill key={c} active={category === c} onClick={() => onCategory(category === c ? null : c)}>
                <span className={cn("size-1.5 rounded-full", CATEGORY_META[c].dot)} />
                {CATEGORY_META[c].label}
                {stats?.unread_by_category[c] ? (
                  <span className="font-mono text-[11px] text-muted-foreground tabular">{stats.unread_by_category[c]}</span>
                ) : null}
              </FilterPill>
            ))}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <Segmented id="read" size="sm" value={read} onChange={setRead} options={[{ value: "all", label: "全部" }, { value: "unread", label: "未读" }]} />
            <Segmented
              id="sort"
              size="sm"
              value={sort}
              onChange={setSort}
              options={[
                { value: "score", label: <><Sparkles className="size-3" />分数</> },
                { value: "time", label: <><Timer className="size-3" />时间</> },
              ]}
            />
            <Button variant="ghost" size="sm" className="h-7 rounded-lg text-[12.5px] text-muted-foreground" onClick={() => markAll.mutate()} disabled={!data?.items.length}>
              <CheckCheck className="size-3.5" /> 本页已读
            </Button>
          </div>
        </div>

        <div className="px-6 pb-16">
          {isLoading ? (
            <div className="space-y-2 px-4 pt-2">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="flex gap-4 py-3">
                  <Skeleton className="size-10 rounded-full" />
                  <div className="flex-1 space-y-2">
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-3 w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          ) : !data?.items.length ? (
            <EmptyState
              icon={<Inbox className="size-6" />}
              title={debounced ? "没有搜到相关内容" : "这里暂时很安静"}
              hint={debounced ? "换个关键词试试，至少 3 个字时会启用全文检索" : "后端会按各信源的间隔自动抓取；也可以去「信源管理」手动抓一次"}
            />
          ) : (
            groups.map((g) => (
              <div key={g.key || "all"}>
                {g.key && <div className="px-4 pt-5 pb-1.5 text-[12px] font-medium tracking-wide text-muted-foreground">{g.key}</div>}
                <AnimatePresence initial={true}>
                  {g.items.map((it) => (
                    <ItemCard key={it.id} item={it} index={idx++} selected={selected === it.id} onSelect={() => select(it.id)} />
                  ))}
                </AnimatePresence>
              </div>
            ))
          )}
          {data && data.total > data.items.length && (
            <div className="py-6 text-center text-[12px] text-muted-foreground">
              显示前 {data.items.length} 条，共 {data.total} 条 · 用搜索或分类缩小范围
            </div>
          )}
        </div>
      </div>
      <AnimatePresence mode="wait">{selected !== null && <ItemPanel key={selected} id={selected} onClose={() => setSelected(null)} />}</AnimatePresence>
    </div>
  );
}

function FilterPill({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex h-7 items-center gap-1.5 rounded-full px-3 text-[12.5px] font-medium transition-colors",
        active ? "bg-foreground text-background" : "text-muted-foreground hover:bg-foreground/[0.05] hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}
