# apps/desktop

桌面端前端（Vite + React 19 + TypeScript + Tailwind CSS v4 + shadcn/ui + motion）与 Tauri 2 壳（`src-tauri/`）。

- `pnpm dev`：浏览器版（http://localhost:1420），需要后端在 127.0.0.1:8765 运行
- `pnpm tauri dev`：桌面窗口（开发模式不会拉起 sidecar）
- `pnpm typecheck` / `pnpm build`
- `pnpm build:app`：带 sidecar 打包（先运行仓库根目录的 `scripts/build-sidecar.ps1`）

后端地址可用环境变量 `VITE_API_BASE` 覆盖；若后端设置了 `INFO_STATION_TOKEN`，前端需同时设置 `VITE_LOCAL_TOKEN`。
设计约定：分层靠色阶（canvas / surface / surface-2）、圆角与柔和阴影，尽量不用 1px 分割线；颜色变量在 `src/index.css`。
