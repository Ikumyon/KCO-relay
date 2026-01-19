import concurrent.futures
import re
from PIL import Image, ImageOps 
import pytesseract
from collections import Counter
from .logger import log

class OCRProcessor:
    def __init__(self, max_workers=None):
        import os
        if max_workers is None:
            cpu_count = os.cpu_count() or 4
            max_workers = min(10, max(4, cpu_count))
        
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self.candidates_history = [] # 連続一致確認用の履歴リスト
        self._confirmed_value = None # 直近で確定した値
        log(f"OCRプロセッサ初期化 (並列数: {max_workers})")

    def shutdown(self):
        """Executorを停止"""
        self.executor.shutdown(wait=False, cancel_futures=True)

    def process_image(self, image):
        """
        1枚の画像に対してOCR処理フローを実行し、結果を返す。
        戻り値: (status, result_text)
        status: "confirmed", "waiting", "duplicate", "invalid", "failed"
        """
        # 1. 候補画像生成
        img_candidates = self.generate_ocr_candidates(image)
        
        # 2. 並列OCR実行 (テキスト取得)
        ocr_raw_text = self.run_ocr_parallel(img_candidates)
        
        if not ocr_raw_text:
            self.candidates_history = [] # 読み取り失敗なら履歴リセット
            return "failed", None

        # 3. 正規化
        normalized_text = self.normalize_numeric_text(ocr_raw_text)
        
        # 4. バリデーション
        if not self.validate_numeric(normalized_text):
            return "invalid", normalized_text
            
        # 5. 確定ロジック (多数決/連続一致)
        status, value = self._analyze_consistency(normalized_text)
        
        return status, value

    def generate_ocr_candidates(self, image):
        """
        入力画像から、OCR読み取り精度を高めるための加工画像リストを生成する
        (3倍拡大、反転、二値化など)
        戻り値: [(name, PIL.Image), ...]
        """
        candidates = []
        
        # 1. 3倍拡大 (精度向上)
        try:
            width, height = image.size
            scale_factor = 3
            image_scaled = image.resize((width * scale_factor, height * scale_factor), Image.LANCZOS)
        except Exception as e:
            log(f"画像拡大エラー: {e}")
            image_scaled = image

        def add_padding(img, amount=10):
             return ImageOps.expand(img, border=amount, fill='white')

        img_gray = image_scaled.convert('L')
        
        # A. 反転 (白文字対応 - 本命)
        img_inv = ImageOps.invert(img_gray)
        candidates.append(("反転", add_padding(img_inv)))
        
        # B. 反転 + 二値化 (Otsu的な閾値指定)
        try:
            candidates.append(("反転+二値化100", add_padding(img_inv.point(lambda x: 255 if x > 100 else 0))))
            candidates.append(("反転+二値化128", add_padding(img_inv.point(lambda x: 255 if x > 128 else 0))))
            candidates.append(("反転+二値化180", add_padding(img_inv.point(lambda x: 255 if x > 180 else 0))))
        except:
            pass
            
        # C. そのまま
        candidates.append(("原画像", add_padding(img_gray)))
        
        return candidates

    def run_ocr_parallel(self, candidates):
        """
        生成された候補画像のリストに対して、異なるPSMモードで並列にTesseractを実行する。
        最も早く有効なフォーマットで返ってきた値を採用する。
        """
        psm_modes = [7, 6] # 7:line, 6:block
        
        def _run_tesseract(img, psm):
            config = f'--psm {psm}'
            text = pytesseract.image_to_string(img, config=config)
            return text.strip()

        # タスク投入
        futures = []
        for psm in psm_modes:
            for name, img in candidates:
                futures.append(self.executor.submit(_run_tesseract, img, psm))
        
        confirmed_num = None
        
        # 結果待機 (早いもの勝ち)
        for future in concurrent.futures.as_completed(futures):
            try:
                text = future.result()
                if not text:
                    continue

                # 簡易チェック: 数字が含まれているか
                matches = re.findall(r'(\d+\.\d+|\d+)', text)
                if matches:
                    candidate_num = max(matches, key=len)
                    # ここでは正規化せず、とりあえず何か数値らしいものが取れたら返す
                    # (バリデーションは後段で行うが、明らかにゴミなら弾きたいので簡易チェックはしてもいい)
                    # 今回はとりあえず最速で何かが取れたらそれを返す方針
                    confirmed_num = candidate_num
                    break 
            except Exception:
                pass
        
        # 残りのタスクはキャンセル推奨だが、Pythonのfuture.cancel()はrunning中だと効かないので放置
        
        return confirmed_num

    def normalize_numeric_text(self, text):
        """
        OCRテキストから数値を抽出し、システムで扱う形式（小数点以下切り捨てなど）に正規化する。
        例: '0.082' -> '0.0'
        """
        if not text:
            return ""
            
        # 既に run_ocr_parallel で抽出済みの場合もあるが、念のため再抽出してもよい
        # ここでは入力が '0.082' のような数値文字列であることを期待
        
        candidate_num = text
        if '.' in candidate_num:
            int_part, dec_part = candidate_num.split('.', 1)
            # ユーザー要望: 小数点第一位まで残して以降切り捨て
            if dec_part:
                candidate_num = f"{int_part}.{dec_part[:1]}"
            else:
                candidate_num = int_part
        
        return candidate_num

    def validate_numeric(self, text):
        """
        数値テキストとしての妥当性を検証する
        ルール: 0-9と.のみ、.は1個まで、先頭末尾の.禁止
        """
        if not text:
            return False
            
        allowed = set('0123456789.')
        if not set(text).issubset(allowed):
            return False
            
        if text.count('.') > 1:
            return False
            
        if text.startswith('.') or text.endswith('.'):
            return False
            
        return True

    def _analyze_consistency(self, text):
        """
        候補リストの履歴を用いて、値が安定しているか（確定してよいか）を判定する。
        """
        # 履歴追加
        self.candidates_history.append(text)
        log(f"候補追加: {self.candidates_history[-3:]} (全{len(self.candidates_history)}件)")
        
        # 履歴が10個以上なら古いのを捨てる
        if len(self.candidates_history) > 10:
            self.candidates_history.pop(0)

        # 直近3回のうち、最頻出の値をチェック
        if len(self.candidates_history) >= 3:
            recent = self.candidates_history[-3:]
            counter = Counter(recent)
            most_common = counter.most_common(1)[0]  # (値, 回数)
            value, count = most_common
            
            if count >= 2:
                # 2回以上出現 = 確定とみなす
                if self._confirmed_value == value:
                    # 前回確定した値と同じなら「重複(変更なし)」
                    return "duplicate", value
                
                # 新しい値で確定
                self._confirmed_value = value
                # 確定したので履歴をリセットして揺らぎを防止する
                self.candidates_history = []
                return "confirmed", value
        
        return "waiting", text
