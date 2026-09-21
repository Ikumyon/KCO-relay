use std::cell::Cell;
use std::sync::Mutex;
use raw_window_handle::{HandleError, HasWindowHandle, RawWindowHandle};
use tray_item::TrayItem;
use windows_sys::Win32::Foundation::{BOOL, ERROR_ALREADY_EXISTS, GetLastError, HANDLE, HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Gdi::{
    CreateRoundRectRgn, DeleteObject, SetWindowRgn,
};
use windows_sys::Win32::System::Threading::CreateMutexW;
use windows_sys::Win32::System::Registry::{RegGetValueW, HKEY_CURRENT_USER, RRF_RT_REG_DWORD};
use windows_sys::Win32::UI::HiDpi::GetDpiForWindow;
use windows_sys::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    EnumWindows, FindWindowW, GetWindowRect, GetWindowTextW, IsIconic, IsZoomed,
    SetForegroundWindow, ShowWindow, SW_HIDE, SW_RESTORE, SW_SHOW,
    WM_DPICHANGED, WM_NCACTIVATE, WM_NCDESTROY, WM_NCPAINT, WM_SIZE,
};

static SINGLE_INSTANCE_MUTEX: Mutex<Option<HANDLE>> = Mutex::new(None);
static WINDOW_RESTORE_FN: Mutex<Option<Box<dyn Fn() + Send + Sync + 'static>>> = Mutex::new(None);
const WINDOW_TITLE: &str = "KCO Relay Server";
const CORNER_SUBCLASS_ID: usize = 1;

thread_local! {
    // SetWindowRgn が同期的に発生させるウィンドウメッセージによる再入を防ぐ。
    static UPDATING_WINDOW_REGION: Cell<bool> = const { Cell::new(false) };
}

/// Windows の「アプリ モード」を読み、OS がダークテーマを選んでいるか返す。
pub fn system_prefers_dark() -> bool {
    let key: Vec<u16> = "Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize\0"
        .encode_utf16()
        .collect();
    let value: Vec<u16> = "AppsUseLightTheme\0".encode_utf16().collect();
    let mut apps_use_light_theme: u32 = 1;
    let mut size = std::mem::size_of::<u32>() as u32;

    unsafe {
        RegGetValueW(
            HKEY_CURRENT_USER,
            key.as_ptr(),
            value.as_ptr(),
            RRF_RT_REG_DWORD,
            std::ptr::null_mut(),
            &mut apps_use_light_theme as *mut u32 as *mut _,
            &mut size,
        ) == 0 && apps_use_light_theme == 0
    }
}

/// ウィンドウ復帰用コールバックを登録
pub fn register_window_handle<F>(restore_fn: F)
where
    F: Fn() + Send + Sync + 'static,
{
    let mut guard = WINDOW_RESTORE_FN.lock().unwrap();
    *guard = Some(Box::new(restore_fn));
}

/// 二重起動を防止し、すでに起動中（タスクトレイ常駐含む）の場合は既存ウィンドウを復元・前面化して false を返す
pub fn ensure_single_instance() -> bool {
    let mutex_name: Vec<u16> = "Local\\KCO_Relay_SingleInstance_Mutex\0"
        .encode_utf16()
        .collect();

    unsafe {
        let handle = CreateMutexW(std::ptr::null(), 1, mutex_name.as_ptr());
        if handle == 0 {
            if GetLastError() == ERROR_ALREADY_EXISTS {
                show_main_window();
                return false;
            }
            return true;
        }

        if GetLastError() == ERROR_ALREADY_EXISTS {
            // すでに別プロセスが存在（タスクトレイ常駐中含む）するため、既存ウィンドウを復帰・前面化
            show_main_window();
            return false;
        }

        // プロセス生存中ハンドルを保持
        let mut guard = SINGLE_INSTANCE_MUTEX.lock().unwrap();
        *guard = Some(handle);
    }
    true
}

unsafe extern "system" fn enum_windows_proc(hwnd: HWND, lparam: LPARAM) -> BOOL {
    let target = &mut *(lparam as *mut Option<HWND>);
    let mut buffer = [0u16; 256];
    let len = GetWindowTextW(hwnd, buffer.as_mut_ptr(), 256);
    if len > 0 {
        let text = String::from_utf16_lossy(&buffer[..len as usize]);
        if text == WINDOW_TITLE {
            *target = Some(hwnd);
            return 0; // 一致したので列挙終了
        }
    }
    1 // 継続
}

/// ウィンドウタイトルから HWND を取得（非表示・トレイ常駐中も確実に検出）
fn find_main_window() -> HWND {
    let title: Vec<u16> = format!("{}\0", WINDOW_TITLE).encode_utf16().collect();
    let hwnd = unsafe { FindWindowW(std::ptr::null(), title.as_ptr()) };
    if hwnd != 0 {
        return hwnd;
    }

    // FindWindowW で見つからない場合は EnumWindows で全探索
    let mut found: Option<HWND> = None;
    unsafe {
        EnumWindows(
            Some(enum_windows_proc),
            &mut found as *mut _ as LPARAM,
        );
    }
    found.unwrap_or(0)
}

/// DWM の角丸ヒントに依存せず、実際に描画できるウィンドウ領域を指定する。
fn update_main_window_region(hwnd: HWND) -> Result<(), String> {
    UPDATING_WINDOW_REGION.with(|updating| {
        if updating.replace(true) {
            return Ok(());
        }
        let result = (|| unsafe {
            if IsIconic(hwnd) != 0 {
                return Ok(());
            }
            if IsZoomed(hwnd) != 0 {
                if SetWindowRgn(hwnd, 0, 1) == 0 {
                    return Err("最大化時のウィンドウ領域の解除に失敗".into());
                }
                return Ok(());
            }

            let mut rect = RECT { left: 0, top: 0, right: 0, bottom: 0 };
            if GetWindowRect(hwnd, &mut rect) == 0 {
                return Err("角丸設定用のウィンドウサイズ取得に失敗".into());
            }
            let width = rect.right - rect.left;
            let height = rect.bottom - rect.top;
            if width <= 0 || height <= 0 {
                return Ok(());
            }
            // Slint 側の半径 8px に合わせ、論理ピクセルから物理ピクセルに変換する。
            let dpi = GetDpiForWindow(hwnd);
            let dpi = if dpi == 0 { 96 } else { dpi };
            let diameter = ((16 * dpi + 48) / 96) as i32;
            let region = CreateRoundRectRgn(0, 0, width, height, diameter, diameter);
            if region == 0 {
                return Err("角丸ウィンドウ領域の作成に失敗".into());
            }
            if SetWindowRgn(hwnd, region, 1) == 0 {
                // 成功時は OS に所有権が移る。失敗時だけここで解放する。
                DeleteObject(region);
                return Err("角丸ウィンドウ領域の設定に失敗".into());
            }
            Ok(())
        })();
        updating.set(false);
        result
    })
}

unsafe extern "system" fn window_corners_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    subclass_id: usize,
    _reference_data: usize,
) -> LRESULT {
    // 本体の枠とタイトルバーは Slint が描画する。角丸領域を設定した
    // ウィンドウに標準の非クライアント描画が走ると、白い枠が重なる。
    if message == WM_NCPAINT {
        return 0;
    }
    if message == WM_NCACTIVATE {
        // winit にアクティブ状態の変更を通知しつつ、DefWindowProc による
        // 標準枠の再描画だけを lParam = -1 で抑止する。
        return DefSubclassProc(hwnd, message, wparam, -1);
    }

    if message == WM_NCDESTROY {
        RemoveWindowSubclass(hwnd, Some(window_corners_proc), subclass_id);
        return DefSubclassProc(hwnd, message, wparam, lparam);
    }

    // Slint / winit の処理後に、新しいサイズ・DPI・最大化状態を取得する。
    let result = DefSubclassProc(hwnd, message, wparam, lparam);
    if message == WM_SIZE || message == WM_DPICHANGED {
        if let Err(error) = update_main_window_region(hwnd) {
            crate::append_log_to_file(&error);
        }
    }
    result
}

fn native_window_handle(window: &slint::Window) -> Result<Option<HWND>, String> {
    let native_window = window.window_handle();
    let handle = match native_window.window_handle() {
        Ok(handle) => handle,
        Err(HandleError::Unavailable) => return Ok(None),
        Err(error) => return Err(format!("ウィンドウ取得に失敗: {error}")),
    };
    let hwnd = match handle.as_raw() {
        RawWindowHandle::Win32(handle) => handle.hwnd.get(),
        _ => return Err("対象が Win32 ウィンドウではありません".into()),
    };
    Ok(Some(hwnd))
}

/// UI スレッドから呼ぶ。ネイティブウィンドウの生成待ちは Ok(false) を返す。
pub fn configure_main_window_corners(window: &slint::Window) -> Result<bool, String> {
    let Some(hwnd) = native_window_handle(window)? else { return Ok(false); };

    if unsafe { SetWindowSubclass(hwnd, Some(window_corners_proc), CORNER_SUBCLASS_ID, 0) } == 0 {
        return Err("ウィンドウの角丸更新処理の登録に失敗".into());
    }
    update_main_window_region(hwnd)?;
    Ok(true)
}

/// メインウィンドウを非表示にし、Slintのイベントループを維持したままタスクトレイに常駐
pub fn hide_to_tray(_window: &slint::Window) {
    let hwnd = find_main_window();
    if hwnd != 0 {
        unsafe {
            ShowWindow(hwnd, SW_HIDE);
        }
    }
}

/// タスクトレイまたは二重起動検知時にメインウィンドウを表示・前面化
pub fn show_main_window() {
    let hwnd = find_main_window();
    if hwnd != 0 {
        unsafe {
            ShowWindow(hwnd, SW_SHOW);
            ShowWindow(hwnd, SW_RESTORE);
            SetForegroundWindow(hwnd);
        }
    }
    let guard = WINDOW_RESTORE_FN.lock().unwrap();
    if let Some(ref f) = *guard {
        f();
    }
}



/// タスクトレイの初期化
pub struct TrayHandle {
    _tray: TrayItem,
}

pub fn setup_tray<FShow, FQuit>(on_show: FShow, on_quit: FQuit) -> Result<TrayHandle, Box<dyn std::error::Error>>
where
    FShow: Fn() + Send + Sync + 'static,
    FQuit: Fn() + Send + Sync + 'static,
{
    let mut tray = TrayItem::new(
        "KCO Relay Server",
        tray_item::IconSource::Resource("tray-default"),
    )?;

    tray.add_menu_item("表示 (Show)", on_show)?;
    tray.add_menu_item("終了 (Exit)", on_quit)?;

    Ok(TrayHandle { _tray: tray })
}
