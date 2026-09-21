use std::io::{Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::Path;
use std::sync::Mutex;
use tray_item::TrayItem;

static LISTENER: Mutex<Option<UnixListener>> = Mutex::new(None);
static WINDOW_RESTORE_FN: Mutex<Option<Box<dyn Fn() + Send + Sync + 'static>>> = Mutex::new(None);
const SOCK_PATH: &str = "/tmp/kco_relay_single_instance.sock";

/// ウィンドウ復帰用コールバックを登録
pub fn register_window_handle<F>(restore_fn: F)
where
    F: Fn() + Send + Sync + 'static,
{
    let mut guard = WINDOW_RESTORE_FN.lock().unwrap();
    *guard = Some(Box::new(restore_fn));
}

/// 二重起動を防止（Linux: Unixドメインソケット IPC 方式）
/// 既存プロセスが存在する場合は復帰通知を送って false を返す
pub fn ensure_single_instance() -> bool {
    // 既存ソケットへの接続確認
    if Path::new(SOCK_PATH).exists() {
        if let Ok(mut stream) = UnixStream::connect(SOCK_PATH) {
            // 既存プロセスが稼働中 -> 復帰通知を送信して即終了
            let _ = stream.write_all(b"SHOW\n");
            return false;
        } else {
            // プロセス異常終了後の残骸ソケットを自動クリーンアップ
            let _ = std::fs::remove_file(SOCK_PATH);
        }
    }

    match UnixListener::bind(SOCK_PATH) {
        Ok(listener) => {
            let listener_clone = match listener.try_clone() {
                Ok(l) => l,
                Err(_) => {
                    let mut guard = LISTENER.lock().unwrap();
                    *guard = Some(listener);
                    return true;
                }
            };

            let mut guard = LISTENER.lock().unwrap();
            *guard = Some(listener);

            // 多重起動時の復帰要求を受信するバックグラウンドスレッド
            std::thread::spawn(move || {
                for stream in listener_clone.incoming() {
                    if let Ok(mut s) = stream {
                        let mut buf = [0u8; 16];
                        let _ = s.read(&mut buf);
                        show_main_window();
                    }
                }
            });

            true
        }
        Err(_) => false,
    }
}

/// メインウィンドウを非表示（Linux: 最小化で常駐）
pub fn hide_to_tray(window: &slint::Window) {
    window.set_minimized(true);
}

/// メインウィンドウを表示・復帰
pub fn show_main_window() {
    let guard = WINDOW_RESTORE_FN.lock().unwrap();
    if let Some(ref f) = *guard {
        f();
    }
}

/// タスクトレイの初期化（Linux）
pub struct TrayHandle {
    _tray: Option<TrayItem>,
}

pub fn setup_tray<FShow, FQuit>(on_show: FShow, on_quit: FQuit) -> Result<TrayHandle, Box<dyn std::error::Error>>
where
    FShow: Fn() + Send + Sync + 'static,
    FQuit: Fn() + Send + Sync + 'static,
{
    let tray = (|| {
        let mut t = TrayItem::new(
            "KCO Relay Server",
            tray_item::IconSource::Resource("audio-x-generic"),
        )
        .ok()?;
        t.add_menu_item("表示 (Show)", on_show).ok()?;
        t.add_menu_item("終了 (Exit)", on_quit).ok()?;
        Some(t)
    })();

    Ok(TrayHandle { _tray: tray })
}
/// Linux ではデスクトップ環境ごとのテーマ検出を行わず、ライトテーマを既定にする。
pub fn system_prefers_dark() -> bool {
    false
}
