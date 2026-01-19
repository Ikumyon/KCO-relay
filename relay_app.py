import tkinter as tk
from tkinter import ttk, scrolledtext
import threading
import sys
import os
import time
from datetime import datetime
from PIL import Image, ImageGrab, ImageDraw, ImageChops
import pystray
import ctypes

# Coreモジュールのインポート
from core.config import INPUT_PORT, OUTPUT_PORT, SESSION_ID
from core.logger import LogManager, log
from core.state import shared_state
from core.database import Database
from core.capture import Win32WindowCapture
from core.ocr import OCRProcessor
from core.gui import ROISelector
from core.server import ThreadedHTTPServer, InputHandler, OutputHandler, set_gui_update_callback
from core.settings import SettingsManager

def get_resource_path(relative_path):
    """PyInstallerでビルドしたexeでも正しく動作するリソースパス取得関数"""
    try:
        # PyInstallerが作成する一時フォルダ
        base_path = sys._MEIPASS
    except AttributeError:
        # 開発環境では通常のパス
        base_path = os.path.dirname(__file__)
    
    return os.path.join(base_path, relative_path)

class App:
    instance = None

    def __init__(self, root):
        App.instance = self
        self.root = root
        self.root.title("KCO Relay Server")
        self.root.geometry("500x600")
        
        # アイコン設定
        try:
            icon_path = get_resource_path(os.path.join("icons", "icon.ico"))
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception as e:
            print(f"Icon load error: {e}")
        
        self.root.protocol('WM_DELETE_WINDOW', self.minimize_to_tray)

        self.setup_ui()
        self.running = True
        
        # OCR関連の状態
        self.roi_coords = None      # 監視範囲 (x1, y1, x2, y2)
        self.target_hwnd = None     # 監視対象ウィンドウハンドル
        self.last_roi_image = None  # 前回取得した画像
        self.monitoring = False     # 監視中フラグ
        self.last_ocr_time = 0      # 最終OCR実行時刻
        
        # OCRプロセッサーの初期化
        self.ocr_processor = OCRProcessor()
        self.ocr_thread = None # バックグラウンドスレッド管理用
        
        # ログ管理初期化 (GUIコールバック登録)
        LogManager.init()
        LogManager.set_gui_callback(self.add_log)

        # データベース初期化
        Database.init_db()
        
        # グローバル状態の初期化 (DBから復元)
        initial_db_state = Database.get_state()
        if initial_db_state:
            shared_state.update(initial_db_state)
            self.update_labels(initial_db_state)
        
        # 前回終了時のROI/ウィンドウ状態を復元
        self.restore_roi_settings()
        
        # サーバーからのGUI更新通知を受け取る
        # サーバーからのGUI更新通知を受け取る
        set_gui_update_callback(self.update_gui_from_thread)

        # クリーンアップ実行 (起動時)
        self.perform_cleanup()

        # サーバー起動 (Input: 5000)
        self.input_server_thread = threading.Thread(target=self.run_input_server)
        self.input_server_thread.daemon = True
        self.input_server_thread.start()

        # サーバー起動 (Output: 5001)
        self.output_server_thread = threading.Thread(target=self.run_output_server)
        self.output_server_thread.daemon = True
        self.output_server_thread.start()

        # 監視ループ開始
        self.root.after(1000, self.monitor_loop)

    def configure_styles(self):
        """スタイル設定 (システムフォント使用)"""
        style = ttk.Style()
        
        # ラベルのスタイル
        style.configure("Dim.TLabel", foreground="#666666", font=("TkDefaultFont", 8))
        style.configure("Bold.TLabel", font=("TkDefaultFont", 9, "bold"))
        
        # ラベルフレームのタイトルを目立たせる (Windowsのテーマによっては効かない場合もあるが設定しておく)
        style.configure("TLabelframe.Label", font=("TkDefaultFont", 9, "bold"), foreground="#333333")

    def setup_ui(self):
        self.configure_styles()
        
        main_frame = ttk.Frame(self.root, padding="15")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # --- 1. Server Status ---
        status_frame = ttk.LabelFrame(main_frame, text="サーバー状態 (Server Status)", padding="10")
        status_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Grid layout for status
        self.lbl_status_in = ttk.Label(status_frame, text=f"Input: Port {INPUT_PORT} (待機中)", foreground="orange")
        self.lbl_status_in.grid(row=0, column=0, sticky=tk.W, padx=(0, 20))
        
        self.lbl_status_out = ttk.Label(status_frame, text=f"Output: Port {OUTPUT_PORT} (待機中)", foreground="orange")
        self.lbl_status_out.grid(row=0, column=1, sticky=tk.W)

        # --- 2. OCR Settings ---
        ocr_frame = ttk.LabelFrame(main_frame, text="OCR監視設定 (OCR Settings)", padding="10")
        ocr_frame.pack(fill=tk.X, pady=(0, 10))
        
        # Buttons Row
        btn_frame = ttk.Frame(ocr_frame)
        btn_frame.pack(fill=tk.X, pady=(0, 10))
        
        self.btn_select_roi = ttk.Button(btn_frame, text="監視範囲設定", command=self.start_roi_selection)
        self.btn_select_roi.pack(side=tk.LEFT, padx=(0, 5))

        self.btn_stop_monitor = ttk.Button(btn_frame, text="監視停止", command=self.stop_monitoring)
        self.btn_stop_monitor.pack(side=tk.LEFT, padx=(0, 5))

        self.btn_settings = ttk.Button(btn_frame, text="設定", command=self.open_settings)
        self.btn_settings.pack(side=tk.LEFT, padx=(0, 5))
        
        # Info inside OCR frame
        info_sub_frame = ttk.Frame(ocr_frame)
        info_sub_frame.pack(fill=tk.X)

        self.lbl_roi_status = ttk.Label(info_sub_frame, text="範囲未設定", foreground="gray")
        self.lbl_roi_status.pack(side=tk.LEFT, padx=(0, 15))
        
        self.lbl_target_window = ttk.Label(info_sub_frame, text="ターゲット: -", foreground="#0066cc")
        self.lbl_target_window.pack(side=tk.LEFT)
        
        # Result Row
        self.lbl_ocr_result = ttk.Label(ocr_frame, text="OCR待機中...", foreground="gray")
        self.lbl_ocr_result.pack(fill=tk.X, pady=(10, 0))

        # --- 3. Status Info ---
        info_frame = ttk.LabelFrame(main_frame, text="現在の情報 (Current Info)", padding="10")
        info_frame.pack(fill=tk.X, pady=(0, 10))

        # Grid system for info
        info_frame.columnconfigure(1, weight=1)
        info_frame.columnconfigure(3, weight=1)

        ttk.Label(info_frame, text="スキーマ:", style="Dim.TLabel").grid(row=0, column=0, sticky=tk.W)
        self.lbl_schema = ttk.Label(info_frame, text="-")
        self.lbl_schema.grid(row=0, column=1, sticky=tk.W, padx=(5, 10))

        ttk.Label(info_frame, text="更新時刻:", style="Dim.TLabel").grid(row=0, column=2, sticky=tk.W)
        self.lbl_updated = ttk.Label(info_frame, text="-")
        self.lbl_updated.grid(row=0, column=3, sticky=tk.W, padx=5)
        
        ttk.Label(info_frame, text="TS:", style="Dim.TLabel").grid(row=1, column=0, sticky=tk.W)
        self.lbl_ts = ttk.Label(info_frame, text="-")
        self.lbl_ts.grid(row=1, column=1, sticky=tk.W, padx=(5, 10))

        # Separator
        ttk.Separator(info_frame, orient=tk.HORIZONTAL).grid(row=2, column=0, columnspan=4, sticky="ew", pady=8)

        # Title
        ttk.Label(info_frame, text="タイトル:", style="Dim.TLabel").grid(row=3, column=0, sticky=tk.NW, pady=2)
        self.lbl_title = tk.Text(info_frame, height=2, width=30, state='disabled', wrap=tk.WORD, 
                                 relief="flat", bg="#f4f4f4", font=("TkDefaultFont", 9))
        self.lbl_title.grid(row=3, column=1, columnspan=3, sticky="ew", padx=5, pady=2)

        # Senka
        ttk.Label(info_frame, text="戦果:", style="Dim.TLabel").grid(row=4, column=0, sticky=tk.W, pady=2)
        self.lbl_senka = tk.Text(info_frame, height=1, width=30, state='disabled', wrap=tk.NONE, 
                                 relief="flat", bg="#f4f4f4", font=("TkDefaultFont", 9))
        self.lbl_senka.grid(row=4, column=1, columnspan=3, sticky="ew", padx=5, pady=2)

        # --- 4. Logs ---
        log_frame = ttk.LabelFrame(main_frame, text="ログ (Logs)", padding="10")
        log_frame.pack(fill=tk.BOTH, expand=True)
        
        self.log_area = scrolledtext.ScrolledText(log_frame, height=5, state='disabled', font=("Consolas", 9))
        self.log_area.pack(fill=tk.BOTH, expand=True)

        # Footer
        ttk.Label(main_frame, text="ウィンドウを閉じるとトレイに最小化されます", style="Dim.TLabel").pack(side=tk.BOTTOM, pady=(5, 0))

    def run_input_server(self):
        """Inputサーバー (5000) を実行"""
        try:
            with ThreadedHTTPServer(("127.0.0.1", INPUT_PORT), InputHandler) as httpd:
                self.input_httpd = httpd
                self.root.after(0, lambda: self.lbl_status_in.config(text=f"Input: Port {INPUT_PORT} (実行中)", foreground="green"))
                log(f"Inputサーバー開始: Port {INPUT_PORT}")
                httpd.serve_forever()
        except OSError as e:
            self.root.after(0, lambda: self.lbl_status_in.config(text=f"Input: Port {INPUT_PORT} (エラー: {e})", foreground="red"))
            log(f"Inputサーバー起動失敗: {e}")

    def run_output_server(self):
        """Outputサーバー (5001) を実行"""
        try:
            with ThreadedHTTPServer(("127.0.0.1", OUTPUT_PORT), OutputHandler) as httpd:
                self.output_httpd = httpd
                self.root.after(0, lambda: self.lbl_status_out.config(text=f"Output: Port {OUTPUT_PORT} (実行中)", foreground="green"))
                log(f"Outputサーバー開始: Port {OUTPUT_PORT}")
                httpd.serve_forever()
        except OSError as e:
            self.root.after(0, lambda: self.lbl_status_out.config(text=f"Output: Port {OUTPUT_PORT} (エラー: {e})", foreground="red"))
            log(f"Outputサーバー起動失敗: {e}")

    def update_gui_from_thread(self, data):
        """バックグラウンドスレッドからGUI更新をスケジュールします。"""
        self.root.after(0, lambda: self.update_labels(data))

    def update_labels(self, data):
        """UIラベルを更新します。"""
        ts_val = data.get('ts', 0)
        ts_str = "-"
        if isinstance(ts_val, (int, float)) and ts_val > 0:
            try:
                ts_str = datetime.fromtimestamp(ts_val / 1000).strftime('%Y/%m/%d %H:%M:%S')
            except Exception:
                ts_str = str(ts_val)
        self.lbl_ts.config(text=ts_str)
        
        updated_ts = data.get('updated_at', 0)
        dt_str = "-"
        if isinstance(updated_ts, (int, float)) and updated_ts > 0:
            try:
                dt_str = datetime.fromtimestamp(updated_ts / 1000).strftime('%Y/%m/%d %H:%M:%S')
            except Exception:
                dt_str = str(updated_ts)
        self.lbl_updated.config(text=dt_str)

        self.lbl_title.config(state='normal')
        self.lbl_title.delete(1.0, tk.END)
        self.lbl_title.insert(tk.END, data.get('title', ''))
        self.lbl_title.config(state='disabled')

        self.lbl_senka.config(state='normal')
        self.lbl_senka.delete(1.0, tk.END)
        self.lbl_senka.insert(tk.END, data.get('senka', ''))
        self.lbl_senka.config(state='disabled')

    def add_log(self, message):
        """ログエリアにメッセージを追加します（スレッドセーフ）。"""
        def _append():
            timestamp = datetime.now().strftime("%H:%M:%S")
            log_msg = f"[{timestamp}] {message}\n"
            self.log_area.config(state='normal')
            self.log_area.insert(tk.END, log_msg)
            self.log_area.see(tk.END)
            self.log_area.config(state='disabled')
        
        self.root.after(0, _append)

    # create_icon_image は削除


    def minimize_to_tray(self):
        """ウィンドウを隠し、トレイアイコンを表示します。"""
        self.root.withdraw()
        
        image = None
        try:
            icon_path = get_resource_path(os.path.join("icons", "icon.ico"))
            if os.path.exists(icon_path):
                image = Image.open(icon_path)
        except Exception as e:
            log(f"Tray icon load error: {e}")
            
        if image is None:
             # フォールバック: 白紙に緑の矩形
            width = 64
            height = 64
            image = Image.new('RGB', (width, height), (255, 255, 255))
            dc = ImageDraw.Draw(image)
            dc.rectangle(
                (width // 2 - 10, height // 2 - 10, width // 2 + 10, height // 2 + 10),
                fill=(0, 128, 0))
        
        menu = pystray.Menu(
            pystray.MenuItem('表示 (Show)', self.show_window),
            pystray.MenuItem('終了 (Exit)', self.quit_app)
        )
        self.icon = pystray.Icon("KCO Relay", image, "KCO Relay Server", menu)
        threading.Thread(target=self.icon.run, daemon=True).start()

    def show_window(self, icon, item):
        """トレイからウィンドウを復元します。"""
        self.icon.stop()
        self.root.after(0, self.root.deiconify)

    def quit_app(self, icon=None, item=None):
        """アプリケーションを終了します。"""
        if hasattr(self, 'icon'):
            self.icon.stop()
        if hasattr(self, 'input_httpd'):
            self.input_httpd.shutdown()
        if hasattr(self, 'output_httpd'):
            self.output_httpd.shutdown()
        
        # Executor停止 (OCR Processor経由)
        if hasattr(self, 'ocr_processor'):
            self.ocr_processor.shutdown()

        self.root.quit()
        sys.exit(0)

    # --- OCR関連メソッド ---
    def start_roi_selection(self):
        """範囲選択モードを開始"""
        ROISelector(self.root, self.on_roi_selected)

    def on_roi_selected(self, coords):
        """範囲選択完了時のコールバック"""
        self.roi_coords = coords
        self.lbl_roi_status.config(text=f"範囲設定済: {coords}", foreground="green")
        log(f"監視範囲を設定しました: {coords}")
        
        # ターゲットウィンドウ特定
        try:
            hwnd = Win32WindowCapture.get_window_at_point(coords[0], coords[1])
            if hwnd:
                self.target_hwnd = hwnd
                title = Win32WindowCapture.get_window_title(hwnd)
                self.lbl_target_window.config(text=f"ターゲット: {title}")
                log(f"ターゲットウィンドウ設定: '{title}' (HWND: {hwnd})")
            else:
                self.target_hwnd = None
                self.lbl_target_window.config(text="ターゲット: 不明")
        except Exception as e:
            log(f"ターゲットウィンドウ特定失敗: {e}")
            self.lbl_target_window.config(text="ターゲット: エラー")
            self.target_hwnd = None
        
        # 設定保存
        try:
            current_settings = SettingsManager.load_settings()
            current_settings['last_roi_coords'] = coords
            
            # タイトルは取得できている場合のみ保存（Noneなら保存しない、あるいはNoneを保存）
            # ここではターゲットが見つかった場合のみそのタイトルを保存
            if self.target_hwnd:
                title = Win32WindowCapture.get_window_title(self.target_hwnd)
                current_settings['last_target_window_title'] = title
            else:
                # ターゲットなしの場合はタイトルをクリアすべきか？
                # 一応クリアしておく
                current_settings['last_target_window_title'] = None
                
            SettingsManager.save_settings(current_settings)
            log("ROI設定を保存しました")
        except Exception as e:
            log(f"ROI設定保存失敗: {e}")
        
        # 状態リセット
        self.last_roi_image = None
        self.monitoring = True
        self.update_monitoring_status()
        
        # 初回即時実行
        self.root.after(100, lambda: self.process_ocr_cycle(force=True))

    def stop_monitoring(self):
        """監視を停止"""
        self.monitoring = False
        self.update_monitoring_status()
        log("監視を停止しました")

    def update_monitoring_status(self):
        """監視状態に応じてUIを更新"""
        if self.monitoring:
            self.lbl_roi_status.config(text=f"監視中: {self.roi_coords}", foreground="green")
            self.btn_stop_monitor.state(['!disabled'])
            self.btn_select_roi.state(['disabled'])
        else:
            status_text = f"停止中: {self.roi_coords}" if self.roi_coords else "範囲未設定"
            self.lbl_roi_status.config(text=status_text, foreground="red" if self.roi_coords else "gray")
            self.btn_stop_monitor.state(['disabled'])
            self.btn_select_roi.state(['!disabled'])

    def monitor_loop(self):
        """1秒ごとの監視処理"""
        if not self.running:
            return

        try:
            # 監視有効 かつ 以前のスレッドが終了している場合のみ実行
            if self.monitoring and self.roi_coords:
                if self.ocr_thread is None or not self.ocr_thread.is_alive():
                    self.ocr_thread = threading.Thread(target=self.process_ocr_cycle, daemon=True)
                    self.ocr_thread.start()
        except Exception as e:
            log(f"監視ループエラー: {e}")
        
        # 1000ms後に再実行
        self.root.after(1000, self.monitor_loop)

    def process_ocr_cycle(self, force=False):
        """OCR監視の1サイクルを実行"""
        # 1. 画像取得
        try:
            current_image = None
            if self.roi_coords:
                x1, y1, x2, y2 = self.roi_coords
                target_hwnd = self.target_hwnd

                # HWNDチェック
                if target_hwnd and not ctypes.windll.user32.IsWindow(target_hwnd):
                    log("ターゲットウィンドウが無効です (閉じられた可能性があります)")
                    target_hwnd = None 

                # Fallback
                if not target_hwnd:
                    target_hwnd = Win32WindowCapture.get_window_at_point(x1, y1)

                if target_hwnd:
                    win_image = Win32WindowCapture.capture_window(target_hwnd)
                    if win_image:
                        win_rect = Win32WindowCapture.get_window_rect(target_hwnd)
                        if win_rect:
                            wx, wy, wr, wb = win_rect
                            if (wr - wx) > 0 and (wb - wy) > 0:
                                rel_x1 = x1 - wx
                                rel_y1 = y1 - wy
                                rel_x2 = x2 - wx
                                rel_y2 = y2 - wy
                                try:
                                    current_image = win_image.crop((rel_x1, rel_y1, rel_x2, rel_y2))
                                except Exception as e:
                                    log(f"Win32 Crop Error: {e}")

            # Fallback to ImageGrab
            if not current_image:
                current_image = ImageGrab.grab(bbox=self.roi_coords)
                
            current_image_gray = current_image.convert('L')
        except Exception as e:
            log(f"画面キャプチャ失敗 (Win32/ImageGrab): {e}")
            return

        # 2. 変化判定
        start_ocr = False
        
        if self.last_roi_image is None:
            self.last_roi_image = current_image_gray
            start_ocr = True
        else:
            diff = ImageChops.difference(self.last_roi_image, current_image_gray)
            if diff.getbbox():
                self.last_roi_image = current_image_gray
                start_ocr = True
            elif force:
                start_ocr = True
            elif time.time() - self.last_ocr_time > 3.0:
                 start_ocr = True

        if not start_ocr:
            return

        # 3. OCR実行 (OCRProcessorに委譲)
        status, value = self.ocr_processor.process_image(current_image_gray)
        self.last_ocr_time = time.time()

        # 4. 結果判定と更新
        if status == "confirmed":
            self.update_from_ocr(value, image=current_image)
        elif status == "waiting":
            self.root.after(0, lambda: self.lbl_ocr_result.config(text=f"OCR候補: '{value}' (確認中...)", foreground="darkorange"))
        elif status == "duplicate":
            self.root.after(0, lambda: self.lbl_ocr_result.config(text=f"OCR: '{value}' (変更なし)", foreground="gray"))
        elif status == "invalid":
            pass # ログには出ているのでUIはクリアしないか、警告を出すか
        elif status == "failed":
            self.root.after(0, lambda: self.lbl_ocr_result.config(text="OCR: 読み取り失敗", foreground="orange"))

    def update_from_ocr(self, text, image=None):
        """OCRで確定した値をシステムに反映 (戦果として)"""
        log(f"OCR確定値: {text}")
        self.root.after(0, lambda: self.lbl_ocr_result.config(text=f"検出: {text}", foreground="blue"))
        
        try:
            current_state = shared_state.get_current()
            new_data = {
                "schema": "nowplaying.v1",
                "ts": int(time.time() * 1000),
                "title": current_state.get('title', ''),
                "senka": text
            }
            
            is_changed = shared_state.update(new_data)
            
            if is_changed:
                LogManager.write_senka(text)
                success, updated_at = Database.update_state(new_data)
                Database.log_event(new_data, SESSION_ID)
                
                if success:
                    new_data['updated_at'] = updated_at
                    self.root.after(0, lambda: self.update_labels(new_data))
                    self.root.after(0, lambda: self.update_labels(new_data))
                    log(f"状態更新完了 (OCR/戦果): {text}")

            # 画像保存 (確定時)
            if image:
                LogManager.save_image(image, prefix="ocr_confirmed")
            
            
        except Exception as e:
            log(f"更新処理エラー: {e}")

    def perform_cleanup(self):
        """保存期間を過ぎたデータのクリーンアップを実行"""
        try:
            settings = SettingsManager.load_settings()
            
            img_days = settings.get('image_retention_days', 10)
            log_days = settings.get('log_retention_days', 10)
            db_days = settings.get('db_retention_days', 10)
            
            log(f"クリーンアップ開始 (Image:{img_days}日, Log:{log_days}日, DB:{db_days}日)")
            
            LogManager.cleanup_images(img_days)
            LogManager.cleanup_logs(log_days)
            Database.cleanup_old_events(db_days)
            
        except Exception as e:
            log(f"クリーンアップ初期化エラー: {e}")

    def open_settings(self):
        """設定ウィンドウを開く"""
        settings_win = tk.Toplevel(self.root)
        settings_win.title("設定 (Settings)")
        settings_win.geometry("400x300")
        
        frame = ttk.Frame(settings_win, padding="20")
        frame.pack(fill=tk.BOTH, expand=True)
        
        current_settings = SettingsManager.load_settings()
        
        # 変数
        var_img = tk.IntVar(value=current_settings.get('image_retention_days', 10))
        var_log = tk.IntVar(value=current_settings.get('log_retention_days', 10))
        var_db = tk.IntVar(value=current_settings.get('db_retention_days', 10))
        
        # UI作成ヘルパー
        def create_spinbox_row(parent, label, var, r):
            ttk.Label(parent, text=label).grid(row=r, column=0, sticky=tk.W, pady=5)
            
            # Spinbox
            spin = ttk.Spinbox(parent, from_=1, to=365, textvariable=var, width=10)
            spin.grid(row=r, column=1, sticky=tk.W, padx=10, pady=5)
            
            ttk.Label(parent, text="日").grid(row=r, column=2, sticky=tk.W, pady=5)
            return r + 1

        row = 0
        ttk.Label(frame, text="データ保存期間設定 (Data Retention)", font=("TkDefaultFont", 10, "bold")).grid(row=row, column=0, columnspan=3, sticky=tk.W, pady=(0, 15))
        row += 1
        
        row = create_spinbox_row(frame, "画像保存期間 (Images):", var_img, row)
        row = create_spinbox_row(frame, "ログ保存期間 (Logs):", var_log, row)
        row = create_spinbox_row(frame, "DBデータ保存期間 (Database):", var_db, row)
        
        def save():
            try:
                # バリデーション (簡単なチェック)
                v_img = int(var_img.get())
                v_log = int(var_log.get())
                v_db = int(var_db.get())
            except ValueError:
                # エラーハンドリング (今回はログ出力のみ)
                log("設定保存エラー: 無効な数値が入力されました")
                return

            new_settings = {
                "image_retention_days": v_img,
                "log_retention_days": v_log,
                "db_retention_days": v_db
            }
            SettingsManager.save_settings(new_settings)
            
            # 即時反映のためにクリーンアップ再実行してもよいが、重いのでログだけ出す
            log("設定を保存しました。次回の起動時またはクリーンアップ時に反映されます。")
            settings_win.destroy()
            
            
        ttk.Button(frame, text="保存 (Save)", command=save).grid(row=row, column=0, columnspan=2, pady=20)

    def restore_roi_settings(self):
        """前回のROIとターゲットウィンドウ設定を復元"""
        try:
            settings = SettingsManager.load_settings()
            last_coords = settings.get('last_roi_coords')
            last_title = settings.get('last_target_window_title')
            
            if not last_coords:
                return

            log(f"前回設定を復元中... ROI: {last_coords}, Title: {last_title}")
            
            # ROI復元
            self.roi_coords = last_coords
            self.lbl_roi_status.config(text=f"範囲設定済(復元): {last_coords}", foreground="green")
            
            # ウィンドウ復元 (タイトルから検索)
            found_hwnd = None
            if last_title:
                found_hwnd = Win32WindowCapture.find_window_by_title(last_title)
            
            if found_hwnd:
                self.target_hwnd = found_hwnd
                self.lbl_target_window.config(text=f"ターゲット(復元): {last_title}")
                log(f"ターゲットウィンドウを復元しました: '{last_title}' (HWND: {found_hwnd})")
            else:
                if last_title:
                    log(f"前回のターゲットウィンドウが見つかりませんでした: '{last_title}'")
                    self.lbl_target_window.config(text=f"ターゲット未検出: {last_title}", foreground="orange")
                else:
                    self.lbl_target_window.config(text="ターゲット: なし")
            
            # 監視再開可能な状態へ (自動スタートはせず、準備完了状態にする)
            # ユーザーが「監視範囲設定」ボタンを押さなくてもいいようにする
            # ただし、ウィンドウが見つかっていない場合は注意が必要
            
            # 自動で監視開始してもよいが、安全のため手動開始待ち、あるいは「範囲設定済」状態にしておく
            self.btn_stop_monitor.state(['disabled'])
            self.btn_select_roi.state(['!disabled'])
            
            # もし完全に復元できたなら自動スタートしたい場合はここをコメントアウト解除
            # if found_hwnd:
            #     self.monitoring = True
            #     self.update_monitoring_status()
            #     self.root.after(100, lambda: self.process_ocr_cycle(force=True))

        except Exception as e:
            log(f"設定復元エラー: {e}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == '--test-run':
        print("テスト実行モード (Test run mode)")
        db_valid = False
        try:
            # テスト時は簡易チェックのみ
            # Databaseなどの依存関係が解決されているか確認
            Database.init_db()
            print("DB初期化: OK")
            db_valid = True
        except Exception as e:
            print(f"DBチェック失敗: {e}")
            
        sys.exit(0 if db_valid else 1)
        
    root = tk.Tk()
    app = App(root)
    root.mainloop()
