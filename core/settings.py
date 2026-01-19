import json
import os
from .config import BASE_DIR

SETTINGS_PATH = os.path.join(BASE_DIR, 'settings.json')

DEFAULT_SETTINGS = {
    "image_retention_days": 10,
    "log_retention_days": 10,
    "db_retention_days": 10,
    "last_roi_coords": None,
    "last_target_window_title": None
}

class SettingsManager:
    _settings = None

    @staticmethod
    def load_settings():
        if SettingsManager._settings is None:
            if os.path.exists(SETTINGS_PATH):
                try:
                    with open(SETTINGS_PATH, 'r', encoding='utf-8') as f:
                        SettingsManager._settings = json.load(f)
                except Exception:
                    SettingsManager._settings = DEFAULT_SETTINGS.copy()
            else:
                SettingsManager._settings = DEFAULT_SETTINGS.copy()
        
        # Ensure all keys exist
        for key, value in DEFAULT_SETTINGS.items():
            if key not in SettingsManager._settings:
                SettingsManager._settings[key] = value
                
        return SettingsManager._settings

    @staticmethod
    def save_settings(new_settings):
        SettingsManager._settings.update(new_settings)
        try:
            with open(SETTINGS_PATH, 'w', encoding='utf-8') as f:
                json.dump(SettingsManager._settings, f, indent=4)
        except Exception as e:
            print(f"Failed to save settings: {e}")

    @staticmethod
    def get(key, default=None):
        settings = SettingsManager.load_settings()
        return settings.get(key, default)
