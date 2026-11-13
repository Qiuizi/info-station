import type { Category } from "./api";

export const CATEGORY_META: Record<Category, { label: string; dot: string; soft: string; text: string }> = {
  school: { label: "学校通知", dot: "bg-sky-500", soft: "bg-sky-500/10", text: "text-sky-600 dark:text-sky-400" },
  competition: {
    label: "竞赛科研",
    dot: "bg-violet-500",
    soft: "bg-violet-500/10",
    text: "text-violet-600 dark:text-violet-400",
  },
  tech: { label: "技术动态", dot: "bg-emerald-500", soft: "bg-emerald-500/10", text: "text-emerald-600 dark:text-emerald-400" },
  news: { label: "新闻话题", dot: "bg-amber-500", soft: "bg-amber-500/10", text: "text-amber-600 dark:text-amber-400" },
  email: { label: "邮件", dot: "bg-rose-500", soft: "bg-rose-500/10", text: "text-rose-600 dark:text-rose-400" },
  feishu: { label: "飞书", dot: "bg-blue-500", soft: "bg-blue-500/10", text: "text-blue-600 dark:text-blue-400" },
};

export const CATEGORY_KEYS: Category[] = ["school", "competition", "tech", "news", "email"];

export function parseDate(s?: string | null): Date | null {
  if (!s) return null;
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? null : d;
}

export function relativeTime(s?: string | null): string {
  const d = parseDate(s);
  if (!d) return "";
  const diff = (Date.now() - d.getTime()) / 1000;
  if (diff < 0) {
    const f = -diff;
    if (f < 3600) return `${Math.max(1, Math.round(f / 60))} 分钟后`;
    if (f < 86400) return `${Math.round(f / 3600)} 小时后`;
    return `${Math.round(f / 86400)} 天后`;
  }
  if (diff < 60) return "刚刚";
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  if (diff < 86400 * 2) return "昨天";
  if (diff < 86400 * 7) return `${Math.floor(diff / 86400)} 天前`;
  return shortDate(s);
}

export function shortDate(s?: string | null): string {
  const d = parseDate(s);
  if (!d) return "";
  const now = new Date();
  const md = `${d.getMonth() + 1}月${d.getDate()}日`;
  return d.getFullYear() === now.getFullYear() ? md : `${d.getFullYear()}年${md}`;
}

export function dateTime(s?: string | null): string {
  const d = parseDate(s);
  if (!d) return "";
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  return `${shortDate(s)} ${hm}`;
}

const WEEK = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];
export function weekday(s?: string | Date | null): string {
  const d = s instanceof Date ? s : parseDate(s ?? null);
  return d ? WEEK[d.getDay()] : "";
}

export function daysLeft(s?: string | null): number | null {
  const d = parseDate(s);
  if (!d) return null;
  return (d.getTime() - Date.now()) / 86400000;
}

export function deadlineLabel(s?: string | null): string {
  const n = daysLeft(s);
  if (n === null) return "";
  if (n < 0) return "已截止";
  if (n < 1) {
    const h = Math.max(1, Math.round(n * 24));
    return `${h} 小时后截止`;
  }
  return `还剩 ${Math.floor(n)} 天`;
}

export function scoreTone(score: number): { ring: string; text: string; bg: string; label: string } {
  if (score >= 85) return { ring: "var(--hot)", text: "text-hot", bg: "bg-hot/10", label: "必看" };
  if (score >= 70) return { ring: "var(--warm)", text: "text-warm", bg: "bg-warm/12", label: "推荐" };
  if (score >= 50) return { ring: "var(--primary)", text: "text-primary", bg: "bg-primary/10", label: "可读" };
  return { ring: "var(--muted-foreground)", text: "text-muted-foreground", bg: "bg-muted", label: "一般" };
}

export function hostOf(url?: string | null): string {
  if (!url) return "";
  try {
    return new URL(url.replace("{RSSHUB}", "http://rsshub.local")).host;
  } catch {
    return url;
  }
}

export function greeting(name: string): string {
  const h = new Date().getHours();
  const hi = h < 6 ? "夜深了" : h < 11 ? "早上好" : h < 13 ? "中午好" : h < 18 ? "下午好" : "晚上好";
  return `${hi}，${name}`;
}
