import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle, AtSign, Bot, Braces, GitBranch, Globe, KeyRound, Loader2, Mail, MessageSquare, MoreHorizontal, Pencil, Play, Plus, RefreshCw, Rss, Trash2,
} from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import { toast } from "sonner";

import { Chip, PageHeader } from "@/components/common";
import { SourceEditor } from "@/components/source-editor";
import { EmailPanel } from "@/components/email-panel";
import { XPanel } from "@/components/x-panel";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { api, type Source } from "@/lib/api";
import { CATEGORY_KEYS, CATEGORY_META, hostOf, relativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";

const TYPE_ICON: Record<string, React.ReactNode> = {
  rss: <Rss className="size-4" />,
  html_list: <Globe className="size-4" />,
  json: <Braces className="size-4" />,
  github: <GitBranch className="size-4" />,
  playwright: <Bot className="size-4" />,
  feishu: <MessageSquare className="size-4" />,
  x: <AtSign className="size-4" />,
  email: <Mail className="size-4" />,
};
const TYPE_LABEL: Record<string, string> = {
  rss: "RSS", html_list: "网页列表", json: "JSON", github: "GitHub", playwright: "浏览器", feishu: "飞书", x: "X", email: "邮件",
};

export function SourcesPage() {
  const qc = useQueryClient();
  const [editing, setEditing] = useState<Source | "new" | null>(null);
  const { data = [] } = useQuery({ queryKey: ["sources"], queryFn: () => api<Source[]>("/api/sources"), refetchInterval: 15_000 });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["sources"] });
    qc.invalidateQueries({ queryKey: ["items"] });
    qc.invalidateQueries({ queryKey: ["stats"] });
  };
  const runAll = useMutation({
    mutationFn: () => api<{ ok: boolean; new: number }[]>("/api/sources/run-all", { method: "POST" }),
    onSuccess: (r) => {
      toast.success(`抓取完成：${r.filter((x) => x.ok).length}/${r.length} 个信源成功，新增 ${r.reduce((a, x) => a + (x.new || 0), 0)} 条`);
      refresh();
    },
  });
  const ok = data.filter((s) => s.enabled && !s.state.last_error && s.state.last_success).length;
  const failed = data.filter((s) => s.enabled && s.state.last_error).length;

  return (
    <div className="h-full overflow-y-auto">
      <PageHeader
        title="信源管理"
        subtitle={`${data.length} 个信源 · ${ok} 个正常${failed ? ` · ${failed} 个出错` : ""} · 配置保存在 config/sources.yaml`}
      >
        <Button variant="ghost" className="h-9 rounded-xl text-muted-foreground" onClick={() => api("/api/sources/reload", { method: "POST" }).then(() => { toast.success("已重新加载 sources.yaml"); refresh(); })}>
          重新加载
        </Button>
        <Button variant="secondary" className="h-9 rounded-xl" onClick={() => runAll.mutate()} disabled={runAll.isPending}>
          {runAll.isPending ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} 全部抓取
        </Button>
        <Button className="h-9 rounded-xl" onClick={() => setEditing("new")}>
          <Plus className="size-4" /> 新增信源
        </Button>
      </PageHeader>

      <div className="space-y-8 px-10 pb-16">
        {CATEGORY_KEYS.map((cat) => {
          const list = data.filter((s) => s.category === cat);
          if (!list.length) return null;
          return (
            <section key={cat}>
              <div className="mb-2 flex items-center gap-2 px-1 text-[13px] font-medium text-muted-foreground">
                <span className={cn("size-1.5 rounded-full", CATEGORY_META[cat].dot)} />
                {CATEGORY_META[cat].label}
                <span className="font-mono text-[12px] tabular">{list.length}</span>
              </div>
              <div className="space-y-1.5">
                {list.map((s, i) => (
                  <SourceRow key={s.id} source={s} index={i} onEdit={() => setEditing(s)} onChanged={refresh} />
                ))}
              </div>
            </section>
          );
        })}
      </div>
      {editing && <SourceEditor source={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={refresh} />}
    </div>
  );
}

function SourceRow({ source: s, index, onEdit, onChanged }: { source: Source; index: number; onEdit: () => void; onChanged: () => void }) {
  const qc = useQueryClient();
  const isX = s.type === "x";
  const run = useMutation({
    mutationFn: () => api<{ ok: boolean; new: number; fetched: number; error?: string }>(`/api/sources/${s.id}/run`, { method: "POST" }),
    onSuccess: (r) => {
      if (r.ok) toast.success(`${s.name}：抓到 ${r.fetched} 条，新增 ${r.new} 条`);
      else toast.error(`${s.name}：${r.error}`);
      onChanged();
      if (isX) qc.invalidateQueries({ queryKey: ["x-status"] });
    },
    onError: (e) => toast.error(String(e)),
  });
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api(`/api/sources/${s.id}/toggle`, { method: "POST", json: { enabled } }),
    onSuccess: onChanged,
  });
  const del = useMutation({
    mutationFn: () => api(`/api/sources/${s.id}`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success(`已删除 ${s.name}`);
      onChanged();
    },
  });
  const login = useMutation({
    mutationFn: () => api("/api/browser/login", { method: "POST", json: { url: s.url } }),
    onMutate: () => toast.info("已打开浏览器窗口：登录完成后关闭窗口即可保存会话"),
  });
  const st = s.state;
  const running = run.isPending || !!st.running;
  const status = !s.enabled
    ? { dot: "bg-muted-foreground/40", text: "已停用" }
    : st.last_error
      ? { dot: "bg-destructive", text: "出错" }
      : st.last_success
        ? { dot: "bg-good", text: `${relativeTime(st.last_success)} · 新增 ${st.last_new ?? 0}` }
        : { dot: "bg-warm", text: s.type === "feishu" ? "推送型，等待接入" : "等待首次抓取" };

  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.025 }}
      className={cn("group rounded-2xl bg-surface-2 px-4 py-3 transition-colors hover:bg-accent/70", !s.enabled && "opacity-60")}
    >
      <div className="flex items-center gap-4">
        <div className={cn("grid size-9 shrink-0 place-items-center rounded-xl", CATEGORY_META[s.category]?.soft, CATEGORY_META[s.category]?.text)}>
          {TYPE_ICON[s.type] ?? <Globe className="size-4" />}
        </div>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[14px] font-medium">{s.name}</span>
            <Chip className="h-[18px] px-1.5 text-[10.5px]">{s.tier}</Chip>
            <Chip className="h-[18px] px-1.5 text-[10.5px]">{TYPE_LABEL[s.type] ?? s.type}</Chip>
          </div>
          <div className="mt-0.5 truncate text-[12px] text-muted-foreground">
            {isX
              ? `${(s.extra.keywords as string[] | undefined)?.length ?? 0} 个关键词 · ${(s.extra.accounts as string[] | undefined)?.length ?? 0} 个账号 · 每天一次`
              : s.type === "github" ? String(s.extra.repo ?? s.extra.query ?? `trending/${s.extra.language ?? ""}`)
                : s.type === "email" ? String(s.extra.address ?? "") : hostOf(s.url)}
            {!isX && s.interval_minutes ? ` · 每 ${s.interval_minutes} 分钟` : ""}
          </div>
        </div>
        <div className="hidden w-48 shrink-0 text-right md:block">
          <div className="flex items-center justify-end gap-1.5 text-[12px] text-muted-foreground">
            <span className={cn("size-1.5 rounded-full", status.dot, running && "animate-pulse")} />
            {running ? "抓取中…" : status.text}
          </div>
          <div className="mt-0.5 font-mono text-[11.5px] text-muted-foreground/80 tabular">累计 {st.items ?? 0} 条</div>
        </div>
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="icon-sm" className="rounded-xl" disabled={running || s.type === "feishu"}
                onClick={() => (!isX || confirm("X 每天会按计划自动抓一次。现在再手动抓一次吗？（频繁抓取容易被限制）")) && run.mutate()}>
                {running ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>立即抓取</TooltipContent>
          </Tooltip>
          <Switch checked={s.enabled} onCheckedChange={(v) => toggle.mutate(v)} className="mx-1.5" />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" className="rounded-xl">
                <MoreHorizontal className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44 rounded-xl">
              <DropdownMenuItem onClick={onEdit}>
                <Pencil className="size-4" /> 编辑
              </DropdownMenuItem>
              {s.type === "playwright" && (
                <DropdownMenuItem onClick={() => login.mutate()}>
                  <KeyRound className="size-4" /> 打开浏览器登录
                </DropdownMenuItem>
              )}
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" onClick={() => confirm(`删除信源「${s.name}」？已抓到的条目会保留。`) && del.mutate()}>
                <Trash2 className="size-4" /> 删除
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
      {isX && <XPanel source={s} />}
      {s.type === "email" && <EmailPanel source={s} />}
      {s.enabled && st.last_error && (
        <div className="mt-2.5 flex items-start gap-2 rounded-xl bg-destructive/8 px-3 py-2 text-[12px] leading-relaxed text-destructive">
          <AlertCircle className="mt-0.5 size-3.5 shrink-0" />
          <span className="break-all">{st.last_error}</span>
        </div>
      )}
    </motion.div>
  );
}
