import http.server
import socketserver
import json
import time
from .state import shared_state
from .logger import log, LogManager
from .database import Database
from .config import SESSION_ID

# GUI更新通知用のコールバック
_gui_update_callback = None

def set_gui_update_callback(callback):
    """メインスレッド(GUI)への更新通知用コールバックを設定"""
    global _gui_update_callback
    _gui_update_callback = callback

class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

# --- 入力サーバー (Port 5000) ---
class InputHandler(http.server.BaseHTTPRequestHandler):
    
    def do_POST(self):
        if self.path == '/update':
            try:
                content_length = int(self.headers.get('Content-Length', 0))
                post_data = self.rfile.read(content_length)
                data = json.loads(post_data.decode('utf-8'))
                
                # バリデーション
                if 'schema' not in data:
                    data['schema'] = 'nowplaying.v1'
                
                if 'ts' not in data:
                    data['ts'] = int(time.time() * 1000)
                else:
                     try:
                        data['ts'] = int(data['ts'])
                     except ValueError:
                         raise ValueError("tsは整数である必要があります")

                 # バリデーション (titleかsenkaがあればOKとする)
                if 'title' not in data and 'senka' not in data:
                    raise ValueError("タイトルまたは戦果が必要です")
                    
                # 補完
                current_state = shared_state.get_current()
                if 'title' not in data:
                    data['title'] = current_state.get('title', '')
                if 'senka' not in data:
                    data['senka'] = current_state.get('senka', '')

                # 1. 共有メモリ更新
                is_changed = shared_state.update(data)

                if is_changed:
                    # ログファイル出力
                    if data.get('title') != current_state.get('title'):
                        LogManager.write_bgm(data['title'])
                        log(f"タイトル更新: {data['title']}")
                    
                    if str(data.get('senka')) != str(current_state.get('senka')):
                        LogManager.write_senka(data['senka'])
                        log(f"戦果更新: {data['senka']}")

                    # 2. DB更新 (現在の状態)
                    success, updated_at = Database.update_state(data)
                    
                    # 3. イベント記録 (履歴)
                    Database.log_event(data, SESSION_ID)
                    
                    if success:
                        # GUIへの通知
                        if _gui_update_callback:
                            data['updated_at'] = updated_at
                            _gui_update_callback(data)

                # 変更がなくても 200 OK は返す
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')

            except json.JSONDecodeError:
                self._send_error(400, "Invalid JSON")
            except ValueError as e:
                self._send_error(400, str(e))
            except Exception as e:
                log(f"サーバーエラー: {e}")
                self._send_error(500, str(e))
        else:
            self.send_response(404)
            self.end_headers()
            
    def _send_error(self, code, message):
        self.send_response(code)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}).encode('utf-8'))

    def log_message(self, format, *args):
        pass

# --- 出力サーバー (Port 5001) ---
class OutputHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/sse':
            self.handle_sse()
        else:
            self.send_response(404)
            self.end_headers()

    def handle_sse(self):
        """SSEストリームを処理します。"""
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'keep-alive')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

        log(f"SSE接続開始: {self.client_address}")

        try:
            # 初回送信: 現在値があれば送る
            current = shared_state.get_current()
            if current.get('title'):
                self.send_sse_event(current)

            # 更新ループ
            while True:
                # 更新を待機
                shared_state.wait_for_update()
                
                # 新しいデータを取得して送信
                current = shared_state.get_current()
                self.send_sse_event(current)
                
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # クライアント切断は正常
            log(f"SSE切断: {self.client_address}")
        except Exception as e:
            log(f"SSEエラー: {e}")

    def send_sse_event(self, data):
        """SSEイベントをフォーマットして送信"""
        payload = {
            "schema": data.get("schema", "nowplaying.v1"),
            "ts": data.get("ts"),
            "title": data.get("title"),
            "senka": data.get("senka")
        }
        json_str = json.dumps(payload, ensure_ascii=False)
        msg = f"event: nowplaying\ndata: {json_str}\n\n"
        try:
            self.wfile.write(msg.encode('utf-8'))
            self.wfile.flush()
        except BrokenPipeError:
            raise

    def log_message(self, format, *args):
        pass
