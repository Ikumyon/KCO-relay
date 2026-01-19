import sqlite3
import time
from .config import DB_PATH
from .logger import log

class Database:
    @staticmethod
    def init_db():
        """データベースとテーブルを初期化します。"""
        try:
            conn = sqlite3.connect(DB_PATH)
            # WALモードを有効化
            conn.execute("PRAGMA journal_mode = WAL;")
            conn.execute("PRAGMA synchronous = NORMAL;")
            
            # nowplaying_state テーブル作成
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nowplaying_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    schema TEXT NOT NULL,
                    ts INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    senka TEXT,
                    updated_at INTEGER NOT NULL
                );
            """)

            # カラム追加チェック (senka)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT senka FROM nowplaying_state LIMIT 1")
            except sqlite3.OperationalError:
                log("DBスキーマ更新: nowplaying_state に senka カラムを追加します")
                try:
                    conn.execute("ALTER TABLE nowplaying_state ADD COLUMN senka TEXT DEFAULT ''")
                except Exception as e:
                    log(f"カラム追加エラー (nowplaying_state): {e}")

            # events テーブル作成
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts INTEGER NOT NULL,
                    schema TEXT NOT NULL,
                    title TEXT NOT NULL,
                    senka TEXT,
                    video_id TEXT,
                    url TEXT,
                    source TEXT NOT NULL DEFAULT 'youtube',
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL DEFAULT 'nowplaying'
                );
            """)

            # カラム追加チェック (events)
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT senka FROM events LIMIT 1")
            except sqlite3.OperationalError:
                log("DBスキーマ更新: events に senka カラムを追加します")
                try:
                    conn.execute("ALTER TABLE events ADD COLUMN senka TEXT DEFAULT ''")
                except Exception as e:
                    log(f"カラム追加エラー (events): {e}")
            
            # 存在しない場合は初期行を挿入
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM nowplaying_state WHERE id = 1")
            if cursor.fetchone()[0] == 0:
                cursor.execute("""
                    INSERT INTO nowplaying_state (id, schema, ts, title, senka, updated_at)
                    VALUES (1, 'nowplaying.v1', 0, '', '', 0);
                """)
            
            conn.commit()
            conn.close()
            log(f"データベースを初期化しました: {DB_PATH}")
        except Exception as e:
            log(f"データベース初期化エラー: {e}")

    @staticmethod
    def log_event(data, session_id):
        """イベントをeventsテーブルに記録します。"""
        try:
            conn = sqlite3.connect(DB_PATH)
            
            ts = data.get('ts')
            schema = data.get('schema', 'nowplaying.v1')
            title = data.get('title', '')
            senka = data.get('senka', '')
            video_id = data.get('video_id')
            url = data.get('url')
            source = data.get('source', 'youtube')
            kind = data.get('kind', 'nowplaying')
            
            conn.execute("""
                INSERT INTO events (ts, schema, title, senka, video_id, url, source, session_id, kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ts, schema, title, senka, video_id, url, source, session_id, kind))
            
            conn.commit()
            conn.close()
            log(f"イベント記録: {title} / 戦果:{senka} (ts={ts})")
        except Exception as e:
            log(f"イベント記録エラー: {e}")

    @staticmethod
    def update_state(data):
        """nowplayingの状態を更新します。"""
        try:
            conn = sqlite3.connect(DB_PATH)
            now_ts = int(time.time() * 1000)
            
            senka = data.get('senka', '')
            conn.execute("""
                UPDATE nowplaying_state
                SET schema = ?, ts = ?, title = ?, senka = ?, updated_at = ?
                WHERE id = 1
            """, (data['schema'], data['ts'], data['title'], senka, now_ts))
            conn.commit()
            conn.close()
            return True, now_ts
        except Exception as e:
            log(f"データベース更新エラー: {e}")
            return False, 0

    @staticmethod
    def get_state():
        """現在のnowplayingの状態を取得します。"""
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT schema, ts, title, senka, updated_at FROM nowplaying_state WHERE id = 1")
            row = cursor.fetchone()
            conn.close()
            if row:
                return {
                    "schema": row[0],
                    "ts": row[1],
                    "title": row[2],
                    "senka": row[3],
                    "updated_at": row[4]
                }
            return None
            return None
        except Exception as e:
            log(f"データベース取得エラー: {e}")
            return None

    @staticmethod
    def cleanup_old_events(days):
        """指定した日数より古いイベントデータを削除します。"""
        try:
            conn = sqlite3.connect(DB_PATH)
            # 現在時刻からdays日前以のタイムスタンプ(ms)を計算
            cutoff_ts = int((time.time() - (days * 24 * 60 * 60)) * 1000)
            
            cursor = conn.cursor()
            cursor.execute("DELETE FROM events WHERE ts < ?", (cutoff_ts,))
            deleted_count = cursor.rowcount
            
            conn.commit()
            if deleted_count > 0:
                conn.execute("VACUUM") # サイズ削減のためVACUUM
            conn.close()
            
            if deleted_count > 0:
                log(f"DBクリーンアップ: {days}日以前のデータ {deleted_count}件を削除しました")
        except Exception as e:
            log(f"DBクリーンアップエラー: {e}")
