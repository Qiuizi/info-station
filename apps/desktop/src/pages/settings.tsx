import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AtSign, Bell, Bot, Check, Clock3, Gauge, Globe, Loader2, Monitor, Moon, Palette, Plug, Power, Sparkles, Sun, UserRound, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { Chip, PageHeader, Segmented } from "@/components/common";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Slider } from "@/components/ui/slider";
import { CATEGORY_KEYS, CATEGORY_META } from "@/lib/format";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useTheme, type Theme } from "@/hooks/use-theme";
import { api, type Settings } from "@/lib/api";
import { getAutostart, inTauri, setAutostart, systemNotify } from "@/lib/platform";
import { isPeakTime, PLAN_LABEL, slotLabel, useRunScoring, useScoreSchedule } from "@/lib/scoring";
import { cn } from "@/lib/utils";

type Draft = Settings;

export function SettingsPage() {
  const qc = useQueryClient();
  const { theme, setTheme } = useTheme();
  const { data } = useQuery({ queryKey: ["settings"], queryFn: () => api<Settings>("/api/settings") });
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => {
    if (data) setDraft(structuredClone(data));
  }, [data]);
  const dirty = !!data && !!draft && JSON.stringify(data) !== JSON.stringify(draft);

  const save = useMutation({
    mutationFn: (d: Draft) => api<Settings>("/api/settings", { method: "PUT", json: d }),
    onSuccess: (s) => {
      qc.setQueryData(["settings"], s);
      qc.invalidateQueries({ queryKey: ["health"] });
      toast.success("设置已保存");
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });
  const testLlm = useMutation({
    mutationFn: () => api<{ ok: boolean; error?: string }>("/api/settings/test-llm", { method: "POST" }),
    onSuccess: (r) => (r.ok ? toast.success("模型连接正常") : toast.error(`连接失败：${r.error}`)),
  });
  const testProxy = useMutation({
    mutationFn: () =>
      api<{ ok: boolean; status?: number; ms?: number; error?: string; proxy: string; proxy_from: string }>("/api/settings/test-proxy", { method: "POST" }),
    onSuccess: (r) =>
      r.ok
        ? toast.success(`能打开 x.com（HTTP ${r.status}，${r.ms} ms）· 代理 ${r.proxy || "直连"}`)
        : toast.error(`打不开 x.com：${r.error ?? `HTTP ${r.status}`} · 代理 ${r.proxy || "直连"}`),
  });
  const rescore = useRunScoring();
  const { data: sched } = useScoreSchedule();

  if (!draft) return null;
  const up = <K extends keyof Draft>(sec: K, patch: Partial<Draft[K]>) =>
    setDraft((d) => (d ? { ...d, [sec]: { ...(d[sec] as object), ...patch } } : d));

  return (
    <div className="relative h-full overflow-y-auto">
      <PageHeader title="设置" subtitle={draft.paths ? `数据目录：${draft.paths.data}` : undefined} />
      <div className="max-w-[900px] space-y-5 px-10 pb-32">
        <Card icon={<Palette className="size-4" />} title="外观">
          <Row label="主题" hint="跟随系统时会随 Windows 深浅色自动切换">
            <Segmented<Theme>
              id="theme"
              value={theme}
              onChange={setTheme}
              options={[
                { value: "light", label: <><Sun className="size-3.5" />浅色</> },
                { value: "dark", label: <><Moon className="size-3.5" />深色</> },
                { value: "system", label: <><Monitor className="size-3.5" />跟随系统</> },
              ]}
            />
          </Row>
        </Card>

        {inTauri && <StartupCard />}

        <Card icon={<Bot className="size-4" />} title="模型筛选（DeepSeek）" desc="OpenAI 兼容接口。没有 Key 时自动降级为规则打分。">
          <Row label="启用模型打分">
            <Switch checked={draft.llm.enabled} onCheckedChange={(v) => up("llm", { enabled: v })} />
          </Row>
          <Row label="API Key" hint={draft.llm.api_key_from_env ? "当前使用环境变量 DEEPSEEK_API_KEY" : draft.llm.api_key_masked ? `已保存：${draft.llm.api_key_masked}` : "未配置"}>
            <Input
              type="password"
              className="w-72"
              placeholder={draft.llm.api_key_masked ? "留空表示不修改" : "sk-…"}
              value={draft.llm.api_key}
              onChange={(e) => up("llm", { api_key: e.target.value })}
            />
          </Row>
          <Row label="Base URL">
            <Input className="w-72 font-mono text-[12.5px]" value={draft.llm.base_url} onChange={(e) => up("llm", { base_url: e.target.value })} />
          </Row>
          <Row label="模型" hint="批量筛选推荐 deepseek-flash（非思考模式）">
            <Input className="w-72 font-mono text-[12.5px]" value={draft.llm.model} onChange={(e) => up("llm", { model: e.target.value })} />
          </Row>
          <Row label="双次独立打分" hint="同一标准打两次取平均，更稳但费用翻倍">
            <Switch checked={draft.llm.dual_score} onCheckedChange={(v) => up("llm", { dual_score: v })} />
          </Row>
          <Row label="关闭思考模式" hint="thinking: disabled，更快更便宜">
            <Switch checked={draft.llm.disable_thinking} onCheckedChange={(v) => up("llm", { disable_thinking: v })} />
          </Row>
          <Row label="发送正文给模型" hint="关闭后只发送标题与来源">
            <Switch checked={draft.llm.send_content} onCheckedChange={(v) => up("llm", { send_content: v })} />
          </Row>
          <Row label="每日调用上限" hint="预算熔断，超过后当天只用规则">
            <Input type="number" className="w-28 font-mono" value={draft.llm.daily_call_budget} onChange={(e) => up("llm", { daily_call_budget: Number(e.target.value) })} />
          </Row>
          <div className="flex gap-2 pt-1">
            <Button variant="secondary" className="rounded-xl" onClick={() => testLlm.mutate()} disabled={testLlm.isPending}>
              {testLlm.isPending ? <Loader2 className="size-4 animate-spin" /> : <Plug className="size-4" />} 测试连接
            </Button>
            <Button variant="ghost" className="rounded-xl" onClick={() => rescore.mutate({ path: "/api/rescore?only_pending=false" })} disabled={rescore.isPending || sched?.running}>
              {rescore.isPending && <Loader2 className="size-4 animate-spin" />} 重新打分今天的条目
            </Button>
            <Button
              variant="ghost"
              className="rounded-xl"
              title="近 30 天发布或尚未截止的学校通知与竞赛科研，按当前规则与提示词重算（约每条 1 次调用）"
              onClick={() => rescore.mutate({ path: "/api/rescore?days=30&categories=school,competition" })}
              disabled={rescore.isPending || sched?.running}
            >
              按新标准重打学校/竞赛（近 30 天）
            </Button>
          </div>
        </Card>

        <Card
          icon={<Clock3 className="size-4" />}
          title="每日打分（DeepSeek 谷时批量）"
          desc="抓取照常进行，新条目先用规则分；每天在下面的时刻把待打分条目一次性交给模型，打完生成简报并发一条汇总通知。"
        >
          <Row
            label="打分时间（北京时间）"
            hint={
              isPeakTime(draft.scoring.time, sched?.peak_windows)
                ? "⚠ 该时刻在工作日属于 DeepSeek 高峰时段（价格为谷时 2 倍）；周末与法定节假日不受影响"
                : "该时刻在任何日子都是谷时（半价）"
            }
          >
            <Input
              type="time"
              className={cn("w-28 font-mono", isPeakTime(draft.scoring.time, sched?.peak_windows) && "text-hot ring-1 ring-hot/40")}
              value={draft.scoring.time}
              onChange={(e) => e.target.value && up("scoring", { time: e.target.value })}
            />
          </Row>
          <Row label="错过定时自动补跑" hint="开机时若在谷时就补跑；若在高峰，顺延到这个高峰结束（12:00 / 18:00）">
            <Switch checked={draft.scoring.catch_up} onCheckedChange={(v) => up("scoring", { catch_up: v })} />
          </Row>
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-card px-4 py-3">
            <div className="text-[13px] leading-relaxed">
              <div>
                下次打分：<span className="font-mono font-semibold tabular">{sched ? slotLabel(sched.next_at) : "—"}</span>
                {sched && sched.plan.reason !== "done" && sched.plan.reason !== "near_next" && (
                  <span className="ml-1.5 text-muted-foreground">（{PLAN_LABEL[sched.plan.reason]}）</span>
                )}
                {sched?.next_is_peak && <span className="ml-1.5 text-hot">峰时</span>}
                {sched?.running && <span className="ml-1.5 text-primary">· 正在打分…</span>}
              </div>
              <div className="text-[12px] text-muted-foreground">
                待打分 {sched?.pending ?? 0} 条
                {sched?.last_done_at && ` · 上次完成 ${slotLabel(sched.last_done_at)}`}
                {sched?.last_result?.error && ` · ${sched.last_result.error}`}
                {sched && (sched.now_is_peak ? " · 现在是高峰时段" : " · 现在是谷时")}
              </div>
            </div>
            <Button
              variant="secondary"
              className="rounded-xl"
              onClick={() => rescore.mutate({ path: "/api/score/run" })}
              disabled={rescore.isPending || sched?.running || !sched?.pending}
            >
              {rescore.isPending || sched?.running ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />} 立即打分
            </Button>
          </div>
          <p className="text-[12px] leading-relaxed text-muted-foreground">
            DeepSeek 官方：北京时间周一至周五（不含法定节假日）{(sched?.peak_windows ?? [["09:00", "12:00"], ["14:00", "18:00"]]).map(([a, b]) => `${a}–${b}`).join("、")} 为高峰，其余时段（含周末、法定节假日全天）为空闲时段，价格减半。规则与节假日表在 config/deepseek_offpeak.yaml。{" "}
            {sched?.pricing_url && (
              <a className="underline underline-offset-2" href={sched.pricing_url} target="_blank" rel="noreferrer">
                官方价格页
              </a>
            )}
          </p>
        </Card>

        <Card icon={<Gauge className="size-4" />} title="阈值" desc="学校与竞赛通知是一手、要行动的信息，门槛单独设置；T2 信源（媒体、聚合）门槛自动上浮。">
          <SliderRow label="进入今日精选（默认）" value={draft.thresholds.featured} onChange={(v) => up("thresholds", { featured: v })} />
          {CATEGORY_KEYS.map((c) => (
            <SliderRow
              key={c}
              label={`精选门槛 · ${CATEGORY_META[c].label}`}
              value={draft.thresholds.by_category?.[c] ?? draft.thresholds.featured}
              onChange={(v) => up("thresholds", { by_category: { ...draft.thresholds.by_category, [c]: v } })}
            />
          ))}
          <p className="text-[12px] leading-relaxed text-muted-foreground">
            排序时，截止 3 / 7 / 14 天内的条目分别 +12 / +8 / +4，已过截止的学校与竞赛通知 −15（只影响精选与必看，不改分数；可在 config/rules.yaml 调整）。
          </p>
          <SliderRow label="弹出系统通知" value={draft.thresholds.notify} onChange={(v) => up("thresholds", { notify: v })} />
          <SliderRow label="全部动态浏览下限" value={draft.thresholds.browse_floor} onChange={(v) => up("thresholds", { browse_floor: v })} />
          <SliderRow label="T2 门槛上浮" max={30} value={draft.thresholds.t2_offset} onChange={(v) => up("thresholds", { t2_offset: v })} />
        </Card>

        <Card icon={<UserRound className="size-4" />} title="我的画像" desc="模型按这份画像判断“和你有没有关系”。">
          <div className="grid grid-cols-2 gap-3">
            <LabeledInput label="称呼" value={draft.profile.name} onChange={(v) => up("profile", { name: v })} />
            <LabeledInput label="学校" value={draft.profile.school} onChange={(v) => up("profile", { school: v })} />
            <LabeledInput label="学院" value={draft.profile.college} onChange={(v) => up("profile", { college: v })} />
            <LabeledInput label="专业" value={draft.profile.major} onChange={(v) => up("profile", { major: v })} />
            <LabeledInput label="年级" value={draft.profile.grade} onChange={(v) => up("profile", { grade: v })} />
          </div>
          <TagEditor label="关注" tags={draft.profile.interests} onChange={(t) => up("profile", { interests: t })} tone="primary" />
          <TagEditor label="不感兴趣" tags={draft.profile.dislikes} onChange={(t) => up("profile", { dislikes: t })} />
          <div>
            <div className="mb-1.5 text-[12px] font-medium text-muted-foreground">补充说明</div>
            <Textarea rows={2} value={draft.profile.notes} onChange={(e) => up("profile", { notes: e.target.value })} />
          </div>
        </Card>

        <Card icon={<Globe className="size-4" />} title="抓取">
          <Row label="默认抓取间隔（分钟）" hint="信源未单独设置时使用；校内站点建议 ≥ 30">
            <Input type="number" className="w-28 font-mono" value={draft.fetch.default_interval_minutes} onChange={(e) => up("fetch", { default_interval_minutes: Number(e.target.value) })} />
          </Row>
          <Row label="浏览器" hint="Playwright 登录态抓取使用；Windows 默认系统自带 Edge">
            <Select value={draft.fetch.browser_channel} onValueChange={(v) => up("fetch", { browser_channel: v })}>
              <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
              <SelectContent>
                {["auto", "msedge", "chrome", "chromium"].map((c) => <SelectItem key={c} value={c}>{c}</SelectItem>)}
              </SelectContent>
            </Select>
          </Row>
          <Row label="无头模式" hint="关闭后抓取时会显示浏览器窗口，便于排查">
            <Switch checked={draft.fetch.browser_headless} onCheckedChange={(v) => up("fetch", { browser_headless: v })} />
          </Row>
          <Row label="GitHub Token" hint={draft.fetch.github_token_masked ? `已配置：${draft.fetch.github_token_masked}` : "可选，提高 API 限额"}>
            <Input type="password" className="w-72" placeholder="ghp_…（留空不修改）" value={draft.fetch.github_token} onChange={(e) => up("fetch", { github_token: e.target.value })} />
          </Row>
          <Row label="RSSHub 地址" hint="可选的 RSS 源使用 {RSSHUB} 占位符">
            <Input className="w-72 font-mono text-[12.5px]" value={draft.fetch.rsshub_base} onChange={(e) => up("fetch", { rsshub_base: e.target.value })} />
          </Row>
          <Row
            label="代理"
            hint="对勾选了“走代理”的信源生效（X 默认勾选），网页请求和浏览器都会用它。清空则使用 Windows 系统代理"
          >
            <div className="flex items-center gap-2">
              <Input
                className="w-60 font-mono text-[12.5px]"
                placeholder="http://127.0.0.1:7897"
                value={draft.fetch.proxy_url}
                onChange={(e) => up("fetch", { proxy_url: e.target.value })}
              />
              <Button variant="secondary" className="h-9 rounded-xl" disabled={testProxy.isPending || dirty} onClick={() => testProxy.mutate()} title={dirty ? "先保存再测试" : "经代理访问 x.com 首页（不登录）"}>
                {testProxy.isPending ? <Loader2 className="size-4 animate-spin" /> : null}测试
              </Button>
            </div>
          </Row>
          <Row
            label="未填写时读取系统代理"
            hint={draft.fetch.system_proxy ? `检测到系统代理：${draft.fetch.system_proxy}` : "没有检测到 Windows 手动代理（PAC 脚本读不到，浏览器仍会自己走系统设置）"}
          >
            <Switch checked={draft.fetch.proxy_use_system} onCheckedChange={(v) => up("fetch", { proxy_use_system: v })} />
          </Row>
        </Card>

        <Card
          icon={<AtSign className="size-4" />}
          title="X（Twitter）"
          desc="用你自己登录的浏览器打开 x.com 搜索页和主页，每天一次、慢慢翻几屏。遇到登录失效、验证或限流会立即停止并通知你，不会自动重试。"
        >
          <Row
            label="每天抓取时间"
            hint={draft.x.time >= draft.scoring.time ? `建议早于打分时间 ${draft.scoring.time}，抓到的推文当天就能打分` : `早于 ${draft.scoring.time} 的谷时打分；没开机时开机后补抓一次`}
          >
            <Input type="time" className="w-32 font-mono" value={draft.x.time} onChange={(e) => up("x", { time: e.target.value })} />
          </Row>
          <Row label="开机补抓" hint="定时点没开机：开机后当天补抓一次">
            <Switch checked={draft.x.catch_up} onCheckedChange={(v) => up("x", { catch_up: v })} />
          </Row>
          <Row label="页面之间随机等待（秒）" hint="每个关键词 / 账号之间等这么久再打开下一个">
            <div className="flex items-center gap-2 font-mono">
              <Input type="number" min={2} className="w-20" value={draft.x.min_delay_seconds} onChange={(e) => up("x", { min_delay_seconds: Number(e.target.value) })} />
              –
              <Input type="number" min={2} className="w-20" value={draft.x.max_delay_seconds} onChange={(e) => up("x", { max_delay_seconds: Number(e.target.value) })} />
            </div>
          </Row>
          <Row label="每页最多" hint="每个关键词 / 账号最多取几条、最多向下滚几屏">
            <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
              <Input type="number" min={1} max={100} className="w-20 font-mono" value={draft.x.per_target_max} onChange={(e) => up("x", { per_target_max: Number(e.target.value) })} />
              条 ·
              <Input type="number" min={0} max={30} className="w-20 font-mono" value={draft.x.max_scrolls} onChange={(e) => up("x", { max_scrolls: Number(e.target.value) })} />
              屏
            </div>
          </Row>
          <Row label="总量上限" hint="单次运行 / 每天（所有 X 信源合计）">
            <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
              <Input type="number" min={1} className="w-20 font-mono" value={draft.x.run_max} onChange={(e) => up("x", { run_max: Number(e.target.value) })} />
              /
              <Input type="number" min={1} className="w-20 font-mono" value={draft.x.daily_max} onChange={(e) => up("x", { daily_max: Number(e.target.value) })} />
              条
            </div>
          </Row>
          <Row label="只要最近几天的推文" hint="置顶的旧帖、翻出来的旧帖不入库">
            <Input type="number" min={1} max={30} className="w-20 font-mono" value={draft.x.max_age_days} onChange={(e) => up("x", { max_age_days: Number(e.target.value) })} />
          </Row>
          <Row label="后台无窗口抓取" hint="如果经常被要求验证，可以关掉，抓取时显示浏览器窗口">
            <Switch checked={draft.x.headless} onCheckedChange={(v) => up("x", { headless: v })} />
          </Row>
          <Row label="信息流显示头像" hint="头像图片也在 X 的服务器上，没开全局代理时可能加载不出来">
            <Switch checked={draft.x.show_avatars} onCheckedChange={(v) => up("x", { show_avatars: v })} />
          </Row>
        </Card>

        <Card icon={<Bell className="size-4" />} title="通知与简报">
          <Row label="系统通知" hint="Windows Toast：每日打分后发一条汇总；规则看出三天内截止的会即时提醒（同一事件只提醒一次）">
            <Switch checked={draft.notify.enabled} onCheckedChange={(v) => up("notify", { enabled: v })} />
          </Row>
          <Row label="免打扰时段">
            <div className="flex items-center gap-2">
              <Input type="time" className="w-28 font-mono" value={draft.notify.quiet_start} onChange={(e) => up("notify", { quiet_start: e.target.value })} />
              <span className="text-muted-foreground">—</span>
              <Input type="time" className="w-28 font-mono" value={draft.notify.quiet_end} onChange={(e) => up("notify", { quiet_end: e.target.value })} />
            </div>
          </Row>
          <Button variant="secondary" className="rounded-xl" onClick={async () => {
            const ok = await systemNotify("信息监控站", "通知测试：高分条目会这样提醒你");
            if (!ok) toast.error("没有通知权限");
          }}>
            <Bell className="size-4" /> 发送测试通知
          </Button>
        </Card>
      </div>

      <AnimatePresence>
        {dirty && (
          <motion.div
            initial={{ y: 24, opacity: 0 }}
            animate={{ y: 0, opacity: 1 }}
            exit={{ y: 24, opacity: 0 }}
            className="fixed bottom-6 left-1/2 z-30 flex -translate-x-1/2 items-center gap-3 rounded-2xl bg-foreground py-2 pr-2 pl-5 text-background shadow-pop"
          >
            <span className="text-[13px]">有未保存的修改</span>
            <Button size="sm" variant="ghost" className="rounded-xl text-background/70 hover:bg-background/10 hover:text-background" onClick={() => setDraft(structuredClone(data!))}>
              <X className="size-3.5" /> 撤销
            </Button>
            <Button size="sm" className="rounded-xl bg-background text-foreground hover:bg-background/90" onClick={() => save.mutate(draft)} disabled={save.isPending}>
              <Check className="size-3.5" /> 保存
            </Button>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

/** 开机自启：直接改系统登录项（不走后端 settings.json，所以不需要点“保存”） */
function StartupCard() {
  const qc = useQueryClient();
  const { data: enabled, isLoading } = useQuery({ queryKey: ["autostart"], queryFn: getAutostart });
  const toggle = useMutation({
    mutationFn: setAutostart,
    onSuccess: (v) => {
      qc.setQueryData(["autostart"], v);
      toast.success(v ? "已开启开机自启" : "已关闭开机自启");
    },
    onError: (e) => toast.error(`修改失败：${String(e)}`),
  });
  return (
    <Card icon={<Power className="size-4" />} title="启动">
      <Row label="开机自启" hint="登录 Windows 后自动在托盘运行（不弹窗口），07:00 抓 X、07:30 谷时打分才能按时执行">
        <Switch checked={!!enabled} disabled={isLoading || toggle.isPending} onCheckedChange={(v) => toggle.mutate(v)} />
      </Row>
    </Card>
  );
}

function Card({ icon, title, desc, children }: { icon: React.ReactNode; title: string; desc?: string; children: React.ReactNode }) {
  return (
    <section className="rounded-[22px] bg-surface-2 p-6">
      <div className="mb-4 flex items-start gap-3">
        <span className="grid size-8 place-items-center rounded-xl bg-card text-muted-foreground shadow-[0_1px_2px_rgb(0_0_0/0.05)] dark:shadow-none">{icon}</span>
        <div>
          <h2 className="text-[15px] font-semibold">{title}</h2>
          {desc && <p className="mt-0.5 text-[12.5px] text-muted-foreground">{desc}</p>}
        </div>
      </div>
      <div className="space-y-4">{children}</div>
    </section>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6">
      <div className="min-w-0">
        <div className="text-[13.5px]">{label}</div>
        {hint && <div className="mt-0.5 text-[12px] text-muted-foreground">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function SliderRow({ label, value, onChange, max = 100 }: { label: string; value: number; onChange: (v: number) => void; max?: number }) {
  return (
    <div className="flex items-center gap-6">
      <div className="w-36 shrink-0 text-[13.5px]">{label}</div>
      <Slider value={[value]} max={max} step={1} onValueChange={(v) => onChange(v[0])} className="flex-1" />
      <span className="w-10 text-right font-mono text-[14px] font-semibold tabular">{value}</span>
    </div>
  );
}

function LabeledInput({ label, value, onChange }: { label: string; value: string; onChange: (v: string) => void }) {
  return (
    <div>
      <div className="mb-1.5 text-[12px] font-medium text-muted-foreground">{label}</div>
      <Input value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function TagEditor({ label, tags, onChange, tone = "muted" }: { label: string; tags: string[]; onChange: (t: string[]) => void; tone?: "muted" | "primary" }) {
  const [v, setV] = useState("");
  const add = () => {
    const t = v.trim();
    if (t && !tags.includes(t)) onChange([...tags, t]);
    setV("");
  };
  return (
    <div>
      <div className="mb-1.5 text-[12px] font-medium text-muted-foreground">{label}</div>
      <div className="flex flex-wrap items-center gap-1.5 rounded-xl bg-card p-2 dark:bg-white/[0.03]">
        {tags.map((t) => (
          <Chip key={t} tone={tone} className="h-7 pr-1 pl-2.5 text-[12.5px]">
            {t}
            <button onClick={() => onChange(tags.filter((x) => x !== t))} className={cn("grid size-5 place-items-center rounded-full hover:bg-foreground/10")}>
              <X className="size-3" />
            </button>
          </Chip>
        ))}
        <input
          value={v}
          onChange={(e) => setV(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && (e.preventDefault(), add())}
          onBlur={add}
          placeholder="输入后回车添加"
          className="h-7 min-w-28 flex-1 bg-transparent px-1 text-[13px] outline-none placeholder:text-muted-foreground/70"
        />
      </div>
    </div>
  );
}
