//! 信息监控站桌面壳：窗口、托盘、Windows 通知，以及拉起 / 守护 Python 后端 sidecar。
//!
//! - 开发模式（`pnpm tauri dev`）：不拉起 sidecar，请先用 dev.ps1 或 `uv run python run.py` 启动后端。
//! - 发布模式（`pnpm tauri build --config src-tauri/tauri.bundle.conf.json`）：
//!   自动启动随安装包附带的 `info-station-backend` sidecar（PyInstaller 打包的 FastAPI）。
//!   若 127.0.0.1:8765 已有后端在运行（例如你手动启动了开发后端），则不重复启动。
//! - 单实例：再次打开时不多开，只把已有窗口唤起并聚焦。
//! - 开机自启：安装版第一次运行时默认开启（之后以设置页开关为准）；自启时带 `--autostart`，只进托盘不弹窗。
//! - 窗口无系统标题栏 / 边框（decorations: false, transparent），红绿灯按钮和圆角由前端绘制。

use std::net::{SocketAddr, TcpStream};
use std::sync::Mutex;
use std::time::Duration;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, RunEvent, WindowEvent};
use tauri_plugin_autostart::ManagerExt as _;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

const BACKEND_PORT: u16 = 8765;
/// 开机自启时传给自己的参数：带上它就只进托盘、不弹主窗口
const AUTOSTART_ARG: &str = "--autostart";
/// 第一次运行时默认开启自启，开过一次就写这个标记，之后完全以用户的开关为准
const AUTOSTART_MARKER: &str = "autostart-initialized";

#[derive(Default)]
struct Backend(Mutex<Option<CommandChild>>);

fn backend_alive() -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], BACKEND_PORT));
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

fn start_backend(app: &tauri::AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    if backend_alive() {
        println!("[info-station] 检测到已有后端在 {BACKEND_PORT} 端口运行，跳过 sidecar");
        return Ok(());
    }
    let (mut rx, child) = app
        .shell()
        .sidecar("info-station-backend")?
        // --parent-pid：PyInstaller 单文件 exe 有两层进程，kill 只能结束外层；后端盯着本进程，退出时自己跟着退出
        .args([
            "--port".to_string(),
            BACKEND_PORT.to_string(),
            "--parent-pid".to_string(),
            std::process::id().to_string(),
        ])
        // 中文 Windows 默认 GBK：让 Python 一律用 UTF-8（stdout 也按 UTF-8 读）
        .env("PYTHONUTF8", "1")
        .env("PYTHONIOENCODING", "utf-8")
        .spawn()?;
    app.state::<Backend>().0.lock().unwrap().replace(child);
    tauri::async_runtime::spawn(async move {
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(line) | CommandEvent::Stderr(line) => {
                    println!("[backend] {}", String::from_utf8_lossy(&line).trim_end());
                }
                CommandEvent::Terminated(p) => {
                    eprintln!("[backend] 进程退出：{:?}", p.code);
                    break;
                }
                _ => {}
            }
        }
    });
    Ok(())
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.unminimize();
        let _ = w.show();
        let _ = w.set_focus();
    }
}

#[tauri::command]
fn backend_port() -> u16 {
    BACKEND_PORT
}

/// 设置页读开机自启状态（走自定义 command，前端不需要额外的 npm 包）
#[tauri::command]
fn autostart_get(app: tauri::AppHandle) -> Result<bool, String> {
    app.autolaunch().is_enabled().map_err(|e| e.to_string())
}

#[tauri::command]
fn autostart_set(app: tauri::AppHandle, enabled: bool) -> Result<bool, String> {
    let m = app.autolaunch();
    if enabled { m.enable() } else { m.disable() }.map_err(|e| e.to_string())?;
    m.is_enabled().map_err(|e| e.to_string())
}

/// 安装版第一次运行：默认开启开机自启（只做一次；用户之后关掉就不再自动打开）
fn init_autostart_once(app: &tauri::AppHandle) {
    let Ok(dir) = app.path().app_config_dir() else { return };
    let marker = dir.join(AUTOSTART_MARKER);
    if marker.exists() {
        return;
    }
    match app.autolaunch().enable() {
        Ok(()) => {
            let _ = std::fs::create_dir_all(&dir);
            let _ = std::fs::write(&marker, "1");
        }
        Err(e) => eprintln!("[info-station] 开启开机自启失败：{e}"),
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        // 单实例必须最先注册：第二个进程把参数转给这里后自己退出
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_main(app)))
        .plugin(
            tauri_plugin_autostart::Builder::new()
                .app_name("InfoStation")
                .arg(AUTOSTART_ARG)
                .build(),
        )
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_shell::init())
        .manage(Backend::default())
        .invoke_handler(tauri::generate_handler![backend_port, autostart_get, autostart_set])
        .setup(|app| {
            // 窗口配置为 visible: false：开机自启只进托盘，手动打开才显示
            let autostarted = std::env::args().any(|a| a == AUTOSTART_ARG);
            if !autostarted {
                show_main(app.handle());
            }
            if !cfg!(debug_assertions) {
                init_autostart_once(app.handle());
            }

            if !cfg!(debug_assertions) || std::env::var("INFO_STATION_SPAWN_BACKEND").is_ok() {
                if let Err(e) = start_backend(app.handle()) {
                    eprintln!("[info-station] 启动后端失败：{e}");
                }
            }

            // 托盘：单击显示主窗口；菜单里可以退出
            let show = MenuItem::with_id(app, "show", "打开信息监控站", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "退出", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            let mut tray = TrayIconBuilder::with_id("main")
                .tooltip("信息监控站")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "show" => show_main(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
                        show_main(tray.app_handle());
                    }
                });
            // 托盘用铺满的简化版雷达图标（16–32px 下比完整图标清楚，深浅色任务栏都看得清）；
            // 由 scripts/render-icons.py 从 icons/source/tray.svg 生成 32×32 原始 RGBA
            tray = tray.icon(tauri::image::Image::new(include_bytes!("../icons/tray-32.rgba"), 32, 32));
            tray.build(app)?;
            Ok(())
        })
        .on_window_event(|window, event| {
            // 关闭窗口 = 最小化到托盘，后台继续监控
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "main" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("启动 Tauri 失败");

    app.run(|handle, event| {
        if let RunEvent::Exit = event {
            if let Some(child) = handle.state::<Backend>().0.lock().unwrap().take() {
                let _ = child.kill();
            }
        }
    });
}
