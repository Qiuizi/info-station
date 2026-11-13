// 本地后端 API（FastAPI，默认 http://127.0.0.1:8765）
export const API_BASE: string =
  (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://127.0.0.1:8765";
const TOKEN = import.meta.env.VITE_LOCAL_TOKEN as string | undefined;

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init?: RequestInit & { json?: unknown }): Promise<T> {
  const headers: Record<string, string> = { ...(init?.headers as Record<string, string>) };
  if (TOKEN) headers["X-Local-Token"] = TOKEN;
  let body = init?.body;
  if (init?.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  const res = await fetch(API_BASE + path, { ...init, headers, body });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export type Category = "school" | "competition" | "tech" | "news" | "email" | "feishu";

export interface Item {
  id: number;
  source_id: string;
  source_name: string;
  url: string;
  title: string;
  raw_summary: string;
  content?: string;
  summary: string;
  published_at: string | null;
  fetched_at: string;
  category: Category;
  tier: "T1" | "T2";
  status: string;
  score: number;
  rule_score: number;
  llm_scores: number[] | null;
  tags: string[];
  deadline: string | null;
  deadline_note: string;
  action: string;
  relevant: boolean;
  reason: string;
  is_read: boolean;
  starred: boolean;
  feedback: number;
  is_backfill: boolean;
  scored_by: string;
  audience?: string;
  eligible?: number | null;
  dims?: Partial<Record<"relevance" | "action" | "weight" | "novelty" | "credibility", number>> | null;
  rank_bonus?: number;
  heat?: number;
  cluster_size?: number;
  cluster_sources?: string[];
  days_left?: number;
  /** 信源特有字段：推文的作者、头像、互动数等 */
  extra?: TweetExtra | Record<string, unknown> | null;
  author?: string;
  related?: { id: number; title: string; url: string; source_name: string; published_at: string | null; score: number }[];
}

export interface TweetExtra {
  kind: "tweet";
  tweet_id: string;
  handle: string;
  name: string;
  avatar?: string;
  likes: number;
  retweets: number;
  replies: number;
  quotes?: number;
  views?: number | null;
  media?: string[];
  retweeted_by?: string;
  query?: string;
}

export function tweetOf(item: Pick<Item, "extra">): TweetExtra | null {
  const e = item.extra as Partial<TweetExtra> | null | undefined;
  return e && e.kind === "tweet" && e.tweet_id ? (e as TweetExtra) : null;
}

export interface XRunReport {
  at: string;
  finished_at?: string;
  source_id: string;
  targets: number;
  done_targets: number;
  fetched: number;
  by_target: Record<string, number>;
  via_dom: number;
  stopped: string | null;
  proxy: string;
}

/** /api/x/status */
export interface XStatus {
  state: "unknown" | "logged_in" | "login" | "challenge" | "rate_limited" | "suspended" | "error_page";
  message: string;
  blocked: boolean;
  running: boolean;
  updated_at?: string;
  today_count: number;
  daily_max: number;
  last_run?: XRunReport;
  playwright: boolean;
  time: string;
  headless: boolean;
  show_avatars: boolean;
  proxy: string;
  proxy_from: "settings" | "system" | "none" | "off";
  sources: { id: string; name: string; enabled: boolean; keywords: number; accounts: number; next_run: string | null }[];
}

export interface ItemList {
  items: Item[];
  total: number;
  unread: number;
}

export interface Stats {
  total: number;
  unread: number;
  today: number;
  pending_llm: number;
  unread_by_category: Record<string, number>;
  deadlines_7d: number;
  llm_usage_today: { calls: number; prompt_tokens?: number; completion_tokens?: number };
}

export interface Health {
  ok: boolean;
  version: string;
  llm: boolean;
  llm_paused?: boolean;
  llm_error?: string;
  playwright: boolean;
  scheduler: boolean;
}

export interface SourceState {
  last_run?: string | null;
  last_success?: string | null;
  last_error?: string | null;
  last_count?: number;
  last_new?: number;
  total?: number;
  runs?: number;
  failures?: number;
  running?: number;
  items: number;
}

export interface Source {
  id: string;
  name: string;
  type: string;
  url: string | null;
  tier: "T1" | "T2";
  category: Category;
  enabled: boolean;
  interval_minutes: number | null;
  extra: Record<string, unknown>;
  state: SourceState;
  next_run: string | null;
}

export interface BriefItem {
  id: number;
  title: string;
  url: string;
  score: number;
  summary: string;
  raw_summary: string;
  deadline: string | null;
  category: Category;
  tags: string[];
  source_name: string;
  published_at: string | null;
}

export interface Brief {
  date: string;
  generated_at: string;
  greeting: string;
  stats: { new_items: number; must: number; deadlines: number };
  sections: { key: string; title: string; items: BriefItem[] }[];
  markdown: string;
}

export interface Settings {
  llm: {
    enabled: boolean;
    base_url: string;
    model: string;
    api_key: string;
    api_key_masked: string;
    api_key_from_env: boolean;
    dual_score: boolean;
    disable_thinking: boolean;
    temperature: number;
    max_items_per_run: number;
    concurrency: number;
    send_content: boolean;
    daily_call_budget: number;
  };
  thresholds: {
    featured: number;
    by_category: Partial<Record<Category, number>>;
    notify: number;
    browse_floor: number;
    t2_offset: number;
    stale_hours: number;
  };
  profile: {
    name: string;
    school: string;
    college: string;
    major: string;
    grade: string;
    interests: string[];
    dislikes: string[];
    notes: string;
  };
  fetch: {
    default_interval_minutes: number;
    browser_channel: string;
    browser_headless: boolean;
    max_detail_fetch: number;
    rsshub_base: string;
    github_token: string;
    github_token_masked: string;
    proxy_url: string;
    proxy_use_system: boolean;
    /** 只读：检测到的 Windows 系统代理 */
    system_proxy?: string;
  };
  x: {
    time: string;
    catch_up: boolean;
    min_delay_seconds: number;
    max_delay_seconds: number;
    per_target_max: number;
    max_scrolls: number;
    run_max: number;
    daily_max: number;
    max_age_days: number;
    headless: boolean;
    show_avatars: boolean;
  };
  notify: { enabled: boolean; quiet_start: string; quiet_end: string };
  scoring: { time: string; catch_up: boolean; skip_catchup_within_minutes: number; stop_at_peak: boolean };
  scheduler_enabled: boolean;
  paths?: { data: string; config: string; prompts: string };
}

/** /api/score/schedule：每日谷时批量打分的计划与状态 */
export interface ScoreSchedule {
  time: string;
  catch_up: boolean;
  next_at: string;
  next_is_peak: boolean;
  time_is_peak_on_workdays: boolean;
  now_is_peak: boolean;
  next_offpeak: string;
  peak_windows: [string, string][];
  pricing_url: string;
  pending: number;
  running: boolean;
  last_done_at: string | null;
  last_result: { scored?: number; failed?: number; error?: string; trigger?: string; finished_at?: string } | null;
  llm_active: boolean;
  plan: { due: boolean; reason: "scheduled" | "catch_up" | "wait_offpeak" | "near_next" | "done" | "no_catch_up" };
}

export interface ScoreRunResult {
  started: boolean;
  needs_confirm?: boolean;
  running?: boolean;
  message?: string;
  next_offpeak?: string;
  pending?: number;
  requeued?: number;
}

export interface NotificationRow {
  id: number;
  item_id: number | null;
  title: string;
  body: string;
}

// ---------------------------------------------------------------- 邮件信源
export interface EmailProvider {
  label: string;
  host: string;
  port: number;
  ssl: boolean;
  auth: "password" | "oauth2_ms";
  secret_label: string;
  use_proxy: boolean;
  webmail: string;
  help: string;
}

export interface EmailProviders {
  providers: Record<string, EmailProvider>;
  vault: string;
}

export interface EmailOAuth {
  authorized: boolean;
  pending: boolean;
  state: "pending" | "done" | "error" | "expired" | null;
  user_code: string | null;
  verification_uri: string | null;
  expires_at: number | null;
  message: string | null;
}

export interface EmailStatus {
  id: string;
  provider: string;
  address: string;
  address_ok: boolean;
  host: string;
  port: number;
  ssl: boolean;
  auth: "password" | "oauth2_ms";
  has_secret: boolean;
  client_id: string;
  folders: string[];
  days: number;
  proxy: string;
  proxy_from: string;
  oauth: EmailOAuth | null;
}
