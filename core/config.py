import os
import uuid
import pytesseract

# プロジェクトルートディレクトリの取得 (core/config.py の親の親)
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Tesseract設定
DEFAULT_TESSERACT_PATH = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
if os.path.exists(DEFAULT_TESSERACT_PATH):
    pytesseract.pytesseract.tesseract_cmd = DEFAULT_TESSERACT_PATH

# サーバー設定
INPUT_PORT = 5000
OUTPUT_PORT = 5001

# パス設定
DB_PATH = os.path.join(BASE_DIR, 'nowplaying.db')
LOG_DIR = os.path.join(BASE_DIR, 'logs')
IMAGE_DIR = os.path.join(BASE_DIR, 'images')
DEBUG_DIR = os.path.join(BASE_DIR, 'debug_ocr')

# その他
SESSION_ID = str(uuid.uuid4())
MAX_LOG_FILES = 20
MAX_DEBUG_IMAGES = 20
