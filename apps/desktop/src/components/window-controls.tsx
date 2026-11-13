// 自绘的 macOS 风格窗口控件：窗口没有系统标题栏（decorations: false），红黄绿三个按钮在这里画。
// 尺寸 / 间距按 macOS：直径 12px、间隔 8px；鼠标移到按钮组上才显示 × − ⤢ 符号；窗口失焦时变灰。
// 和 macOS 一样不弹文字提示（系统 tooltip 会盖住左上角的 logo）。
import { useEffect, useState } from "react";

import { inTauri } from "@/lib/platform";
import { cn } from "@/lib/utils";

async function currentWindow() {
  const { getCurrentWindow } = await import("@tauri-apps/api/window");
  return getCurrentWindow();
}

/** 窗口是否最大化 / 是否聚焦（最大化时去掉圆角，失焦时红绿灯变灰） */
export function useWindowState() {
  const [state, setState] = useState({ maximized: false, focused: true });
  useEffect(() => {
    if (!inTauri) return;
    let alive = true;
    const offs: (() => void)[] = [];
    (async () => {
      const w = await currentWindow();
      const sync = async () => {
        const maximized = await w.isMaximized();
        if (alive) setState((s) => (s.maximized === maximized ? s : { ...s, maximized }));
      };
      await sync();
      offs.push(await w.onResized(sync));
      offs.push(await w.onFocusChanged(({ payload }) => alive && setState((s) => ({ ...s, focused: payload }))));
    })().catch(() => {});
    return () => {
      alive = false;
      offs.forEach((f) => f());
    };
  }, []);
  return state;
}

const act = (fn: (w: Awaited<ReturnType<typeof currentWindow>>) => Promise<void>) => () => {
  if (inTauri) currentWindow().then(fn).catch(() => {});
};

export function TrafficLights({ focused }: { focused: boolean }) {
  return (
    <div className="traffic-lights group flex items-center gap-2" data-focused={focused}>
      {/* 关闭 = 缩到托盘（Rust 端拦截 CloseRequested 后 hide），后台继续监控 */}
      <Light kind="close" label="关闭（缩到托盘）" onClick={act((w) => w.close())}>
        <path d="M1.5 1.5l5 5M6.5 1.5l-5 5" strokeWidth="1.25" strokeLinecap="round" />
      </Light>
      <Light kind="min" label="最小化" onClick={act((w) => w.minimize())}>
        <path d="M1 4h6" strokeWidth="1.3" strokeLinecap="round" />
      </Light>
      <Light kind="max" label="最大化 / 还原" onClick={act((w) => w.toggleMaximize())}>
        <path d="M1.6 5.1V1.6h3.5zM6.4 2.9v3.5H2.9z" stroke="none" fill="currentColor" />
      </Light>
    </div>
  );
}

function Light({ kind, label, onClick, children }: { kind: "close" | "min" | "max"; label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      onClick={onClick}
      onDoubleClick={(e) => e.stopPropagation()}
      className={cn("traffic-light grid size-3 place-items-center rounded-full", `traffic-light-${kind}`)}
    >
      <svg viewBox="0 0 8 8" className="size-2 opacity-0 transition-opacity duration-100 group-hover:opacity-100" stroke="currentColor" fill="none">
        {children}
      </svg>
    </button>
  );
}
