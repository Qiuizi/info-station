import { motion } from "motion/react";
import type { ReactNode } from "react";

import type { Category } from "@/lib/api";
import { CATEGORY_META, scoreTone } from "@/lib/format";
import { cn } from "@/lib/utils";

export function ScoreRing({ score, size = 40, muted = false }: { score: number; size?: number; muted?: boolean }) {
  const tone = scoreTone(score);
  const stroke = 3;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative grid shrink-0 place-items-center" style={{ width: size, height: size }}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="currentColor" strokeWidth={stroke} className="text-foreground/[0.06]" />
        <motion.circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tone.ring}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: c * (1 - Math.min(100, score) / 100) }}
          transition={{ duration: 0.9, ease: [0.22, 1, 0.36, 1] }}
          opacity={muted ? 0.45 : 1}
        />
      </svg>
      <span className={cn("absolute font-mono text-[13px] font-semibold tabular", muted ? "text-muted-foreground" : tone.text)}>{score}</span>
    </div>
  );
}

export function CategoryDot({ category, className }: { category: Category; className?: string }) {
  return <span className={cn("inline-block size-1.5 shrink-0 rounded-full", CATEGORY_META[category]?.dot ?? "bg-muted-foreground", className)} />;
}

export function Chip({ children, className, tone = "muted" }: { children: ReactNode; className?: string; tone?: "muted" | "warm" | "hot" | "primary" | "good" }) {
  const tones = {
    muted: "bg-foreground/[0.05] text-muted-foreground",
    warm: "bg-warm/12 text-[color-mix(in_oklch,var(--warm)_80%,var(--foreground))]",
    hot: "bg-hot/12 text-hot",
    primary: "bg-primary/10 text-primary",
    good: "bg-good/12 text-good",
  } as const;
  return (
    <span className={cn("inline-flex h-[22px] items-center gap-1 rounded-full px-2 text-[11.5px] font-medium whitespace-nowrap", tones[tone], className)}>
      {children}
    </span>
  );
}

export function PageHeader({ title, subtitle, children }: { title: ReactNode; subtitle?: ReactNode; children?: ReactNode }) {
  return (
    // 页头也能拖动窗口 / 双击最大化（无系统标题栏后，这里相当于 macOS 的统一工具栏）
    <div data-tauri-drag-region className="flex flex-wrap items-end justify-between gap-4 px-10 pt-9 pb-5">
      <div data-tauri-drag-region className="min-w-0">
        <h1 data-tauri-drag-region className="text-[26px] leading-tight font-semibold tracking-[-0.02em]">{title}</h1>
        {subtitle && <p data-tauri-drag-region className="mt-1.5 text-[13.5px] text-muted-foreground">{subtitle}</p>}
      </div>
      {children && <div className="flex items-center gap-2">{children}</div>}
    </div>
  );
}

export interface SegOption<T extends string> {
  value: T;
  label: ReactNode;
}

/** 分段控件：滑动的高亮底块（无描边） */
export function Segmented<T extends string>({ value, onChange, options, id, size = "md" }: {
  value: T;
  onChange: (v: T) => void;
  options: SegOption<T>[];
  id: string;
  size?: "sm" | "md";
}) {
  return (
    <div className={cn("inline-flex items-center rounded-[11px] bg-foreground/[0.05] p-[3px]", size === "sm" && "rounded-[9px]")}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          onClick={() => onChange(o.value)}
          className={cn(
            "relative rounded-[8px] px-3 font-medium transition-colors",
            size === "md" ? "h-7 text-[13px]" : "h-6 px-2.5 text-[12px]",
            value === o.value ? "text-foreground" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {value === o.value && (
            <motion.span
              layoutId={`seg-${id}`}
              className="absolute inset-0 rounded-[8px] bg-card shadow-[0_1px_3px_rgb(0_0_0/0.08),0_1px_1px_rgb(0_0_0/0.03)] dark:bg-white/[0.09] dark:shadow-none"
              transition={{ type: "spring", bounce: 0.15, duration: 0.4 }}
            />
          )}
          <span className="relative z-10 flex items-center gap-1.5">{o.label}</span>
        </button>
      ))}
    </div>
  );
}

export function EmptyState({ icon, title, hint, children }: { icon: ReactNode; title: string; hint?: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center px-6 py-24 text-center">
      <div className="mb-4 grid size-14 place-items-center rounded-2xl bg-foreground/[0.04] text-muted-foreground">{icon}</div>
      <div className="text-[15px] font-medium">{title}</div>
      {hint && <div className="mt-1.5 max-w-sm text-[13px] text-muted-foreground">{hint}</div>}
      {children && <div className="mt-5">{children}</div>}
    </div>
  );
}

export function StatTile({ label, value, hint, icon, accent }: { label: string; value: ReactNode; hint?: ReactNode; icon: ReactNode; accent: string }) {
  return (
    <div className="group relative overflow-hidden rounded-2xl bg-surface-2 px-4 py-3.5 transition-colors hover:bg-accent/60">
      <div className="flex items-center gap-2 text-[12.5px] text-muted-foreground">
        <span className={cn("grid size-6 place-items-center rounded-lg", accent)}>{icon}</span>
        {label}
      </div>
      <div className="mt-2 flex items-baseline gap-2">
        <span className="font-mono text-[24px] font-semibold tracking-tight tabular">{value}</span>
        {hint && <span className="text-[12px] text-muted-foreground">{hint}</span>}
      </div>
    </div>
  );
}
