import { CalendarClock, Flame, Layers, Star } from "lucide-react";
import { motion } from "motion/react";

import { tweetOf, type Item } from "@/lib/api";
import { CATEGORY_META, daysLeft, deadlineLabel, relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { CategoryDot, Chip, ScoreRing } from "./common";
import { TweetAuthor, TweetAvatar, TweetStats, useShowAvatars } from "./tweet";

export function DeadlineChip({ deadline }: { deadline: string | null }) {
  if (!deadline) return null;
  const n = daysLeft(deadline) ?? 99;
  const tone = n < 0 ? "muted" : n <= 3 ? "hot" : n <= 10 ? "warm" : "primary";
  return (
    <Chip tone={tone}>
      <CalendarClock className="size-3" />
      {deadlineLabel(deadline)}
    </Chip>
  );
}

export function ItemCard({ item, selected, onSelect, index }: { item: Item; selected: boolean; onSelect: () => void; index: number }) {
  const tweet = tweetOf(item);
  const showAvatars = useShowAvatars();
  const summary = item.summary || item.raw_summary;
  if (tweet) {
    // 推文：作者一行 + 正文（不再把截断的“标题”和正文重复显示）+ 模型摘要 + 互动数
    return (
      <motion.button
        type="button"
        layout="position"
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        exit={{ opacity: 0, scale: 0.98 }}
        transition={{ duration: 0.28, delay: Math.min(index, 12) * 0.022, ease: [0.22, 1, 0.36, 1] }}
        onClick={onSelect}
        className={cn(
          "group relative flex w-full items-start gap-4 rounded-2xl px-4 py-3.5 text-left transition-colors duration-200",
          selected ? "bg-accent" : "hover:bg-foreground/[0.035]",
        )}
      >
        <ScoreRing score={item.score} muted={item.is_read} />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5 text-[13px]">
            <TweetAvatar tweet={tweet} size={22} show={showAvatars} />
            <TweetAuthor tweet={tweet} />
            <span className="text-foreground/20">·</span>
            <span className="shrink-0 text-[12px] text-muted-foreground tabular">{relativeTime(item.published_at || item.fetched_at)}</span>
            {!item.is_read && <span className="size-1.5 shrink-0 rounded-full bg-primary" aria-label="未读" />}
          </div>
          <p
            className={cn(
              "mt-1.5 line-clamp-4 text-[14px] leading-[1.6] whitespace-pre-line break-words",
              item.is_read && "text-muted-foreground",
            )}
          >
            {item.content || item.title}
          </p>
          {item.summary && <p className="mt-1.5 line-clamp-2 text-[12.5px] leading-relaxed text-muted-foreground">✦ {item.summary}</p>}
          <div className="mt-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[12px] text-muted-foreground">
            <span className={cn("flex items-center gap-1.5 font-medium", CATEGORY_META[item.category]?.text)}>
              <CategoryDot category={item.category} />
              {item.source_name}
            </span>
            <TweetStats tweet={tweet} className="text-[12px]" />
            <DeadlineChip deadline={item.deadline} />
            {item.tags.slice(0, 2).map((t) => (
              <Chip key={t}>{t}</Chip>
            ))}
            {item.starred && <Star className="size-3.5 fill-warm text-warm" />}
          </div>
        </div>
      </motion.button>
    );
  }
  return (
    <motion.button
      type="button"
      layout="position"
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.98 }}
      transition={{ duration: 0.28, delay: Math.min(index, 12) * 0.022, ease: [0.22, 1, 0.36, 1] }}
      onClick={onSelect}
      className={cn(
        "group relative flex w-full items-start gap-4 rounded-2xl px-4 py-3.5 text-left transition-colors duration-200",
        selected ? "bg-accent" : "hover:bg-foreground/[0.035]",
      )}
    >
      <ScoreRing score={item.score} muted={item.is_read} />
      <div className="min-w-0 flex-1">
        <div className="flex items-start gap-2">
          {!item.is_read && <span className="mt-[9px] size-1.5 shrink-0 rounded-full bg-primary" aria-label="未读" />}
          <h3
            className={cn(
              "line-clamp-2 text-[14.5px] leading-[1.55] font-medium tracking-[-0.005em]",
              item.is_read && "text-muted-foreground",
            )}
          >
            {item.title}
          </h3>
        </div>
        {summary && <p className="mt-1 line-clamp-2 text-[13px] leading-relaxed text-muted-foreground">{summary}</p>}
        <div className="mt-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 text-[12px] text-muted-foreground">
          <span className={cn("flex items-center gap-1.5 font-medium", CATEGORY_META[item.category]?.text)}>
            <CategoryDot category={item.category} />
            {item.source_name}
          </span>
          <span className="text-foreground/20">·</span>
          <span className="tabular">{relativeTime(item.published_at || item.fetched_at)}</span>
          <DeadlineChip deadline={item.deadline} />
          {item.tags.slice(0, 3).map((t) => (
            <Chip key={t}>{t}</Chip>
          ))}
          {(item.cluster_sources?.length ?? 1) > 1 && (
            <Chip tone="primary">
              <Layers className="size-3" />
              {item.cluster_sources!.length} 个来源
            </Chip>
          )}
          {(item.heat ?? 0) >= 2 && (
            <Chip tone="hot">
              <Flame className="size-3" />
              热
            </Chip>
          )}
          {item.starred && <Star className="size-3.5 fill-warm text-warm" />}
        </div>
      </div>
    </motion.button>
  );
}
