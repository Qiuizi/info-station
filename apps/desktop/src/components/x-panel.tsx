import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, CircleHelp, Loader2, LogIn, RotateCcw } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { api, type Source, type XStatus } from "@/lib/api";
import { relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

import { Chip } from "./common";

const PROXY_FROM: Record<XStatus["proxy_from"], string> = {
  settings: "设置",
  system: "Windows 系统代理",
  none: "未配置",
  off: "该信源未启用代理",
};

export function useXStatus() {
  return useQuery({ queryKey: ["x-status"], queryFn: () => api<XStatus>("/api/x/status"), refetchInterval: 20_000 });
}

/** 信源页 X 卡片：登录状态、打开浏览器登录、上次抓取结果 */
export function XPanel({ source }: { source: Source }) {
  const qc = useQueryClient();
  const { data: x } = useXStatus();
  const login = useMutation({
    mutationFn: () => api<XStatus>("/api/x/login", { method: "POST" }),
    onMutate: () => toast.info("已打开浏览器窗口：在里面登录 x.com（含两步验证），看到首页后关闭窗口即可", { duration: 8000 }),
    onSuccess: (r) => {
      qc.setQueryData(["x-status"], r);
      if (r.state === "logged_in") toast.success("X 已登录，会话已保存在本机");
      else toast.warning("没能确认登录成功：如果已经登录，下次抓取时会自动确认");
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });
  const clear = useMutation({
    mutationFn: () => api<XStatus>("/api/x/clear-block", { method: "POST" }),
    onSuccess: (r) => qc.setQueryData(["x-status"], r),
  });
  if (!x) return null;
  const me = x.sources.find((v) => v.id === source.id);
  const last = x.last_run && x.last_run.source_id === source.id ? x.last_run : undefined;
  const badge = x.blocked
    ? { icon: <AlertTriangle className="size-3.5" />, text: "已停止", cls: "bg-destructive/10 text-destructive" }
    : x.state === "logged_in"
      ? { icon: <CheckCircle2 className="size-3.5" />, text: "已登录", cls: "bg-good/12 text-good" }
      : { icon: <CircleHelp className="size-3.5" />, text: "未确认登录", cls: "bg-warm/12 text-warm" };

  return (
    <div className="mt-3 space-y-2.5 rounded-xl bg-background/60 px-3.5 py-3 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("flex h-6 items-center gap-1 rounded-full px-2 font-medium", badge.cls)}>
          {badge.icon}
          {badge.text}
        </span>
        <span className="text-muted-foreground">
          每天 {x.time}（早于谷时打分）{me?.next_run && source.enabled ? ` · 下次 ${relativeTime(me.next_run)}` : ""}
          {" · "}今天 {x.today_count}/{x.daily_max} 条
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          {x.blocked && (
            <Button variant="ghost" size="sm" className="h-7 rounded-lg text-[12px]" onClick={() => clear.mutate()} disabled={clear.isPending}>
              <RotateCcw className="size-3.5" /> 我已处理，恢复
            </Button>
          )}
          <Button
            size="sm"
            variant={x.state === "logged_in" && !x.blocked ? "secondary" : "default"}
            className="h-7 rounded-lg text-[12px]"
            disabled={login.isPending || x.running || !x.playwright}
            onClick={() => login.mutate()}
          >
            {login.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <LogIn className="size-3.5" />}
            {login.isPending ? "等待你在窗口里登录…" : "打开浏览器登录 X"}
          </Button>
        </div>
      </div>
      {!x.playwright && <div className="text-destructive">未安装 Playwright：在 backend 目录运行 uv sync --extra browser</div>}
      {x.message && (x.blocked || x.state !== "logged_in") && (
        <div className={cn("leading-relaxed", x.blocked ? "text-destructive" : "text-muted-foreground")}>{x.message}</div>
      )}
      {!source.enabled && (
        <div className="text-muted-foreground">
          这个信源默认停用：先登录 X，在“编辑”里确认关键词和账号后再打开开关。
        </div>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-muted-foreground">
        <span>
          代理：{x.proxy || "直连"}（{PROXY_FROM[x.proxy_from]}）
        </span>
        {x.running && (
          <span className="flex items-center gap-1 text-primary">
            <Loader2 className="size-3.5 animate-spin" /> 正在抓取…
          </span>
        )}
        {last && (
          <span>
            上次 {relativeTime(last.finished_at || last.at)}：{last.done_targets}/{last.targets} 个页面，抓到 {last.fetched} 条
            {last.via_dom ? `（${last.via_dom} 个页面用页面解析兜底）` : ""}
            {last.stopped ? " · 中途停止" : ""}
          </span>
        )}
      </div>
      {last && Object.keys(last.by_target).length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {Object.entries(last.by_target).map(([k, n]) => (
            <Chip key={k} className="h-[20px] px-2 text-[11px]">
              {k} · {n}
            </Chip>
          ))}
        </div>
      )}
    </div>
  );
}
