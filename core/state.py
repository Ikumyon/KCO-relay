import threading

class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.data = {
            "schema": "nowplaying.v1",
            "ts": 0,
            "title": "",
            "senka": ""
        }
    
    def update(self, new_data):
        """
        状態を更新します。タイトルまたは戦果が変更された場合のみ更新と通知を行います。
        """
        with self.lock:
            current_title = self.data.get('title', '')
            new_title = new_data.get('title', '')
            
            current_senka = self.data.get('senka', '')
            new_senka = new_data.get('senka') # Noneの場合は無視するなど要検討だが、ここでは更新があれば反映
            
            # 両方空なら無視
            if not new_title and new_senka is None:
                return False

            # 変更チェック: タイトルまたは戦果が変わったか
            is_title_changed = (new_title and current_title != new_title)
            is_senka_changed = (new_senka is not None and str(current_senka) != str(new_senka))
            
            if not is_title_changed and not is_senka_changed:
                return False
            
            # 変更があった場合更新 (既存の値を保持しつつマージ)
            if new_title:
                self.data['title'] = new_title
            if new_senka is not None:
                self.data['senka'] = new_senka
            
            # その他のフィールドも更新
            if 'ts' in new_data:
                self.data['ts'] = new_data['ts']
            if 'schema' in new_data:
                self.data['schema'] = new_data['schema']

            self.condition.notify_all()
            return True

    def get_current(self):
        """現在の状態のコピーを返します。"""
        with self.lock:
            return self.data.copy()

    def wait_for_update(self, timeout=None):
        """更新があるまで待機します。"""
        with self.condition:
            return self.condition.wait(timeout)

# グローバル共有インスタンス
shared_state = SharedState()
