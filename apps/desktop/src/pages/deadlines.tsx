import { useQuery } from "@tanstack/react-query";
import { CalendarCheck2 } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";

import { CategoryDot, EmptyState, PageHeader } from "@/components/common";
import { ItemPanel } from "@/components/item-panel";
import { api, type Item } from "@/lib/api";
import { dateTime, weekday } from "@/lib/format";
import { cn } from "@/lib/utils";

const LANES = [
  { key: "urgent", title: "3 天内", hint: "抓紧处理", test: (d: number) => d >= 0 && d < 3, tone: "text-hot", bar: "bg-hot" },
  { key: "week", title: "本周", hint: "3–7 天", test: (d: number) => d >= 3 && d < 7, tone: "text-warm", bar: "bg-warm" },
  { key: "month", title: "一个月内", hint: "7–30 天", test: (d: number) => d >= 7 && d < 30, tone: "text-primary", bar: "bg-primary" },
  { key: "later", title: "更远", hint: "30 天以后", test: (d: number) => d >= 30, tone: "text-muted-foreground", bar: "bg-muted-foreground" },
  { key: "past", title: "刚截止", hint: "过去 48 小时", test: (d: number) => d < 0, tone: "text-muted-foreground", bar: "bg-muted-foreground/50" },
];

export function DeadlinesPage() {
  const [selected, setSelected] = useState<number | null>(null);
  const { data = [], isLoading } = useQuery({ queryKey: ["deadlines"], queryFn: () => api<Item[]>("/api/deadlines"), refetchInterval: 60_000 });
  const upcoming = data.filter((d) => (d.days_left ?? 0) >= 0).length;

  return (
    <div className="flex h-full gap-3">
      <div className="min-w-0 flex-1 overflow-y-auto">
        <PageHeader title="截止日期看板" subtitle={`从通知正文里抽取的报名 / 提交截止时间 · ${upcoming} 项待办`} />
        {!isLoading && data.length === 0 ? (
          <EmptyState icon={<CalendarCheck2 className="size-6" />} title="暂时没有临近的截止日期" hint="模型或规则从通知里识别到截止时间后，会自动出现在这里" />
        ) : (
          <div className="grid auto-cols-[minmax(232px,1fr)] grid-flow-col gap-4 overflow-x-auto px-10 pb-10">
            {LANES.map((lane) => {
              const items = data.filter((d) => lane.test(d.days_left ?? 0));
              return (
                <div key={lane.key} className="flex min-h-[420px] flex-col rounded-[20px] bg-surface-2 p-2.5">
                  <div className="flex items-baseline justify-between px-2.5 pt-1.5 pb-3">
                    <div className="flex items-center gap-2">
                      <span className={cn("h-3.5 w-1 rounded-full", lane.bar)} />
                      <span className="text-[14px] font-semibold">{lane.title}</span>
                      <span className="font-mono text-[12px] text-muted-foreground tabular">{items.length}</span>
                    </div>
                    <span className="text-[11.5px] text-muted-foreground">{lane.hint}</span>
                  </div>
                  <div className="space-y-2">
                    {items.map((it, i) => (
                      <motion.button
                        key={it.id}
                        initial={{ opacity: 0, y: 8 }}
                        animate={{ opacity: 1, y: 0 }}
                        transition={{ delay: i * 0.03, duration: 0.3 }}
                        onClick={() => setSelected(it.id)}
                        className={cn(
                          "w-full rounded-2xl bg-card p-3.5 text-left shadow-[0_1px_2px_rgb(0_0_0/0.04)] transition-all hover:-translate-y-0.5 hover:shadow-surface dark:shadow-none",
                          selected === it.id && "ring-2 ring-ring/50",
                          lane.key === "past" && "opacity-60",
                        )}
                      >
                        <div className="flex items-start justify-between gap-3">
                          <div className={cn("font-mono text-[22px] leading-none font-semibold tabular", lane.tone)}>
                            {(it.days_left ?? 0) < 0 ? "—" : (it.days_left ?? 0) < 1 ? `${Math.max(1, Math.round((it.days_left ?? 0) * 24))}h` : `${Math.floor(it.days_left ?? 0)}d`}
                          </div>
                          <div className="text-right text-[11.5px] leading-tight text-muted-foreground tabular">
                            {dateTime(it.deadline)}
                            <div>{weekday(it.deadline)}</div>
                          </div>
                        </div>
                        <div className="mt-2.5 line-clamp-3 text-[13px] leading-[1.6] font-medium">{it.title}</div>
                        {it.action && <div className="mt-1.5 line-clamp-2 text-[12px] leading-relaxed text-muted-foreground">{it.action}</div>}
                        <div className="mt-2.5 flex items-center gap-1.5 text-[11.5px] text-muted-foreground">
                          <CategoryDot category={it.category} />
                          <span className="truncate">{it.source_name}</span>
                          <span className="ml-auto font-mono tabular">{it.score}</span>
                        </div>
                      </motion.button>
                    ))}
                    {items.length === 0 && <div className="px-3 py-8 text-center text-[12px] text-muted-foreground/70">暂无</div>}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
      <AnimatePresence mode="wait">{selected !== null && <ItemPanel key={selected} id={selected} onClose={() => setSelected(null)} />}</AnimatePresence>
    </div>
  );
}
