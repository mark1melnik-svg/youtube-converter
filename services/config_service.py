import json
import os
from datetime import datetime
from pathlib import Path


def get_config_dir():
    if os.name == "nt":
        appdata = os.getenv("APPDATA")
        if appdata:
            cfg_dir = os.path.join(appdata, "YouTubeConverter")
        else:
            cfg_dir = os.path.join(str(Path.home()), ".YouTubeConverter")
    else:
        cfg_dir = os.path.join(str(Path.home()), ".config", "YouTubeConverter")
    os.makedirs(cfg_dir, exist_ok=True)
    return cfg_dir


def get_config_path():
    return os.path.join(get_config_dir(), "settings.json")


def get_history_path():
    return os.path.join(get_config_dir(), "history.json")


def load_settings(app):
    cfg = get_config_path()
    if not os.path.isfile(cfg):
        return
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            data = json.load(f)
        app.default_download_dir = data.get("download_dir", app.default_download_dir)
        app.cookies_dir = data.get("cookies_dir", app.cookies_dir)
        app.open_folder_after_download = data.get("open_folder_after_download", False)
        app.lang = data.get("lang", "ru")
        app.theme_mode = data.get("theme_mode", "dark")
        app.hw_accel = data.get("hw_accel", "auto")
        app.filename_template = data.get("filename_template", "%(title)s")
        app.auto_number_files = data.get("auto_number_files", True)
        app.autonumber_next = int(data.get("autonumber_next", getattr(app, "autonumber_next", 1)) or 1)
        app.video_quality_var.set(data.get("video_quality", "best"))
        app.audio_quality_var.set(data.get("audio_quality", "best"))

        app.subs_enabled_var.set(data.get("subs_enabled", False))
        app.subs_auto_var.set(data.get("subs_auto", True))
        app.subs_srt_var.set(data.get("subs_srt", True))
        app.subs_lang_var.set(data.get("subs_lang", "auto"))
        app.mode_var.set(data.get("mode", "video_mp4"))
        if hasattr(app, "embed_metadata_var"):
            app.embed_metadata_var.set(data.get("embed_metadata", True))
        if hasattr(app, "auto_paste_clipboard_var"):
            app.auto_paste_clipboard_var.set(data.get("auto_paste_clipboard", False))
        if hasattr(app, "download_playlist_var"):
            app.download_playlist_var.set(data.get("download_playlist", False))
    except Exception as e:
        print(f"Не удалось прочитать настройки: {e}")


def save_settings(app):
    cfg = get_config_path()
    data = {
        "download_dir": app.default_download_dir,
        "cookies_dir": app.cookies_dir,
        "open_folder_after_download": app.open_folder_after_download,
        "lang": app.lang,
        "theme_mode": app.theme_mode,
        "hw_accel": getattr(app, "hw_accel", "auto"),
        "filename_template": getattr(app, "filename_template", "%(title)s"),
        "auto_number_files": app.auto_number_files,
        "autonumber_next": int(getattr(app, "autonumber_next", 1) or 1),
        "video_quality": app.video_quality_var.get(),
        "audio_quality": app.audio_quality_var.get(),
        "subs_enabled": app.subs_enabled_var.get(),
        "subs_auto": app.subs_auto_var.get(),
        "subs_srt": app.subs_srt_var.get(),
        "subs_lang": app.subs_lang_var.get(),
        "mode": app.mode_var.get(),
        "embed_metadata": app.embed_metadata_var.get() if hasattr(app, "embed_metadata_var") else True,
        "auto_paste_clipboard": app.auto_paste_clipboard_var.get() if hasattr(app, "auto_paste_clipboard_var") else False,
        "download_playlist": app.download_playlist_var.get() if hasattr(app, "download_playlist_var") else False,
    }
    try:
        with open(cfg, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Не удалось сохранить настройки: {e}")


def load_history(app):
    path = get_history_path()
    if not os.path.isfile(path):
        app.history = []
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            app.history = json.load(f)
    except Exception:
        app.history = []


def save_history(app):
    path = get_history_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(app.history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        app.log(f"⚠️ Failed to save history: {e}", tag="err")


def add_history_entry(app, url: str, mode: str, download_path: str, file_path: str | None = None):
    entry = {
        "url": url,
        "mode": mode,
        "path": download_path,
        "file_path": file_path or download_path,
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    app.history.insert(0, entry)
    if len(app.history) > app.history_limit:
        app.history = app.history[:app.history_limit]
    app.save_history()
    app.refresh_history_tree()


def refresh_history_tree(app):
    app.history_tree.delete(*app.history_tree.get_children())
    for entry in app.history:
        app.history_tree.insert(
            "",
            "end",
            values=(entry["time"], entry["mode"], entry["url"]),
        )
