import os
import time
from datetime import datetime
from .config import LOG_DIR, IMAGE_DIR, MAX_LOG_FILES

class LogManager:
    _bgm_log_path = None
    _senka_log_path = None
    _log_callback = None

    @staticmethod
    def init():
        """ログディレクトリとファイルを初期化し、ローテーションを実行"""
        try:
            os.makedirs(LOG_DIR, exist_ok=True)
            os.makedirs(IMAGE_DIR, exist_ok=True)
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            LogManager._bgm_log_path = os.path.join(LOG_DIR, f'bgm_log_{timestamp}.txt')
            LogManager._senka_log_path = os.path.join(LOG_DIR, f'senka_log_{timestamp}.txt')
            
            # ファイル作成（空ファイル）
            open(LogManager._bgm_log_path, 'w', encoding='utf-8').close()
            open(LogManager._senka_log_path, 'w', encoding='utf-8').close()
            
            # 既存のローテーション (Countベース)
            LogManager.rotate_logs('bgm_log')
            LogManager.rotate_logs('senka_log')
            
            log(f"ログファイル作成: {LogManager._bgm_log_path}, {LogManager._senka_log_path}")
            
        except Exception as e:
            log(f"ログ初期化エラー: {e}")

    @staticmethod
    def rotate_logs(prefix):
        """指定されたプレフィックスのログファイルを最大数に制限"""
        try:
            files = [f for f in os.listdir(LOG_DIR) if f.startswith(prefix) and f.endswith('.txt')]
            files.sort() # 名前順≒古い順
            
            while len(files) > MAX_LOG_FILES:
                target = files.pop(0)
                try:
                    os.remove(os.path.join(LOG_DIR, target))
                    log(f"古いログを削除: {target}")
                except Exception as e:
                    log(f"ログ削除エラー({target}): {e}")
        except Exception as e:
            log(f"ログローテーションエラー: {e}")

    @staticmethod
    def cleanup_by_days(days, directory=LOG_DIR, extensions=None):
        """指定した日数より古いファイルを削除"""
        if days < 1:
            return
            
        try:
            cutoff_time = time.time() - (days * 24 * 60 * 60)
            count = 0
            
            if not os.path.exists(directory):
                return

            for f in os.listdir(directory):
                f_path = os.path.join(directory, f)
                if not os.path.isfile(f_path):
                    continue
                    
                if extensions:
                    if not any(f.endswith(ext) for ext in extensions):
                        continue
                
                # 更新日時チェック
                try:
                    mtime = os.path.getmtime(f_path)
                    if mtime < cutoff_time:
                        os.remove(f_path)
                        count += 1
                except Exception:
                    pass
            
            if count > 0:
                dir_name = os.path.basename(directory)
                log(f"{dir_name}クリーンアップ: {days}日以前のファイル {count}件を削除しました")
                
        except Exception as e:
            log(f"ファイルクリーンアップエラー: {e}")

    @staticmethod
    def cleanup_logs(days):
        LogManager.cleanup_by_days(days, LOG_DIR, ['.txt', '.log'])

    @staticmethod
    def cleanup_images(days):
        LogManager.cleanup_by_days(days, IMAGE_DIR, ['.png', '.jpg', '.jpeg'])

    @staticmethod
    def save_image(image, prefix="capture"):
        """画像を保存"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')[:-3]
            filename = f"{prefix}_{timestamp}.png"
            path = os.path.join(IMAGE_DIR, filename)
            image.save(path)
            # log(f"画像保存: {filename}")
            return path
        except Exception as e:
            log(f"画像保存エラー: {e}")
            return None

    @staticmethod
    def write_bgm(title):
        """BGMログ書き込み"""
        if not LogManager._bgm_log_path or not title:
            return
        try:
            ts_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
            with open(LogManager._bgm_log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{ts_str}] {title}\n")
        except Exception as e:
            log(f"BGMログ書き込みエラー: {e}")

    @staticmethod
    def write_senka(senka):
        """戦果ログ書き込み"""
        if not LogManager._senka_log_path or senka is None or senka == "":
            return
        try:
            ts_str = datetime.now().strftime('%Y/%m/%d %H:%M:%S')
            with open(LogManager._senka_log_path, 'a', encoding='utf-8') as f:
                f.write(f"[{ts_str}] {senka}\n")
        except Exception as e:
            log(f"戦果ログ書き込みエラー: {e}")

    @staticmethod
    def set_gui_callback(callback):
        """GUIにログを表示するためのコールバックを設定"""
        LogManager._log_callback = callback

def log(message):
    """コンソールとGUIの両方にログを出力するヘルパー関数"""
    print(message)
    if LogManager._log_callback:
        try:
            LogManager._log_callback(message)
        except Exception:
            pass
