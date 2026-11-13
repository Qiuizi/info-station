import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { api, type ScoreRunResult, type ScoreSchedule } from "@/lib/api";
import { parseDate } from "@/lib/format";

/** 每日谷时批量打分的计划（下次打分时间、是否峰时等），每分钟刷新；批量进行中时每 5 秒刷新。 */
export function useScoreSchedule(enabled = true) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["score-schedule"],
    enabled,
    queryFn: () => api<ScoreSchedule>("/api/score/schedule"),
    refetchInterval: (q) => (q.state.data?.running ? 5_000 : 60_000),
  });
  // 批量打分结束时刷新信息流与统计
  const wasRunning = useRef(false);
  const running = !!q.data?.running;
  useEffect(() => {
    if (wasRunning.current && !running) {
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      qc.invalidateQueries({ queryKey: ["brief"] });
    }
    wasRunning.current = running;
  }, [running, qc]);
  return q;
}

/** “今天 07:30” / “明天 07:30” / “11-13 周五 07:30” */
export function slotLabel(iso?: string | null): string {
  const d = parseDate(iso);
  if (!d) return "—";
  const hm = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  const day0 = new Date();
  day0.setHours(0, 0, 0, 0);
  const diff = Math.floor((d.getTime() - day0.getTime()) / 86_400_000);
  if (diff === 0) return `今天 ${hm}`;
  if (diff === 1) return `明天 ${hm}`;
  if (diff === 2) return `后天 ${hm}`;
  const wd = "日一二三四五六"[d.getDay()];
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")} 周${wd} ${hm}`;
}

/** 某个“每天的时刻”是否落在工作日高峰窗口（设置页即时提示用；规则来自后端）。 */
export function isPeakTime(hhmm: string, windows: [string, string][] = [["09:00", "12:00"], ["14:00", "18:00"]]): boolean {
  const t = hhmm.padStart(5, "0");
  return windows.some(([a, b]) => a <= t && t < b);
}

export const PLAN_LABEL: Record<ScoreSchedule["plan"]["reason"], string> = {
  scheduled: "定时",
  catch_up: "补跑",
  wait_offpeak: "错过定时，顺延到谷时",
  near_next: "定时",
  done: "定时",
  no_catch_up: "定时",
};

/**
 * 立即打分 / 重新打分：高峰时段后端会要求确认，这里弹出带“仍然打分”按钮的提醒。
 * path 例如 "/api/score/run" 或 "/api/rescore?days=30&categories=school,competition"
 */
export function useRunScoring() {
  const qc = useQueryClient();
  const m = useMutation({
    mutationFn: ({ path, force }: { path: string; force?: boolean }) =>
      api<ScoreRunResult>(`${path}${path.includes("?") ? "&" : "?"}force=${force ? "true" : "false"}`, { method: "POST" }),
    onSuccess: (r, vars) => {
      qc.invalidateQueries({ queryKey: ["score-schedule"] });
      if (r.needs_confirm) {
        const until = slotLabel(r.next_offpeak);
        toast.warning("现在是 DeepSeek 高峰时段", {
          description: `${r.message ?? "价格是谷时的 2 倍。"}${r.pending ? `待打分 ${r.pending} 条，` : ""}${until} 起恢复谷时价；不急的话会在每日定时自动打分。`,
          duration: 12_000,
          action: { label: "仍然打分", onClick: () => m.mutate({ ...vars, force: true }) },
        });
        return;
      }
      if (r.running) return toast("正在打分中，稍等片刻");
      if (r.started) toast.success(`开始打分：${r.pending ?? 0} 条${r.requeued ? `（已放回队列 ${r.requeued} 条）` : ""}`);
      qc.invalidateQueries({ queryKey: ["items"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });
  return m;
}
