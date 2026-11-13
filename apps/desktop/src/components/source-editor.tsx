import { useMutation } from "@tanstack/react-query";
import { Eye, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { useEmailProviders } from "@/components/email-panel";
import { api, type Source } from "@/lib/api";
import { CATEGORY_KEYS, CATEGORY_META, shortDate } from "@/lib/format";

const TEMPLATES: Record<string, Record<string, unknown>> = {
  rss: { limit: 30 },
  html_list: { list: { item: "li", title: "a@title", link: "a", date: "span" }, detail: { enabled: true, content: [".v_news_content"], max: 5 } },
  json: { items_path: "data.list", fields: { title: "title", url: "url", date: "date", summary: "summary" } },
  github: { mode: "releases", repo: "owner/name", limit: 5 },
  playwright: { wait_for: "li", list: { item: "li", title: "a@title", link: "a", date: "span" }, detail: { enabled: true, max: 3 } },
  feishu: { allow_llm: false },
  x: {
    keywords: ["DeepSeek min_faves:50", "大模型 min_faves:20"],
    accounts: ["OpenAI", "deepseek_ai"],
    use_proxy: true,
    per_target_max: 30,
    include_retweets: false,
    include_replies: false,
  },
  email: { provider: "qq", address: "", folders: ["INBOX"], days: 7, max_per_run: 50, use_proxy: false },
};

interface Preview {
  ok: boolean;
  error?: string;
  count?: number;
  items: { title: string; url: string; published_at: string | null }[];
}

export function SourceEditor({ source, onClose, onSaved }: { source: Source | null; onClose: () => void; onSaved: () => void }) {
  const isNew = !source;
  const [form, setForm] = useState({
    id: source?.id ?? "",
    name: source?.name ?? "",
    type: source?.type ?? "html_list",
    url: source?.url ?? "",
    tier: source?.tier ?? "T1",
    category: source?.category ?? "school",
    interval_minutes: source?.interval_minutes ?? 60,
    enabled: source?.enabled ?? true,
  });
  const [extra, setExtra] = useState(JSON.stringify(source?.extra ?? TEMPLATES.html_list, null, 2));
  const [preview, setPreview] = useState<Preview | null>(null);
  const set = (k: keyof typeof form, v: unknown) => setForm((f) => ({ ...f, [k]: v }));
  const isX = form.type === "x";
  const isEmail = form.type === "email";
  const { data: emailProviders } = useEmailProviders();
  const [emailSecret, setEmailSecret] = useState("");
  // X 信源：关键词 / 账号用两个多行输入框编辑，写回高级配置 JSON
  const extraObj = (() => {
    try {
      return JSON.parse(extra || "{}") as Record<string, unknown>;
    } catch {
      return null;
    }
  })();
  const setExtraKey = (k: string, v: unknown) => {
    const base = extraObj ?? {};
    setExtra(JSON.stringify({ ...base, [k]: v }, null, 2));
  };
  const lines = (k: string) => ((extraObj?.[k] as string[] | undefined) ?? []).join("\n");
  // 编辑时原样保留（关键词里可以有空格），保存时再去空行、去首尾空格
  const setLines = (k: string, text: string) => setExtraKey(k, text.split("\n"));

  const body = () => {
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(extra || "{}");
    } catch {
      throw new Error("高级配置不是合法的 JSON");
    }
    if (form.type === "x") {
      for (const k of ["keywords", "accounts"]) {
        const v = parsed[k];
        if (Array.isArray(v)) parsed[k] = v.map((x) => String(x).trim()).filter(Boolean);
      }
      return { ...form, url: null, interval_minutes: null, extra: parsed };
    }
    if (isEmail) {
      return { ...form, url: null, interval_minutes: Number(form.interval_minutes) || 15, extra: parsed };
    }
    return { ...form, url: form.url || null, interval_minutes: Number(form.interval_minutes) || null, extra: parsed };
  };
  const save = useMutation({
    mutationFn: async () => {
      await api(isNew ? "/api/sources" : `/api/sources/${source!.id}`, { method: isNew ? "POST" : "PUT", json: body() });
      // 授权码不进 sources.yaml：信源存好后单独写进 Windows 凭据管理器
      if (isEmail && emailSecret.trim()) await api(`/api/email/${form.id}/secret`, { method: "PUT", json: { secret: emailSecret } });
    },
    onSuccess: () => {
      toast.success("已保存");
      onSaved();
      onClose();
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });
  const test = useMutation({
    mutationFn: () => api<Preview>("/api/sources/preview", { method: "POST", json: { ...body(), id: form.id || "preview" } }),
    onSuccess: setPreview,
    onError: (e) => toast.error(String((e as Error).message)),
  });

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[88vh] overflow-y-auto rounded-[22px] border-0 p-0 shadow-pop sm:max-w-[640px]">
        <div className="p-6 pb-0">
          <DialogHeader>
            <DialogTitle className="text-[18px]">{isNew ? "新增信源" : `编辑 · ${source!.name}`}</DialogTitle>
            <DialogDescription>选择器写法：<code className="font-mono">sel</code> 取文本，<code className="font-mono">sel@attr</code> 取属性；日期可写成列表，按“年 月 日”顺序拼接。</DialogDescription>
          </DialogHeader>
        </div>
        <div className="grid grid-cols-2 gap-4 p-6">
          <Field label="ID（英文，唯一）">
            <Input value={form.id} disabled={!isNew} onChange={(e) => set("id", e.target.value)} placeholder="scu-xxx" />
          </Field>
          <Field label="名称">
            <Input value={form.name} onChange={(e) => set("name", e.target.value)} placeholder="软件学院 · 学院通知" />
          </Field>
          <Field label="类型">
            <Select
              value={form.type}
              onValueChange={(v) => {
                set("type", v);
                if (isNew) setExtra(JSON.stringify(TEMPLATES[v] ?? {}, null, 2));
                if (isNew && v === "x") setForm((f) => ({ ...f, type: v, tier: "T2", category: "news", enabled: false }));
                if (isNew && v === "email") setForm((f) => ({ ...f, type: v, tier: "T2", category: "email", interval_minutes: 15 }));
              }}
            >
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {Object.keys(TEMPLATES).map((t) => (
                  <SelectItem key={t} value={t}>{t}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="分类">
            <Select value={form.category} onValueChange={(v) => set("category", v)}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                {CATEGORY_KEYS.map((c) => (
                  <SelectItem key={c} value={c}>{CATEGORY_META[c].label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          {isX && (
            <>
              <Field label="关键词（每行一个，搜“最新”；可用 X 搜索语法，如 min_faves:50、lang:zh）">
                <Textarea value={lines("keywords")} onChange={(e) => setLines("keywords", e.target.value)} rows={6} className="font-mono text-[12.5px]" />
              </Field>
              <Field label="账号（每行一个，@ 可省略）">
                <Textarea value={lines("accounts")} onChange={(e) => setLines("accounts", e.target.value)} rows={6} className="font-mono text-[12.5px]" />
              </Field>
              <div className="col-span-2 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-2xl bg-surface-2 px-4 py-3 text-[12.5px]">
                <label className="flex items-center gap-2">
                  <Switch checked={extraObj?.use_proxy !== false} onCheckedChange={(v) => setExtraKey("use_proxy", v)} />
                  走代理（设置 → 抓取 → 代理）
                </label>
                <label className="flex items-center gap-2">
                  <Switch checked={!!extraObj?.include_retweets} onCheckedChange={(v) => setExtraKey("include_retweets", v)} />
                  包含转推
                </label>
                <label className="flex items-center gap-2">
                  <Switch checked={!!extraObj?.include_replies} onCheckedChange={(v) => setExtraKey("include_replies", v)} />
                  包含回复
                </label>
                <span className="text-muted-foreground">每天一次，时间与频率在 设置 → X 里改；“试抓预览”只列出要打开的页面，不会访问 x.com。</span>
              </div>
            </>
          )}
          {isEmail && (
            <>
              <Field label="邮箱服务商">
                <Select
                  value={String(extraObj?.provider ?? "qq")}
                  onValueChange={(v) => {
                    const base = extraObj ?? {};
                    setExtra(JSON.stringify({ ...base, provider: v, use_proxy: emailProviders?.providers[v]?.use_proxy ?? false }, null, 2));
                    if (isNew && !form.name) set("name", emailProviders?.providers[v]?.label ?? v);
                  }}
                >
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {Object.entries(emailProviders?.providers ?? {}).map(([k, p]) => (
                      <SelectItem key={k} value={k}>{p.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Field>
              <Field label="邮箱地址">
                <Input value={String(extraObj?.address ?? "")} onChange={(e) => setExtraKey("address", e.target.value.trim())} placeholder="you@example.com" />
              </Field>
              {(() => {
                const p = emailProviders?.providers[String(extraObj?.provider ?? "qq")];
                if (!p) return null;
                return (
                  <>
                    {p.auth === "oauth2_ms" ? (
                      <Field label="client_id（你在 Microsoft Entra 注册的应用）" className="col-span-2">
                        <Input value={String(extraObj?.client_id ?? "")} onChange={(e) => setExtraKey("client_id", e.target.value.trim())} placeholder="00000000-0000-0000-0000-000000000000" className="font-mono" />
                      </Field>
                    ) : (
                      <Field label={`${p.secret_label}（只存进 Windows 凭据管理器，留空则不修改）`} className="col-span-2">
                        <Input type="password" autoComplete="off" value={emailSecret} onChange={(e) => setEmailSecret(e.target.value)} placeholder={p.secret_label} />
                      </Field>
                    )}
                    {(String(extraObj?.provider) === "custom" || String(extraObj?.provider) === "scu") && (
                      <Field label="IMAP 服务器（SSL 993）" className="col-span-2">
                        <Input value={String(extraObj?.host ?? "")} onChange={(e) => setExtraKey("host", e.target.value.trim())} placeholder={p.host || "imap.example.com"} />
                      </Field>
                    )}
                    <div className="col-span-2 rounded-2xl bg-surface-2 px-4 py-3 text-[12.5px] leading-relaxed text-muted-foreground">{p.help}</div>
                  </>
                );
              })()}
            </>
          )}
          {!isX && !isEmail && <Field label="URL" className="col-span-2">
            <Input value={form.url} onChange={(e) => set("url", e.target.value)} placeholder="https://… （RSSHub 可写 {RSSHUB}/路由）" />
          </Field>}
          <Field label="分级">
            <Select value={form.tier} onValueChange={(v) => set("tier", v)}>
              <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="T1">T1 · 官方一手</SelectItem>
                <SelectItem value="T2">T2 · 媒体 / 聚合</SelectItem>
              </SelectContent>
            </Select>
          </Field>
          {!isX && (
            <Field label="抓取间隔（分钟）">
              <Input type="number" min={5} value={form.interval_minutes} onChange={(e) => set("interval_minutes", e.target.value)} />
            </Field>
          )}
          <Field label="高级配置（JSON：list / detail / repo / mode / query / fields …）" className="col-span-2">
            <Textarea value={extra} onChange={(e) => setExtra(e.target.value)} rows={9} className="font-mono text-[12px] leading-relaxed" />
          </Field>
          {preview && (
            <div className="col-span-2 rounded-2xl bg-surface-2 p-4">
              <div className="mb-2 text-[12.5px] font-medium">
                {preview.ok ? `试抓成功：${preview.count} 条（只预览，不入库）` : <span className="text-destructive">试抓失败：{preview.error}</span>}
              </div>
              <div className="space-y-1">
                {preview.items.slice(0, 8).map((it) => (
                  <div key={it.url} className="flex justify-between gap-3 text-[12.5px]">
                    <span className="truncate">{it.title}</span>
                    <span className="shrink-0 text-muted-foreground tabular">{shortDate(it.published_at) || "无日期"}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
        <div className="flex items-center justify-between bg-surface-2/60 px-6 py-4">
          <Button variant="ghost" className="rounded-xl" onClick={() => test.mutate()} disabled={test.isPending}>
            {test.isPending ? <Loader2 className="size-4 animate-spin" /> : <Eye className="size-4" />} 试抓预览
          </Button>
          <div className="flex gap-2">
            <Button variant="ghost" className="rounded-xl" onClick={onClose}>取消</Button>
            <Button className="rounded-xl" onClick={() => save.mutate()} disabled={save.isPending || !form.id || !form.name}>保存</Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children, className }: { label: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={className}>
      <Label className="mb-1.5 block text-[12px] font-medium text-muted-foreground">{label}</Label>
      {children}
    </div>
  );
}
