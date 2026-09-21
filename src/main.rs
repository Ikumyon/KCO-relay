#![cfg_attr(windows, windows_subsystem = "windows")]

mod platform;

use std::sync::{Arc, Mutex};
use std::time::Duration;
use std::path::Path;
use std::fs;
use axum::{
    routing::{get, post},
    Router, Json, response::sse::{Event, KeepAlive, Sse},
    http::StatusCode,
};
use futures_util::stream::Stream;
use serde::{Deserialize, Serialize};
use tokio::sync::broadcast;
use tower_http::cors::CorsLayer;
use chrono::{Local, Utc};
use rusqlite::{params, Connection};

slint::include_modules!();

const SETTINGS_FILE: &str = "settings.json";
const DB_FILE: &str = "nowplaying.db";
const LOG_FILE: &str = "logs/relay.log";

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct AppSettings {
    pub log_retention_days: i32,
    pub db_retention_days: i32,
    #[serde(default)]
    pub theme_mode: ThemeMode,
}

fn populate_settings_editor(window: &SettingsWindow, settings: &AppSettings) {
    window.set_theme_mode(settings.theme_mode.setting_value());
    window.set_log_days_str(settings.log_retention_days.to_string().into());
    window.set_db_days_str(settings.db_retention_days.to_string().into());
    window.set_save_error("".into());
}

fn apply_visual_settings(ui: &AppWindow, editor: &SettingsWindow, settings: &AppSettings) {
    let dark = settings.theme_mode.prefers_dark();
    Theme::get(ui).set_dark_mode(dark);
    Theme::get(editor).set_dark_mode(dark);
}

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ThemeMode {
    System,
    Light,
    Dark,
}

impl Default for ThemeMode {
    fn default() -> Self {
        Self::System
    }
}

impl ThemeMode {
    pub fn setting_value(&self) -> i32 {
        match self {
            Self::System => 0,
            Self::Light => 1,
            Self::Dark => 2,
        }
    }

    pub fn from_setting_value(value: i32) -> Self {
        match value {
            1 => Self::Light,
            2 => Self::Dark,
            _ => Self::System,
        }
    }

    pub fn prefers_dark(&self) -> bool {
        match self {
            Self::System => platform::system_prefers_dark(),
            Self::Light => false,
            Self::Dark => true,
        }
    }
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            log_retention_days: 10,
            db_retention_days: 10,
            theme_mode: ThemeMode::System,
        }
    }
}

impl AppSettings {
    pub fn load() -> Self {
        if let Ok(content) = fs::read_to_string(SETTINGS_FILE) {
            if let Ok(settings) = serde_json::from_str(&content) {
                return settings;
            }
        }
        Self::default()
    }

    pub fn save(&self) -> Result<(), Box<dyn std::error::Error>> {
        let json_str = serde_json::to_string_pretty(self)?;
        fs::write(SETTINGS_FILE, json_str)?;
        Ok(())
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct NowPlayingData {
    #[serde(default = "default_schema")]
    pub schema: String,
    #[serde(default = "default_ts")]
    pub ts: i64,
    pub title: String,
}

fn default_schema() -> String {
    "nowplaying.v1".to_string()
}

fn default_ts() -> i64 {
    Utc::now().timestamp_millis()
}

#[derive(Clone)]
pub struct AppState {
    pub current_data: Arc<Mutex<Option<NowPlayingData>>>,
    pub tx: broadcast::Sender<NowPlayingData>,
    pub ui_handle: slint::Weak<AppWindow>,
    pub session_id: String,
}

// ログファイル出力
fn append_log_to_file(msg: &str) {
    if let Some(parent) = Path::new(LOG_FILE).parent() {
        let _ = fs::create_dir_all(parent);
    }
    let now = Local::now().format("%Y-%m-%d %H:%M:%S");
    let line = format!("[{}] {}\n", now, msg);
    use std::io::Write;
    if let Ok(mut file) = fs::OpenOptions::new().create(true).append(true).open(LOG_FILE) {
        let _ = file.write_all(line.as_bytes());
    }
}

// データベース初期化
fn init_db() -> Result<Connection, Box<dyn std::error::Error>> {
    let conn = Connection::open(DB_FILE)?;
    conn.execute(
        "CREATE TABLE IF NOT EXISTS nowplaying_state (
            id INTEGER PRIMARY KEY,
            schema TEXT NOT NULL,
            ts INTEGER NOT NULL,
            title TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )",
        [],
    )?;
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            schema TEXT NOT NULL,
            ts INTEGER NOT NULL,
            title TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )",
        [],
    )?;
    Ok(conn)
}

// 古いDBレコードのクリーンアップ
fn cleanup_old_events(days: i32) {
    if let Ok(conn) = Connection::open(DB_FILE) {
        let cutoff_millis = Utc::now().timestamp_millis() - (days as i64 * 86_400_000);
        let _ = conn.execute(
            "DELETE FROM events WHERE created_at < ?1",
            params![cutoff_millis],
        );
    }
}

// 最新のステートを取得
fn get_saved_state() -> Option<(NowPlayingData, String)> {
    if let Ok(conn) = Connection::open(DB_FILE) {
        let mut stmt = conn.prepare("SELECT schema, ts, title, updated_at FROM nowplaying_state WHERE id = 1").ok()?;
        let mut rows = stmt.query([]).ok()?;
        if let Some(row) = rows.next().ok()? {
            let schema: String = row.get(0).unwrap_or_default();
            let ts: i64 = row.get(1).unwrap_or(0);
            let title: String = row.get(2).unwrap_or_default();
            let updated_at: i64 = row.get(3).unwrap_or(0);
            let updated_str = if updated_at > 0 {
                chrono::DateTime::from_timestamp_millis(updated_at)
                    .map(|dt| dt.with_timezone(&Local).format("%Y/%m/%d %H:%M:%S").to_string())
                    .unwrap_or_else(|| "-".to_string())
            } else {
                "-".to_string()
            };
            return Some((NowPlayingData { schema, ts, title }, updated_str));
        }
    }
    None
}

// DBステート更新とイベント記録
fn save_state_to_db(data: &NowPlayingData, session_id: &str) {
    if let Ok(conn) = Connection::open(DB_FILE) {
        let now_millis = Utc::now().timestamp_millis();
        let _ = conn.execute(
            "INSERT OR REPLACE INTO nowplaying_state (id, schema, ts, title, updated_at) VALUES (1, ?1, ?2, ?3, ?4)",
            params![data.schema, data.ts, data.title, now_millis],
        );
        let _ = conn.execute(
            "INSERT INTO events (session_id, schema, ts, title, created_at) VALUES (?1, ?2, ?3, ?4, ?5)",
            params![session_id, data.schema, data.ts, data.title, now_millis],
        );
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // プラットフォーム抽象化による二重起動防止チェック
    if !platform::ensure_single_instance() {
        return Ok(());
    }

    let main_window = AppWindow::new()?;
    let ui_handle = main_window.as_weak();

    // ウィンドウ復帰処理の登録（タスクトレイ・二重起動からの復帰用）
    let h_restore = ui_handle.clone();
    platform::register_window_handle(move || {
        let h = h_restore.clone();
        let _ = slint::invoke_from_event_loop(move || {
            if let Some(ui) = h.upgrade() {
                ui.window().set_minimized(false);
                let _ = ui.show();
            }
        });
    });

    // 設定サブウィンドウ初期化
    let settings_window = SettingsWindow::new()?;
    let sw_handle = settings_window.as_weak();

    // 設定ロード
    let settings = AppSettings::load();
    let saved_settings = std::rc::Rc::new(std::cell::RefCell::new(settings.clone()));
    let use_dark_theme = settings.theme_mode.prefers_dark();
    Theme::get(&main_window).set_dark_mode(use_dark_theme);
    Theme::get(&settings_window).set_dark_mode(use_dark_theme);
    populate_settings_editor(&settings_window, &settings);

    // DB初期化とクリーンアップ
    let _ = init_db();
    cleanup_old_events(settings.db_retention_days);

    let session_id = format!("sess_{}", Utc::now().timestamp_millis());

    // ログ追記用ヘルパー
    let append_log = {
        let handle = ui_handle.clone();
        move |msg: &str| {
            append_log_to_file(msg);
            let time_str = Local::now().format("%H:%M:%S").to_string();
            let log_line = format!("[{}] {}\n", time_str, msg);
            let handle = handle.clone();
            let _ = slint::invoke_from_event_loop(move || {
                if let Some(ui) = handle.upgrade() {
                    let mut current = ui.get_log_text().to_string();
                    current.push_str(&log_line);
                    if current.len() > 10000 {
                        let offset = current.len() - 8000;
                        current = current[offset..].to_string();
                    }
                    ui.set_log_text(current.into());
                }
            });
        }
    };

    append_log("KCO Relay サーバー (Rust/Slint) 起動中...");

    let (tx, _rx) = broadcast::channel::<NowPlayingData>(100);
    let current_data = Arc::new(Mutex::new(None));

    // 前回保存ステートの復元
    if let Some((saved, updated_str)) = get_saved_state() {
        if !saved.title.is_empty() {
            *current_data.lock().unwrap() = Some(saved.clone());
            main_window.set_schema_text(saved.schema.into());
            main_window.set_ts_text(saved.ts.to_string().into());
            main_window.set_updated_text(updated_str.into());
            main_window.set_current_title(saved.title.clone().into());
            append_log(&format!("前回の再生状態を復元: {}", saved.title));
        }
    }

    let state = AppState {
        current_data: current_data.clone(),
        tx: tx.clone(),
        ui_handle: ui_handle.clone(),
        session_id: session_id.clone(),
    };

    // --- 1. 入力サーバー (Port 5000: POST /update) ---
    let state_5000 = state.clone();
    let log_fn_5000 = append_log.clone();
    let handle_5000 = ui_handle.clone();

    tokio::spawn(async move {
        let app = Router::new()
            .route("/update", post(handle_update))
            .layer(CorsLayer::permissive())
            .with_state(state_5000);

        let addr = "127.0.0.1:5000";
        match tokio::net::TcpListener::bind(addr).await {
            Ok(listener) => {
                log_fn_5000(&format!("Inputサーバー開始: Port 5000"));
                let h = handle_5000.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = h.upgrade() {
                        ui.set_status_in("5000: Input (実行中)".into());
                        ui.set_status_in_color(slint::Color::from_rgb_u8(22, 163, 74));
                    }
                });

                if let Err(e) = axum::serve(listener, app).await {
                    log_fn_5000(&format!("Inputサーバーエラー: {}", e));
                }
            }
            Err(e) => {
                log_fn_5000(&format!("Inputサーバーポートバインド失敗 (5000): {}", e));
                let h = handle_5000.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = h.upgrade() {
                        ui.set_status_in("5000: Input (エラー)".into());
                        ui.set_status_in_color(slint::Color::from_rgb_u8(220, 38, 38));
                    }
                });
            }
        }
    });

    // --- 2. 出力サーバー (Port 5001: GET /sse) ---
    let state_5001 = state.clone();
    let log_fn_5001 = append_log.clone();
    let handle_5001 = ui_handle.clone();

    tokio::spawn(async move {
        let app = Router::new()
            .route("/sse", get(handle_sse))
            .layer(CorsLayer::permissive())
            .with_state(state_5001);

        let addr = "127.0.0.1:5001";
        match tokio::net::TcpListener::bind(addr).await {
            Ok(listener) => {
                log_fn_5001(&format!("Outputサーバー開始: Port 5001"));
                let h = handle_5001.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = h.upgrade() {
                        ui.set_status_out("5001: Output (実行中)".into());
                        ui.set_status_out_color(slint::Color::from_rgb_u8(22, 163, 74));
                    }
                });

                if let Err(e) = axum::serve(listener, app).await {
                    log_fn_5001(&format!("Outputサーバーエラー: {}", e));
                }
            }
            Err(e) => {
                log_fn_5001(&format!("Outputサーバーポートバインド失敗 (5001): {}", e));
                let h = handle_5001.clone();
                let _ = slint::invoke_from_event_loop(move || {
                    if let Some(ui) = h.upgrade() {
                        ui.set_status_out("5001: Output (エラー)".into());
                        ui.set_status_out_color(slint::Color::from_rgb_u8(220, 38, 38));
                    }
                });
            }
        }
    });

    // --- 3. タスクトレイアイコン設定 ---
    let _tray = platform::setup_tray(
        move || {
            platform::show_main_window();
        },
        move || {
            std::process::exit(0);
        },
    )?;

    // --- 4. Slint UIコールバック設定 ---
    let h_min_win = ui_handle.clone();
    main_window.on_minimize_window(move || {
        if let Some(ui) = h_min_win.upgrade() {
            ui.window().set_minimized(true);
        }
    });

    let h_min = ui_handle.clone();
    main_window.on_minimize_to_tray(move || {
        if let Some(ui) = h_min.upgrade() {
            platform::hide_to_tray(ui.window());
        }
    });

    let h_max = ui_handle.clone();
    main_window.on_toggle_maximize(move || {
        if let Some(ui) = h_max.upgrade() {
            let win = ui.window();
            let is_max = win.is_maximized();
            win.set_maximized(!is_max);
        }
    });

    main_window.on_quit_app(move || {
        std::process::exit(0);
    });

    let h_drag = ui_handle.clone();
    main_window.on_drag_window(move |dx, dy| {
        if let Some(ui) = h_drag.upgrade() {
            let win = ui.window();
            let pos = win.position();
            win.set_position(slint::PhysicalPosition::new(
                pos.x + dx as i32,
                pos.y + dy as i32,
            ));
        }
    });

    // 設定ウィンドウを開く
    let sw_open = sw_handle.clone();
    let settings_open = saved_settings.clone();
    main_window.on_open_settings(move || {
        if let Some(sw) = sw_open.upgrade() {
            populate_settings_editor(&sw, &settings_open.borrow());
            sw.show().unwrap();
        }
    });

    // 設定サブウィンドウの増減ステップ
    let sw_step_log = sw_handle.clone();
    settings_window.on_step_log_days(move |delta| {
        if let Some(sw) = sw_step_log.upgrade() {
            let cur = sw.get_log_days_str().to_string().parse::<i32>().unwrap_or(10);
            let next = (cur + delta).clamp(1, 365);
            sw.set_log_days_str(next.to_string().into());
        }
    });

    let sw_step_db = sw_handle.clone();
    settings_window.on_step_db_days(move |delta| {
        if let Some(sw) = sw_step_db.upgrade() {
            let cur = sw.get_db_days_str().to_string().parse::<i32>().unwrap_or(10);
            let next = (cur + delta).clamp(1, 365);
            sw.set_db_days_str(next.to_string().into());
        }
    });

    // 設定サブウィンドウのキャンセル
    let sw_cancel = sw_handle.clone();
    let settings_cancel = saved_settings.clone();
    settings_window.on_cancel_settings(move || {
        if let Some(sw) = sw_cancel.upgrade() {
            populate_settings_editor(&sw, &settings_cancel.borrow());
            sw.hide().unwrap();
        }
    });

    // 設定保存コールバック
    let sw_save = sw_handle.clone();
    let main_theme = main_window.as_weak();
    let log_save = append_log.clone();
    let settings_save = saved_settings.clone();
    settings_window.on_save_settings(move |log_str, db_str, theme_value| {
        let log_days = log_str.to_string().parse::<i32>().unwrap_or(10).clamp(1, 365);
        let db_days = db_str.to_string().parse::<i32>().unwrap_or(10).clamp(1, 365);
        let theme_mode = ThemeMode::from_setting_value(theme_value);
        let new_settings = AppSettings {
            log_retention_days: log_days,
            db_retention_days: db_days,
            theme_mode,
        };
        if let Err(e) = new_settings.save() {
            log_save(&format!("設定の保存に失敗: {}", e));
            if let Some(sw) = sw_save.upgrade() {
                sw.set_save_error("設定を保存できませんでした。保存先を確認して再試行してください。".into());
            }
            return;
        }
        *settings_save.borrow_mut() = new_settings.clone();
        log_save(&format!("設定を保存しました (ログ: {}日, DB: {}日)", log_days, db_days));
        cleanup_old_events(db_days);
        if let Some(sw) = sw_save.upgrade() {
            sw.set_save_error("".into());
            if let Some(ui) = main_theme.upgrade() {
                apply_visual_settings(&ui, &sw, &new_settings);
            }
            sw.hide().unwrap();
        }
    });

    append_log("サーバー待受中 (Port 5000 / 5001)");

    main_window.show()?;
    #[cfg(windows)]
    let corner_setup_timer = {
        // show() 直後は HWND がまだない場合があるため、イベントループ内で生成を待つ。
        // このタイマーは run() が戻るまで保持し、設定完了・失敗時に停止する。
        let timer = std::rc::Rc::new(slint::Timer::default());
        let timer_weak = std::rc::Rc::downgrade(&timer);
        let window_weak = main_window.as_weak();
        let log_corners = append_log.clone();
        timer.start(slint::TimerMode::Repeated, Duration::from_millis(16), move || {
            let Some(timer) = timer_weak.upgrade() else { return; };
            let Some(ui) = window_weak.upgrade() else {
                timer.stop();
                return;
            };
            match platform::configure_main_window_corners(ui.window()) {
                Ok(false) => {} // ウィンドウ生成待ち。次のイベントループで再試行する。
                Ok(true) => {
                    timer.stop();
                    log_corners("ウィンドウ枠の角丸を適用しました");
                }
                Err(message) => {
                    timer.stop();
                    log_corners(&message);
                }
            }
        });
        timer
    };
    // 「OSに合わせる」は編集中の値ではなく、保存済み設定に従う。
    let theme_timer = slint::Timer::default();
    let theme_ui = main_window.as_weak();
    let theme_editor = settings_window.as_weak();
    let theme_settings = saved_settings.clone();
    theme_timer.start(slint::TimerMode::Repeated, Duration::from_secs(1), move || {
        let settings = theme_settings.borrow();
        if let (Some(ui), Some(editor)) = (theme_ui.upgrade(), theme_editor.upgrade()) {
            if matches!(settings.theme_mode, ThemeMode::System)
                && Theme::get(&ui).get_dark_mode() != settings.theme_mode.prefers_dark()
            {
                apply_visual_settings(&ui, &editor, &settings);
            }
        }
    });
    main_window.run()?;
    theme_timer.stop();
    #[cfg(windows)]
    corner_setup_timer.stop();
    Ok(())
}

// POST /update ハンドラ
async fn handle_update(
    axum::extract::State(state): axum::extract::State<AppState>,
    Json(payload): Json<NowPlayingData>,
) -> (StatusCode, Json<serde_json::Value>) {
    if payload.title.is_empty() {
        return (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": "タイトルが空です"})),
        );
    }

    let title = payload.title.clone();
    let schema = payload.schema.clone();
    let ts = payload.ts;
    let now_str = Local::now().format("%Y/%m/%d %H:%M:%S").to_string();
    let ts_str = format!("{}", ts);

    // 共有データ更新
    {
        let mut cur = state.current_data.lock().unwrap();
        *cur = Some(payload.clone());
    }

    // SQLite DB更新 & イベント記録
    save_state_to_db(&payload, &state.session_id);

    // SSEブロードキャスト
    let _ = state.tx.send(payload);

    // UI更新
    let h = state.ui_handle.clone();
    let log_title = title.clone();
    let _ = slint::invoke_from_event_loop(move || {
        if let Some(ui) = h.upgrade() {
            ui.set_schema_text(schema.into());
            ui.set_updated_text(now_str.into());
            ui.set_ts_text(ts_str.into());
            ui.set_current_title(title.into());

            let mut cur_log = ui.get_log_text().to_string();
            let time_str = Local::now().format("%H:%M:%S").to_string();
            cur_log.push_str(&format!("[{}] タイトル更新: {}\n", time_str, log_title));
            ui.set_log_text(cur_log.into());
        }
    });

    (StatusCode::OK, Json(serde_json::json!({"status": "ok"})))
}

// GET /sse ハンドラ
async fn handle_sse(
    axum::extract::State(state): axum::extract::State<AppState>,
) -> Sse<impl Stream<Item = Result<Event, std::convert::Infallible>>> {
    let mut rx = state.tx.subscribe();
    let initial_data = {
        let cur = state.current_data.lock().unwrap();
        cur.clone()
    };

    let stream = async_stream::stream! {
        // 初回送信
        if let Some(data) = initial_data {
            if let Ok(json_str) = serde_json::to_string(&data) {
                yield Ok(Event::default().event("nowplaying").data(json_str));
            }
        }

        // 更新時送信
        while let Ok(data) = rx.recv().await {
            if let Ok(json_str) = serde_json::to_string(&data) {
                yield Ok(Event::default().event("nowplaying").data(json_str));
            }
        }
    };

    Sse::new(stream).keep_alive(KeepAlive::default().interval(Duration::from_secs(15)))
}
