import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleHelp, ExternalLink, KeyRound, Loader2, PlugZap, ShieldCheck, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type EmailProviders, type EmailStatus, type Source } from "@/lib/api";
import { openExternal } from "@/lib/platform";
import { cn } from "@/lib/utils";

export function useEmailProviders() {
  return useQuery({ queryKey: ["email-providers"], queryFn: () => api<EmailProviders>("/api/email/providers"), staleTime: Infinity });
}

/** 信源页邮件卡片：授权码（存 Windows 凭据管理器）、测试连接、Outlook 设备代码授权 */
export function EmailPanel({ source }: { source: Source }) {
  const qc = useQueryClient();
  const key = ["email", source.id];
  const { data: providers } = useEmailProviders();
  const { data: st } = useQuery({
    queryKey: key,
    queryFn: () => api<EmailStatus>(`/api/email/${source.id}`),
    refetchInterval: (q) => (q.state.data?.oauth?.pending ? 3000 : 30_000),
  });
  const [secret, setSecret] = useState("");
  const [showHelp, setShowHelp] = useState(false);
  const set = (r: EmailStatus) => qc.setQueryData(key, r);
  const save = useMutation({
    mutationFn: () => api<EmailStatus>(`/api/email/${source.id}/secret`, { method: "PUT", json: { secret } }),
    onSuccess: (r) => {
      set(r);
      setSecret("");
      toast.success("已保存到 Windows 凭据管理器（不会写进配置文件）");
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });
  const forget = useMutation({
    mutationFn: () => api<EmailStatus>(`/api/email/${source.id}/secret`, { method: "DELETE" }),
    onSuccess: (r) => {
      set(r);
      toast.success("已从凭据管理器删除");
    },
  });
  const test = useMutation({
    mutationFn: () => api<{ ok: boolean; message?: string; error?: string }>(`/api/email/${source.id}/test`, { method: "POST" }),
    onSuccess: (r) => (r.ok ? toast.success(r.message) : toast.error(r.error, { duration: 10_000 })),
    onError: (e) => toast.error(String((e as Error).message)),
  });
  const oauth = useMutation({
    mutationFn: () => api<EmailStatus["oauth"]>(`/api/email/${source.id}/oauth/start`, { method: "POST" }),
    onSuccess: (o) => {
      qc.invalidateQueries({ queryKey: key });
      if (o?.verification_uri) openExternal(o.verification_uri);
    },
    onError: (e) => toast.error(String((e as Error).message), { duration: 10_000 }),
  });
  if (!st) return null;
  const p = providers?.providers[st.provider];
  const isOAuth = st.auth === "oauth2_ms";
  const o = st.oauth;
  const badge = st.has_secret
    ? { icon: <CheckCircle2 className="size-3.5" />, text: isOAuth ? "已授权" : "已保存授权码", cls: "bg-good/12 text-good" }
    : { icon: <CircleHelp className="size-3.5" />, text: isOAuth ? "未授权" : "未填授权码", cls: "bg-warm/12 text-warm" };

  return (
    <div className="mt-3 space-y-2.5 rounded-xl bg-background/60 px-3.5 py-3 text-[12.5px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("flex h-6 items-center gap-1 rounded-full px-2 font-medium", badge.cls)}>
          {badge.icon}
          {badge.text}
        </span>
        <span className="text-muted-foreground">
          {p?.label ?? st.provider} · {st.address_ok ? st.address : <span className="text-warm">地址未填（在“编辑”里改）</span>} · {st.host}:{st.port}
          {st.proxy_from !== "off" ? ` · 代理 ${st.proxy || "未配置"}` : ""}
        </span>
        <div className="ml-auto flex items-center gap-1.5">
          <Button variant="ghost" size="sm" className="h-7 rounded-lg text-[12px]" onClick={() => setShowHelp((v) => !v)}>
            <CircleHelp className="size-3.5" /> 怎么获取
          </Button>
          <Button
            variant="secondary"
            size="sm"
            className="h-7 rounded-lg text-[12px]"
            disabled={test.isPending || !st.has_secret || !st.address_ok}
            onClick={() => test.mutate()}
          >
            {test.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <PlugZap className="size-3.5" />} 测试连接
          </Button>
        </div>
      </div>

      {showHelp && p && <div className="rounded-lg bg-surface-2 px-3 py-2 leading-relaxed text-muted-foreground">{p.help}</div>}

      {!isOAuth && (
        <div className="flex items-center gap-2">
          <KeyRound className="size-3.5 shrink-0 text-muted-foreground" />
          <Input
            type="password"
            autoComplete="off"
            value={secret}
            onChange={(e) => setSecret(e.target.value)}
            placeholder={st.has_secret ? `已保存；重新填写可替换${p?.secret_label ?? "授权码"}` : `${p?.secret_label ?? "授权码"}（不是邮箱登录密码）`}
            className="h-8 max-w-[340px] rounded-lg text-[12.5px]"
          />
          <Button size="sm" className="h-8 rounded-lg text-[12px]" disabled={!secret.trim() || save.isPending} onClick={() => save.mutate()}>
            保存
          </Button>
          {st.has_secret && (
            <Button variant="ghost" size="icon-sm" className="rounded-lg text-muted-foreground" title="删除已保存的授权码" onClick={() => forget.mutate()}>
              <Trash2 className="size-3.5" />
            </Button>
          )}
        </div>
      )}

      {isOAuth && (
        <div className="space-y-2">
          {!st.client_id && (
            <div className="text-warm">还没有填 client_id：先在 Microsoft Entra 注册一个应用（见 README「Outlook」），在“编辑”里填进 client_id。</div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" className="h-8 rounded-lg text-[12px]" disabled={!st.client_id || oauth.isPending || !!o?.pending} onClick={() => oauth.mutate()}>
              {oauth.isPending ? <Loader2 className="size-3.5 animate-spin" /> : <ShieldCheck className="size-3.5" />}
              {st.has_secret ? "重新授权" : "Microsoft 授权"}
            </Button>
            {st.has_secret && (
              <Button variant="ghost" size="sm" className="h-8 rounded-lg text-[12px] text-muted-foreground" onClick={() => forget.mutate()}>
                <Trash2 className="size-3.5" /> 撤销本机令牌
              </Button>
            )}
          </div>
          {o?.pending && o.user_code && (
            <div className="flex flex-wrap items-center gap-3 rounded-lg bg-primary/8 px-3 py-2">
              <span>在浏览器打开</span>
              <button className="flex items-center gap-1 text-primary underline-offset-2 hover:underline" onClick={() => openExternal(o.verification_uri!)}>
                {o.verification_uri} <ExternalLink className="size-3" />
              </button>
              <span>输入短码</span>
              <span className="select-all rounded-md bg-background px-2 py-0.5 font-mono text-[14px] font-semibold tracking-widest">{o.user_code}</span>
              <Loader2 className="size-3.5 animate-spin text-muted-foreground" />
            </div>
          )}
          {o?.message && !o.pending && o.state && <div className={o.state === "done" ? "text-good" : "text-destructive"}>{o.message}</div>}
        </div>
      )}
      <div className="text-muted-foreground">只读收取：不标记已读、不发送、不删除；{st.folders.join("、")} 最近 {st.days} 天。密码只存在本机 Windows 凭据管理器。</div>
    </div>
  );
}
