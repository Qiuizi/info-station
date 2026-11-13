import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkCheck, CalendarClock, ExternalLink, Sparkles, Star, ThumbsDown, ThumbsUp, X } from "lucide-react";
import { motion } from "motion/react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, tweetOf, type Item } from "@/lib/api";
import { CATEGORY_META, dateTime, deadlineLabel, hostOf, relativeTime, scoreTone } from "@/lib/format";
import { openExternal } from "@/lib/platform";
import { cn } from "@/lib/utils";

import { CategoryDot, Chip, ScoreRing } from "./common";
import { TweetAuthor, TweetAvatar, TweetStats, useShowAvatars } from "./tweet";

export function ItemPanel({ id, onClose }: { id: number; onClose: () => void }) {
  const qc = useQueryClient();
  const { data: item, isLoading } = useQuery({ queryKey: ["item", id], queryFn: () => api<Item>(`/api/items/${id}`) });
  const patch = useMutation({
    mutationFn: (body: Partial<Pick<Item, "is_read" | "starred" | "feedback">>) =>
      api(`/api/items/${id}`, { method: "PATCH", json: body }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["item", id] });
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  return (
    <motion.aside
      key={id}
      initial={{ opacity: 0, x: 24 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 24 }}
      transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
      className="flex h-full w-[440px] shrink-0 flex-col overflow-hidden rounded-[20px] bg-card shadow-pop"
    >
      <div className="flex items-center justify-between px-5 pt-4">
        <span className="text-[12px] font-medium tracking-wide text-muted-foreground">详情</span>
        <div className="flex items-center gap-0.5">
          {item && (
            <>
              <IconBtn label={item.starred ? "取消收藏" : "收藏"} onClick={() => patch.mutate({ starred: !item.starred })}>
                <Star className={cn("size-4", item.starred && "fill-warm text-warm")} />
              </IconBtn>
              <IconBtn label={item.is_read ? "标为未读" : "标为已读"} onClick={() => patch.mutate({ is_read: !item.is_read })}>
                <BookmarkCheck className={cn("size-4", item.is_read && "text-primary")} />
              </IconBtn>
            </>
          )}
          <IconBtn label="关闭 (Esc)" onClick={onClose}>
            <X className="size-4" />
          </IconBtn>
        </div>
      </div>

      {isLoading || !item ? (
        <div className="space-y-3 p-6">
          <Skeleton className="h-6 w-4/5" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <div className="flex-1 overflow-y-auto px-6 pt-3 pb-8">
          <div className="flex items-center gap-2 text-[12px] text-muted-foreground">
            <span className={cn("flex items-center gap-1.5 font-medium", CATEGORY_META[item.category]?.text)}>
              <CategoryDot category={item.category} />
              {CATEGORY_META[item.category]?.label}
            </span>
            <span className="text-foreground/20">·</span>
            <span>{item.source_name}</span>
            <Chip className="h-[18px] px-1.5 text-[10.5px]">{item.tier}</Chip>
          </div>
          <TweetHeader item={item} />
          {!tweetOf(item) && <h2 className="mt-3 text-[19px] leading-[1.5] font-semibold tracking-[-0.01em]">{item.title}</h2>}
          <div className="mt-2 text-[12.5px] text-muted-foreground tabular">
            {dateTime(item.published_at) || relativeTime(item.fetched_at)} · {hostOf(item.url)}
          </div>

          <div className="mt-5 flex items-center gap-4 rounded-2xl bg-surface-2 p-4">
            <ScoreRing score={item.score} size={52} />
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2 text-[13px] font-medium">
                <span className={scoreTone(item.score).text}>{scoreTone(item.score).label}</span>
                {item.relevant && <Chip tone="good">与你相关</Chip>}
              </div>
              <div className="mt-1 text-[12px] leading-relaxed text-muted-foreground">
                {item.scored_by === "rule" ? "规则打分" : `${item.scored_by} 打分`}
                {item.llm_scores && item.llm_scores.length > 1 && ` · 两次独立打分 ${item.llm_scores.join(" / ")}`}
                {item.scored_by !== "rule" && ` · 规则分 ${item.rule_score}`}
              </div>
            </div>
          </div>

          {item.summary && (
            <Section icon={<Sparkles className="size-3.5" />} title="一句话">
              <p className="text-[14px] leading-[1.75]">{item.summary}</p>
            </Section>
          )}
          {(item.deadline || item.action) && (
            <Section icon={<CalendarClock className="size-3.5" />} title="行动">
              {item.deadline && (
                <div className="flex items-baseline gap-2">
                  <span className="font-mono text-[15px] font-semibold tabular">{dateTime(item.deadline)}</span>
                  <span className="text-[12.5px] text-hot">{deadlineLabel(item.deadline)}</span>
                </div>
              )}
              {item.action && <p className="mt-1.5 text-[13.5px] leading-relaxed">{item.action}</p>}
              {item.deadline_note && <p className="mt-1.5 text-[12.5px] leading-relaxed text-muted-foreground">“{item.deadline_note}”</p>}
            </Section>
          )}
          {(item.reason || item.audience) && (
            <Section title="打分理由">
              {item.audience && (
                <p className="mb-1 text-[12.5px]">
                  适用对象：{item.audience}
                  {item.eligible === 0 && <span className="ml-1.5 text-muted-foreground">（你不在范围内）</span>}
                </p>
              )}
              {item.reason && <p className="text-[13px] leading-relaxed text-muted-foreground">{item.reason}</p>}
            </Section>
          )}
          {item.tags.length > 0 && (
            <div className="mt-5 flex flex-wrap gap-1.5">
              {item.tags.map((t) => (
                <Chip key={t}>{t}</Chip>
              ))}
            </div>
          )}
          {!tweetOf(item) && (item.content || item.raw_summary) && (
            <Section title="原文摘录">
              <p className="line-clamp-[14] text-[13px] leading-[1.8] whitespace-pre-line text-muted-foreground">
                {(item.content || item.raw_summary).slice(0, 1200)}
              </p>
            </Section>
          )}
          {item.related && item.related.length > 0 && (
            <Section title={`同一事件的其他报道 · ${item.related.length}`}>
              <div className="space-y-1">
                {item.related.map((r) => (
                  <button
                    key={r.id}
                    onClick={() => openExternal(r.url)}
                    className="flex w-full items-center justify-between gap-3 rounded-xl px-3 py-2 text-left text-[13px] hover:bg-foreground/[0.04]"
                  >
                    <span className="truncate">{r.title}</span>
                    <span className="shrink-0 text-[12px] text-muted-foreground">{r.source_name}</span>
                  </button>
                ))}
              </div>
            </Section>
          )}
        </div>
      )}

      {item && (
        <div className="flex items-center gap-2 bg-gradient-to-t from-card via-card to-transparent px-5 pt-3 pb-4">
          <Button className="h-9 flex-1 rounded-xl" onClick={() => openExternal(item.url)}>
            <ExternalLink className="size-4" /> 打开原文
          </Button>
          <IconBtn label="有用：以后多推这类" active={item.feedback === 1} onClick={() => patch.mutate({ feedback: item.feedback === 1 ? 0 : 1 })}>
            <ThumbsUp className="size-4" />
          </IconBtn>
          <IconBtn label="没用：以后少推这类" active={item.feedback === -1} onClick={() => patch.mutate({ feedback: item.feedback === -1 ? 0 : -1 })}>
            <ThumbsDown className="size-4" />
          </IconBtn>
        </div>
      )}
    </motion.aside>
  );
}

function Section({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="mt-6">
      <div className="mb-2 flex items-center gap-1.5 text-[12px] font-medium text-muted-foreground">
        {icon}
        {title}
      </div>
      {children}
    </section>
  );
}

function IconBtn({ label, onClick, children, active }: { label: string; onClick: () => void; children: React.ReactNode; active?: boolean }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={onClick}
          className={cn("rounded-xl text-muted-foreground hover:text-foreground", active && "bg-primary/10 text-primary")}
        >
          {children}
        </Button>
      </TooltipTrigger>
      <TooltipContent>{label}</TooltipContent>
    </Tooltip>
  );
}

function TweetHeader({ item }: { item: Item }) {
  const tweet = tweetOf(item);
  const show = useShowAvatars();
  if (!tweet) return null;
  return (
    <div className="mt-3 rounded-2xl bg-surface-2 p-4">
      <div className="flex items-center gap-3 text-[13.5px]">
        <TweetAvatar tweet={tweet} size={36} show={show} />
        <div className="min-w-0">
          <TweetAuthor tweet={tweet} className="flex-col items-start gap-0" />
        </div>
      </div>
      <p className="mt-3 text-[15px] leading-[1.65] whitespace-pre-line break-words">{item.content || item.title}</p>
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12.5px]">
        <TweetStats tweet={tweet} />
        {tweet.query && <span className="text-muted-foreground">来自：{tweet.query}</span>}
      </div>
    </div>
  );
}
