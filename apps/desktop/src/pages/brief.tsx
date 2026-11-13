import { useQuery } from "@tanstack/react-query";
import { CalendarClock, ChevronLeft, ChevronRight, Copy, Newspaper, RefreshCw } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { toast } from "sonner";

import { CategoryDot, EmptyState } from "@/components/common";
import { ItemPanel } from "@/components/item-panel";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api, type Brief } from "@/lib/api";
import { dateTime, deadlineLabel, scoreTone, weekday } from "@/lib/format";
import { cn } from "@/lib/utils";

function shift(date: string, days: number) {
  const d = new Date(date + "T12:00:00");
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function BriefPage() {
  const [date, setDate] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const { data, isLoading, refetch, isFetching } = useQuery({
    queryKey: ["brief", date],
    queryFn: () => api<Brief>(date ? `/api/briefs/${date}` : "/api/briefs/today"),
  });
  const today = new Date();
  const isToday = !date;
  const d = data ? new Date(data.date + "T12:00:00") : today;

  return (
    <div className="flex h-full gap-3">
      <div className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[760px] px-10 pt-12 pb-20">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-[12.5px] font-medium tracking-[0.12em] text-muted-foreground uppercase">
              <Newspaper className="size-4" /> Daily Brief · 每日简报
            </div>
            <div className="flex items-center gap-1">
              <Button variant="ghost" size="icon-sm" className="rounded-xl" onClick={() => setDate(shift(data?.date ?? "", -1))} disabled={!data}>
                <ChevronLeft className="size-4" />
              </Button>
              <Button variant="ghost" size="icon-sm" className="rounded-xl" disabled={isToday} onClick={() => {
                const n = shift(data!.date, 1);
                const t = shift(new Date().toISOString().slice(0, 10), 0);
                setDate(n >= t ? null : n);
              }}>
                <ChevronRight className="size-4" />
              </Button>
              <Button variant="ghost" size="sm" className="rounded-xl text-muted-foreground" onClick={() => refetch()}>
                <RefreshCw className={cn("size-3.5", isFetching && "animate-spin")} /> 重新生成
              </Button>
              <Button variant="ghost" size="sm" className="rounded-xl text-muted-foreground" disabled={!data} onClick={() => {
                navigator.clipboard.writeText(data!.markdown);
                toast.success("已复制 Markdown");
              }}>
                <Copy className="size-3.5" /> 复制
              </Button>
            </div>
          </div>

          <div className="mt-8 flex items-end gap-5">
            <div className="font-mono text-[64px] leading-none font-semibold tracking-tighter tabular">{String(d.getDate()).padStart(2, "0")}</div>
            <div className="pb-1.5">
              <div className="text-[15px] font-medium">{d.getFullYear()}年{d.getMonth() + 1}月 · {weekday(d)}</div>
              <div className="mt-1 text-[13px] text-muted-foreground">规则编排，不调用模型 · 一事一条</div>
            </div>
          </div>

          {isLoading || !data ? (
            <div className="mt-10 space-y-3">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : (
            <>
              <p className="mt-8 text-[20px] leading-[1.6] font-medium tracking-[-0.01em]">{data.greeting}</p>
              <div className="mt-5 flex gap-6 text-[13px] text-muted-foreground">
                <span><b className="font-mono text-foreground tabular">{data.stats.new_items}</b> 条新抓取</span>
                <span><b className="font-mono text-foreground tabular">{data.stats.must}</b> 条必看</span>
                <span><b className="font-mono text-foreground tabular">{data.stats.deadlines}</b> 个截止</span>
              </div>
              {data.sections.every((s) => !s.items.length) && (
                <EmptyState icon={<Newspaper className="size-6" />} title="这一天没有可编排的条目" />
              )}
              {data.sections.filter((s) => s.items.length).map((sec, si) => (
                <motion.section
                  key={sec.key}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.05 * si }}
                  className={cn("mt-10", sec.key === "must" && "rounded-[22px] bg-surface-2 p-6")}
                >
                  <h2 className="mb-3 flex items-center gap-2 text-[13px] font-semibold tracking-wide text-muted-foreground">
                    {sec.key === "deadline" && <CalendarClock className="size-4 text-hot" />}
                    {sec.title}
                  </h2>
                  <ol className="space-y-1">
                    {sec.items.map((it, i) => (
                      <li key={it.id}>
                        <button
                          onClick={() => setSelected(it.id)}
                          className="group flex w-full gap-4 rounded-2xl px-3 py-3 text-left transition-colors hover:bg-foreground/[0.04]"
                        >
                          <span className={cn("w-6 shrink-0 pt-0.5 font-mono text-[13px] tabular", scoreTone(it.score).text)}>
                            {String(i + 1).padStart(2, "0")}
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-[15px] leading-[1.55] font-medium">{it.title}</span>
                            {(it.summary || it.raw_summary) && (
                              <span className="mt-1 line-clamp-2 block text-[13px] leading-relaxed text-muted-foreground">{it.summary || it.raw_summary}</span>
                            )}
                            <span className="mt-1.5 flex items-center gap-2 text-[12px] text-muted-foreground">
                              <CategoryDot category={it.category} />
                              {it.source_name}
                              {it.deadline && (
                                <span className="text-hot">· 截止 {dateTime(it.deadline)}（{deadlineLabel(it.deadline)}）</span>
                              )}
                              <span className="ml-auto font-mono tabular">{it.score}</span>
                            </span>
                          </span>
                        </button>
                      </li>
                    ))}
                  </ol>
                </motion.section>
              ))}
              <div className="mt-12 text-center text-[12px] text-muted-foreground/70">生成于 {dateTime(data.generated_at)}</div>
            </>
          )}
        </div>
      </div>
      <AnimatePresence mode="wait">{selected !== null && <ItemPanel key={selected} id={selected} onClose={() => setSelected(null)} />}</AnimatePresence>
    </div>
  );
}
