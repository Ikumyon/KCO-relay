from ctypes import wintypes
import ctypes
from PIL import Image
from .logger import log

class Win32WindowCapture:
    # Constants
    PW_CLIENTONLY = 1
    PW_RENDERFULLCONTENT = 2
    
    # Structures
    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long)]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [('biSize', wintypes.DWORD),
                    ('biWidth', ctypes.c_long),
                    ('biHeight', ctypes.c_long),
                    ('biPlanes', wintypes.WORD),
                    ('biBitCount', wintypes.WORD),
                    ('biCompression', wintypes.DWORD),
                    ('biSizeImage', wintypes.DWORD),
                    ('biXPelsPerMeter', ctypes.c_long),
                    ('biYPelsPerMeter', ctypes.c_long),
                    ('biClrUsed', wintypes.DWORD),
                    ('biClrImportant', wintypes.DWORD)]

    @staticmethod
    def get_window_at_point(x, y):
        """指定座標にあるウィンドウハンドルを取得"""
        try:
            point = wintypes.POINT(x, y)
            hwnd = ctypes.windll.user32.WindowFromPoint(point)
            return hwnd
        except Exception as e:
            log(f"ウィンドウ取得エラー: {e}")
            return None

    @staticmethod
    def find_window_by_title(title_query):
        """タイトルに指定文字列を含むウィンドウを検索して返す (最初に見つかったもの)"""
        result_hwnd = None
        
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        
        def enum_windows_callback(hwnd, lParam):
            nonlocal result_hwnd
            if Win32WindowCapture.get_window_title(hwnd) == title_query:
                result_hwnd = hwnd
                return False # Stop enumeration
            return True

        ctypes.windll.user32.EnumWindows(WNDENUMPROC(enum_windows_callback), 0)
        return result_hwnd

    @staticmethod
    def get_window_title(hwnd):
        """ウィンドウタイトルを取得"""
        try:
            if not hwnd:
                return "Unknown"
            length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
            return buff.value
        except Exception:
            return "Error"

    @staticmethod
    def get_window_rect(hwnd):
        """ウィンドウの矩形を取得 (left, top, right, bottom)"""
        try:
            rect = Win32WindowCapture.RECT()
            ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
            return (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:
            return None

    @staticmethod
    def capture_window(hwnd):
        """指定ウィンドウをPrintWindowでキャプチャしPIL Imageで返す"""
        hwndDC = None
        mfcDC = None
        saveDC = None
        oldBitMap = None
        
        try:
            # ウィンドウRect取得
            rect = Win32WindowCapture.get_window_rect(hwnd)
            if not rect:
                log("ウィンドウ矩形取得失敗")
                return None
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            
            if width <= 0 or height <= 0:
                log(f"無効なウィンドウサイズ: {width}x{height}")
                return None

            # デバイスコンテキスト作成
            hwndDC = ctypes.windll.user32.GetWindowDC(hwnd)
            mfcDC  = ctypes.windll.gdi32.CreateCompatibleDC(hwndDC)
            saveDC = ctypes.windll.gdi32.CreateCompatibleBitmap(hwndDC, width, height)
            
            if not saveDC or not mfcDC:
                log("GDIオブジェクト作成失敗")
                return None
            
            # SelectObjectは元のオブジェクトを返すので保存しておく
            oldBitMap = ctypes.windll.gdi32.SelectObject(mfcDC, saveDC)
            
            # PrintWindow実行
            # まず PW_RENDERFULLCONTENT (2) を試す (Win8.1+)
            result = ctypes.windll.user32.PrintWindow(hwnd, mfcDC, Win32WindowCapture.PW_RENDERFULLCONTENT)
            
            # 失敗した場合は flag 0 でリトライ
            if result == 0:
                # log("PrintWindow(flag=2) failed, retrying with flag=0")
                result = ctypes.windll.user32.PrintWindow(hwnd, mfcDC, 0)
            
            if result == 0:
                log("PrintWindow failed (both flags)")
                return None

            # ビットマップ取得
            bmpinfo = Win32WindowCapture.BITMAPINFOHEADER()
            bmpinfo.biSize = ctypes.sizeof(Win32WindowCapture.BITMAPINFOHEADER)
            bmpinfo.biWidth = width
            bmpinfo.biHeight = -height # Top-down
            bmpinfo.biPlanes = 1
            bmpinfo.biBitCount = 32
            bmpinfo.biCompression = 0
            
            buffer_len = height * width * 4
            buffer = ctypes.create_string_buffer(buffer_len)
            
            # 戻り値チェック (0なら失敗)
            lines = ctypes.windll.gdi32.GetDIBits(mfcDC, saveDC, 0, height, ctypes.byref(buffer), ctypes.byref(bmpinfo), 0)
            if lines == 0:
                log("GetDIBits failed")
                return None
            
            # PIL Image変換
            image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
            
            return image
            
        except Exception as e:
            log(f"ウィンドウキャプチャエラー: {e}")
            return None
        finally:
            # クリーンアップ (作成した逆順で)
            if mfcDC and oldBitMap:
                ctypes.windll.gdi32.SelectObject(mfcDC, oldBitMap)
            if saveDC:
                ctypes.windll.gdi32.DeleteObject(saveDC)
            if mfcDC:
                ctypes.windll.gdi32.DeleteDC(mfcDC)
            if hwndDC:
                ctypes.windll.user32.ReleaseDC(hwnd, hwndDC)

# 高DPI設定
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass
