import { useQuery } from "@tanstack/react-query";
import { Heart, MessageCircle, Repeat2 } from "lucide-react";
import { useState } from "react";

import { api, type Settings, type TweetExtra } from "@/lib/api";
import { cn } from "@/lib/utils";

export function compactCount(n?: number | null): string {
  if (!n) return "0";
  if (n >= 10000) return `${(n / 10000).toFixed(n >= 100000 ? 0 : 1).replace(/\.0$/, "")}万`;
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(n);
}

/** 设置 → X → 显示头像（头像图床在大陆通常也要走代理，默认关闭，只显示首字母） */
export function useShowAvatars(): boolean {
  const { data } = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/api/settings"), staleTime: 60_000 });
  return !!data?.x?.show_avatars;
}

export function TweetAvatar({ tweet, size = 36, show }: { tweet: TweetExtra; size?: number; show: boolean }) {
  const [failed, setFailed] = useState(false);
  const initial = (tweet.name || tweet.handle || "?").trim().charAt(0).toUpperCase();
  const style = { width: size, height: size };
  if (show && tweet.avatar && !failed) {
    return (
      <img
        src={tweet.avatar.replace("_normal.", "_bigger.")}
        alt=""
        style={style}
        className="shrink-0 rounded-full bg-muted object-cover"
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    );
  }
  return (
    <span style={style} className="grid shrink-0 place-items-center rounded-full bg-foreground/[0.07] text-[13px] font-semibold text-foreground/70">
      {initial}
    </span>
  );
}

export function TweetAuthor({ tweet, className }: { tweet: TweetExtra; className?: string }) {
  return (
    <span className={cn("flex min-w-0 items-baseline gap-1.5", className)}>
      <span className="truncate font-semibold text-foreground">{tweet.name || `@${tweet.handle}`}</span>
      {tweet.name && <span className="truncate text-muted-foreground">@{tweet.handle}</span>}
    </span>
  );
}

export function TweetStats({ tweet, className }: { tweet: TweetExtra; className?: string }) {
  return (
    <span className={cn("flex items-center gap-3 tabular text-muted-foreground", className)}>
      <span className="flex items-center gap-1" title="回复">
        <MessageCircle className="size-3.5" />
        {compactCount(tweet.replies)}
      </span>
      <span className="flex items-center gap-1" title="转发">
        <Repeat2 className="size-3.5" />
        {compactCount(tweet.retweets)}
      </span>
      <span className="flex items-center gap-1" title="点赞">
        <Heart className="size-3.5" />
        {compactCount(tweet.likes)}
      </span>
    </span>
  );
}
