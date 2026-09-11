import tkinter as tk
from tkinter import ttk, messagebox, filedialog, StringVar
from datetime import datetime
from pathlib import Path
import os
import sys
import subprocess
import threading
import re
import queue as thread_queue
import time

import customtkinter as ctk
from i18n import get_translations
from settings_dialog import SettingsDialog
from services import config_service, download_service, env_service, preview_service
from tooltip import ToolTip

# Enable seamless .config() compatibility on all CTk widgets
ctk.CTkBaseClass.config = lambda self, *args, **kwargs: self.configure(*args, **kwargs)

_orig_pb_configure = ctk.CTkProgressBar.configure
def _pb_configure(self, require_redraw=False, **kwargs):
    if "value" in kwargs:
        val = kwargs.pop("value")
        max_val = kwargs.pop("maximum", 100) or 100
        norm = max(0.0, min(1.0, float(val) / float(max_val))) if max_val else 0.0
        self.set(norm)
    kwargs.pop("maximum", None)
    kwargs.pop("mode", None)
    if kwargs:
        return _orig_pb_configure(self, require_redraw=require_redraw, **kwargs)
ctk.CTkProgressBar.configure = _pb_configure


# ---------- Apple Clean Dark / Light Palette Constants ----------
APPLE_BG = ("#F2F2F7", "#161618")
APPLE_CARD = ("#FFFFFF", "#242426")
APPLE_CARD_BORDER = ("#E5E5EA", "#323234")
APPLE_INPUT_BG = ("#E5E5EA", "#1C1C1E")
APPLE_INPUT_BORDER = ("#D1D1D6", "#38383A")
APPLE_BTN_BG = ("#E5E5EA", "#2C2C2E")
APPLE_BTN_HOVER = ("#D1D1D6", "#3A3A3C")
APPLE_ACCENT = ("#007AFF", "#0A84FF")
APPLE_ACCENT_HOVER = ("#0051A8", "#0066CC")
APPLE_FG = ("#000000", "#FFFFFF")
APPLE_MUTED = ("#8E8E93", "#8E8E93")
APPLE_DISABLED = ("#AEAEB2", "#636366")


# ---------- Основное приложение ----------

class YouTubeConverterApp:
    def __init__(self, root: ctk.CTk):
        self.root = root

        self.lang = "ru"
        self.trans = get_translations()

        self.theme_mode = "dark"
        self.hw_accel = "auto"
        self.default_download_dir = str(Path.home() / "Downloads")
        self.cookies_dir = None
        self.open_folder_after_download = False
        self.filename_template = "%(title)s"
        self.auto_number_files = True
        self.autonumber_next = 1

        self.video_container_var = StringVar(value="mp4")
        self.audio_container_var = StringVar(value="mp3")
        self.mode_var = StringVar(value="video_mp4")
        self.mode_display_var = StringVar()
        self.video_quality_var = StringVar(value="best")
        self.audio_quality_var = StringVar(value="best")

        self.subs_enabled_var = tk.BooleanVar(value=False)
        self.subs_auto_var = tk.BooleanVar(value=True)
        self.subs_srt_var = tk.BooleanVar(value=True)
        self.subs_lang_var = StringVar(value="auto")

        self.time_from_var = StringVar(value="")
        self.time_to_var = StringVar(value="")
        self.embed_metadata_var = tk.BooleanVar(value=True)
        self.auto_paste_clipboard_var = tk.BooleanVar(value=False)
        self.download_playlist_var = tk.BooleanVar(value=False)
        self.last_downloaded_file = None
        self._last_auto_pasted = None

        self.load_settings()

        ctk.set_appearance_mode(self.theme_mode)
        ctk.set_default_color_theme("blue")

        self.root.title(self._t("app_title") + " v1.0.1")
        self.root.geometry("1180x760")
        self.root.minsize(980, 620)
        self.root.configure(fg_color=APPLE_BG)

        self._setup_app_icon()
        self.root.bind("<FocusIn>", self._on_window_focus)

        self.current_process: subprocess.Popen | None = None
        self.download_thread: threading.Thread | None = None
        self.cancel_requested = False

        self.progress_re = re.compile(r'(\d+(?:\.\d+)?)%')  # XX.X%

        self.thumb_image = None
        self.thumb_full_image = None
        self.thumb_source_image = None
        self.preview_title_label = None
        self.preview_channel_label = None
        self.preview_status_label = None
        self.preview_request_id = 0
        self._last_progress_log = ""
        self._last_progress_log_ts = 0.0
        self._warn_throttle = {}
        self._resize_after_id = None
        self._thumb_resize_after_id = None
        self._last_preview_wrap = None
        self._last_dir_wrap = None
        self._last_root_size = None
        self._resizing = False

        self.history = []
        self.history_limit = 50

        self.queue_thread: threading.Thread | None = None
        self.queue_running = False
        self.ui_queue = thread_queue.Queue()

        self.load_history()

        self.setup_ui()
        self._apply_theme_style()
        self._apply_mode_to_flags()
        self._sync_quality_controls()

        # Startup log: clean, informative, no diagnostic spam
        self.log(self._t("log_startup_ready"), tag="ok")

        self.root.after(50, self._process_ui_queue)
        self.root.after(100, lambda: self.check_environment(verbose=False))
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _setup_app_icon(self):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("mark1melnik.youtubeconverter.1.0")
        except Exception:
            pass

        base_dirs = [
            self.get_executable_dir(),
            os.getcwd(),
            os.path.dirname(os.path.abspath(__file__)),
        ]
        for b in base_dirs:
            p = os.path.join(b, "icon.ico")
            if os.path.isfile(p):
                try:
                    self.root.iconbitmap(p)
                    break
                except Exception:
                    pass

    def _on_window_focus(self, event=None):
        if not getattr(self, "auto_paste_clipboard_var", None) or not self.auto_paste_clipboard_var.get():
            return
        try:
            clip = self.root.clipboard_get().strip()
        except Exception:
            return
        if clip and ("youtube.com" in clip or "youtu.be" in clip):
            current = self.url_entry.get().strip()
            if clip != current and clip != getattr(self, "_last_auto_pasted", None):
                self._last_auto_pasted = clip
                self.url_entry.delete(0, tk.END)
                self.url_entry.insert(0, clip)
                self._on_url_key()

    def _on_url_key(self, event=None):
        """Очистка + debounce для автопревью."""
        self.preview_request_id += 1
        self.thumb_image = None
        self.thumb_full_image = None
        self.thumb_label.configure(image=None)
        if self.preview_title_label:
            self.preview_title_label.configure(text="")
        if self.preview_channel_label:
            self.preview_channel_label.configure(text="")
        if self.preview_status_label:
            self.preview_status_label.configure(text=self._t("preview_status_idle"))

        if hasattr(self, "_preview_timeout"):
            self.root.after_cancel(self._preview_timeout)
        self._preview_timeout = self.root.after(800, self._update_preview)

    # ---------- i18n ----------
    def _t(self, key: str):
        return self.trans.get(self.lang, {}).get(key, key)

    def retranslate_ui(self):
        self.root.title(self._t("app_title") + " v1.0.1")

        self.url_label.configure(text=self._t("url"))
        self.mode_label.configure(text=self._t("mode"))
        self._update_mode_combo_values()
        self.paste_url_button.configure(text=self._t("paste_url"))
        self.clear_url_button.configure(text=self._t("clear_url"))

        self.download_button.configure(text=self._t("download"))
        self.cancel_button.configure(text=self._t("cancel"))
        self.settings_button.configure(text=self._t("settings"))
        self.status_label.configure(text=self._t("status_ready"))

        # Retranslate Tabview tabs
        try:
            old_log = getattr(self, "tab_log_name", "Log")
            new_log = self._t("tab_log")
            if old_log != new_log:
                self.tabview.rename(old_log, new_log)
                self.tab_log_name = new_log

            old_hist = getattr(self, "tab_history_name", "History")
            new_hist = self._t("tab_history")
            if old_hist != new_hist:
                self.tabview.rename(old_hist, new_hist)
                self.tab_history_name = new_hist

            old_queue = getattr(self, "tab_queue_name", "Queue")
            new_queue = self._t("tab_queue")
            if old_queue != new_queue:
                self.tabview.rename(old_queue, new_queue)
                self.tab_queue_name = new_queue
        except Exception:
            pass

        self.download_folder_label.configure(text=self._t("download_folder"))
        self.open_folder_button.configure(text=self._t("open_folder"))
        self.change_folder_button.configure(text=self._t("change_folder"))

        self.subs_group_label.configure(text=self._t("subs_group"))
        self.quality_group_label.configure(text=self._t("quality_group"))
        self.video_quality_label.configure(text=self._t("video_quality"))
        self.audio_quality_label.configure(text=self._t("audio_quality"))
        self.auto_number_check.configure(text=self._t("auto_number_files"))
        if hasattr(self, "download_playlist_check"):
            self.download_playlist_check.configure(text=self._t("download_playlist"))
        self.subs_enable_check.configure(text=self._t("subs_enable"))
        self.subs_lang_label.configure(text=self._t("subs_lang"))
        self.subs_auto_check.configure(text=self._t("subs_auto"))
        self.subs_srt_check.configure(text=self._t("subs_srt"))

        self.format_hint_label.configure(text=self._t("format_hint"))

        self.log_label.configure(text=self._t("log_label"))
        self.clear_log_button.configure(text=self._t("clear_log"))
        self.copy_log_button.configure(text=self._t("copy_log"))

        self.history_label.configure(text=self._t("history_title"))
        self.repeat_button.configure(text=self._t("history_repeat"))

        self.queue_label.configure(text=self._t("queue_label"))
        self.queue_start_button.configure(text=self._t("queue_start"))
        self.queue_remove_button.configure(text=self._t("queue_remove_selected"))
        self.queue_up_button.configure(text=self._t("queue_move_up"))
        self.queue_down_button.configure(text=self._t("queue_move_down"))
        self.queue_clear_button.configure(text=self._t("queue_clear"))
        self.queue_save_button.configure(text=self._t("queue_save"))
        self.queue_load_button.configure(text=self._t("queue_load"))
        if hasattr(self, "queue_menu"):
            self.queue_menu.entryconfigure(0, label=self._t("queue_menu_paste"))
            self.queue_menu.entryconfigure(1, label=self._t("queue_menu_remove_selected"))
            self.queue_menu.entryconfigure(2, label=self._t("queue_menu_clear"))
        self.preview_header_label.configure(text=self._t("preview_panel_title"))
        self.preview_status_label.configure(text=self._t("preview_status_idle"))
        self.check_env_button.configure(text=self._t("check_environment"))
        self.list_formats_button.configure(text=self._t("list_formats"))
        if hasattr(self, "entry_menu"):
            self.entry_menu.entryconfigure(0, label=self._t("entry_menu_clear"))
            self.entry_menu.entryconfigure(1, label=self._t("entry_menu_paste"))

        cols = self._t("history_cols")
        for i, col in enumerate(cols):
            self.history_tree.heading(f"#{i}", text=col)

        if hasattr(self, "trim_group_label"):
            self.trim_group_label.configure(text=self._t("trim_group"))
            self.trim_from_label.configure(text=self._t("trim_from"))
            self.trim_to_label.configure(text=self._t("trim_to"))
            self.trim_hint_label.configure(text=self._t("trim_hint"))
        if hasattr(self, "embed_metadata_check"):
            self.embed_metadata_check.configure(text=self._t("embed_metadata"))
        if hasattr(self, "open_file_button"):
            self.open_file_button.configure(text=self._t("history_open_file"))
            self.show_folder_button.configure(text=self._t("history_show_in_folder"))
        if hasattr(self, "history_menu"):
            try:
                self.history_menu.entryconfigure(0, label=self._t("history_menu_open"))
                self.history_menu.entryconfigure(1, label=self._t("history_menu_folder"))
                self.history_menu.entryconfigure(2, label=self._t("history_menu_copy_url"))
                self.history_menu.entryconfigure(4, label=self._t("history_menu_remove"))
            except Exception:
                pass

    # ---------- настройки / история ----------

    def _get_output_template_with_choice(self, download_path: str, ext: str = "%(ext)s"):
        base_tmpl = (self.filename_template or "%(title,id)s").strip()
        if not base_tmpl:
            base_tmpl = "%(title,id)s"
        base_path = Path(download_path)
        return str(base_path / f"{base_tmpl}.{ext}")

    def load_settings(self):
        config_service.load_settings(self)

    def save_settings(self):
        config_service.save_settings(self)

    def load_history(self):
        config_service.load_history(self)

    def save_history(self):
        config_service.save_history(self)

    def add_history_entry(self, url: str, mode: str, download_path: str, file_path: str | None = None):
        config_service.add_history_entry(self, url, mode, download_path, file_path)

    def refresh_history_tree(self):
        config_service.refresh_history_tree(self)

    # ---------- UI Setup (CustomTkinter + Apple Dark HIG) ----------

    def setup_ui(self):
        # Configure root grid weights: workspace row expands (weight=1), bottom status bar fixed (weight=0)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=0)

        root_container = ctk.CTkFrame(self.root, fg_color="transparent")
        root_container.grid(row=0, column=0, sticky="nsew", padx=14, pady=(10, 2))
        root_container.columnconfigure(0, weight=1)
        root_container.rowconfigure(0, weight=0)
        root_container.rowconfigure(1, weight=1)

        # BOTTOM STATUS BAR: Pinned permanently at row 1 (weight=0)
        status_bar = ctk.CTkFrame(self.root, fg_color="transparent", height=28)
        status_bar.grid(row=1, column=0, sticky="ew", padx=14, pady=(2, 6))
        status_bar.columnconfigure(4, weight=1)

        self.env_ytdlp_label = ctk.CTkLabel(status_bar, text="yt-dlp: ⏳", text_color=APPLE_MUTED, font=("Segoe UI", 9))
        self.env_ytdlp_label.grid(row=0, column=0, sticky="w", padx=(0, 16))

        self.env_ffmpeg_label = ctk.CTkLabel(status_bar, text="ffmpeg: ⏳", text_color=APPLE_MUTED, font=("Segoe UI", 9))
        self.env_ffmpeg_label.grid(row=0, column=1, sticky="w", padx=(0, 16))

        self.env_node_label = ctk.CTkLabel(status_bar, text="node: ⏳", text_color=APPLE_MUTED, font=("Segoe UI", 9))
        self.env_node_label.grid(row=0, column=2, sticky="w", padx=(0, 16))

        self.env_cookies_label = ctk.CTkLabel(status_bar, text="cookies.txt: ⏳", text_color=APPLE_MUTED, font=("Segoe UI", 9))
        self.env_cookies_label.grid(row=0, column=3, sticky="w", padx=(0, 16))

        self.check_env_button = ctk.CTkButton(
            status_bar,
            text=self._t("check_environment"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=24,
            font=("Segoe UI", 9),
            command=lambda: self.check_environment(verbose=True),
        )
        self.check_env_button.grid(row=0, column=5, sticky="e")

        # ==========================================
        # CARD 1: Input & Primary Action Bar
        # ==========================================
        card1 = ctk.CTkFrame(
            root_container,
            fg_color=APPLE_CARD,
            border_color=APPLE_CARD_BORDER,
            border_width=1,
            corner_radius=12,
        )
        card1.grid(row=0, column=0, sticky="nwe", pady=(0, 10))
        card1.columnconfigure(0, weight=0)
        card1.columnconfigure(1, weight=1)
        card1.columnconfigure(2, weight=0)

        # Row 0: URL input + URL Action Buttons (Paste, Clear, Settings)
        self.url_label = ctk.CTkLabel(
            card1,
            text=self._t("url"),
            font=("Segoe UI", 12, "bold"),
            text_color=APPLE_FG,
        )
        self.url_label.grid(row=0, column=0, sticky="w", padx=(14, 8), pady=(12, 4))

        self.url_entry = ctk.CTkEntry(
            card1,
            fg_color=APPLE_INPUT_BG,
            border_color=APPLE_INPUT_BORDER,
            text_color=APPLE_FG,
            corner_radius=8,
            height=32,
        )
        self.url_entry.grid(row=0, column=1, sticky="we", padx=(0, 8), pady=(12, 4))
        self.url_entry.bind("<Key>", self._on_url_key)

        url_btn_bar = ctk.CTkFrame(card1, fg_color="transparent")
        url_btn_bar.grid(row=0, column=2, sticky="e", padx=(0, 14), pady=(12, 4))

        self.paste_url_button = ctk.CTkButton(
            url_btn_bar,
            text=self._t("paste_url"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            text_color=APPLE_FG,
            corner_radius=8,
            height=32,
            width=95,
            command=self._paste_url,
        )
        self.paste_url_button.pack(side=tk.LEFT, padx=(0, 6))

        self.clear_url_button = ctk.CTkButton(
            url_btn_bar,
            text=self._t("clear_url"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            text_color=APPLE_FG,
            corner_radius=8,
            height=32,
            width=85,
            command=self._clear_url,
        )
        self.clear_url_button.pack(side=tk.LEFT, padx=(0, 6))

        self.settings_button = ctk.CTkButton(
            url_btn_bar,
            text=self._t("settings"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            text_color=APPLE_FG,
            corner_radius=8,
            height=32,
            width=95,
            command=self.open_settings,
        )
        self.settings_button.pack(side=tk.LEFT)

        # Row 1: Mode + Download & Cancel Buttons
        self.mode_label = ctk.CTkLabel(
            card1,
            text=self._t("mode"),
            font=("Segoe UI", 11),
            text_color=APPLE_FG,
        )
        self.mode_label.grid(row=1, column=0, sticky="w", padx=(14, 8), pady=(4, 6))

        self.mode_combo = ctk.CTkComboBox(
            card1,
            variable=self.mode_display_var,
            values=[],
            command=self._on_mode_combo_change,
            fg_color=APPLE_INPUT_BG,
            border_color=APPLE_INPUT_BORDER,
            button_color=APPLE_INPUT_BORDER,
            button_hover_color="#48484A",
            dropdown_fg_color=APPLE_INPUT_BG,
            dropdown_text_color=APPLE_FG,
            text_color=APPLE_FG,
            corner_radius=8,
            height=32,
            state="readonly",
        )
        self._update_mode_combo_values()
        self.mode_combo.grid(row=1, column=1, sticky="we", padx=(0, 8), pady=(4, 6))

        action_btn_bar = ctk.CTkFrame(card1, fg_color="transparent")
        action_btn_bar.grid(row=1, column=2, sticky="we", padx=(0, 14), pady=(4, 6))
        action_btn_bar.columnconfigure(0, weight=3)
        action_btn_bar.columnconfigure(1, weight=2)

        self.download_button = ctk.CTkButton(
            action_btn_bar,
            text=self._t("download"),
            fg_color=APPLE_ACCENT,
            hover_color=APPLE_ACCENT_HOVER,
            text_color="#FFFFFF",
            corner_radius=8,
            height=32,
            font=("Segoe UI", 11, "bold"),
            command=self.download_video,
        )
        self.download_button.grid(row=0, column=0, sticky="we", padx=(0, 6))

        self.cancel_button = ctk.CTkButton(
            action_btn_bar,
            text=self._t("cancel"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            text_color=APPLE_FG,
            corner_radius=8,
            height=32,
            state="disabled",
            command=self.cancel_download,
        )
        self.cancel_button.grid(row=0, column=1, sticky="we")

        # Row 2: Status
        self.status_label = ctk.CTkLabel(
            card1,
            text=self._t("status_ready"),
            text_color=APPLE_MUTED,
            font=("Segoe UI", 10),
            anchor="w",
        )
        self.status_label.grid(row=2, column=0, columnspan=3, sticky="w", padx=(14, 14), pady=(4, 2))

        # Row 3: Thin sleek progress bar (height=4, no heavy dividers)
        self.progress = ctk.CTkProgressBar(
            card1,
            fg_color=APPLE_INPUT_BG,
            progress_color=APPLE_ACCENT,
            height=4,
            corner_radius=2,
        )
        self.progress.grid(row=3, column=0, columnspan=3, sticky="we", padx=(14, 14), pady=(4, 12))
        self.progress.set(0)

        # ==========================================
        # MIDDLE AREA: 2 columns
        # Left: Cards 2 & 3 | Right: Preview & Tabview
        # ==========================================
        main_content = ctk.CTkFrame(root_container, fg_color="transparent")
        main_content.grid(row=1, column=0, sticky="nsew")
        main_content.columnconfigure(0, weight=5)
        main_content.columnconfigure(1, weight=6)
        main_content.rowconfigure(0, weight=1)

        # --- LEFT COLUMN (Cards 2 & 3) ---
        left_col = ctk.CTkFrame(main_content, fg_color="transparent")
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        left_col.columnconfigure(0, weight=1)
        left_col.rowconfigure(0, weight=1)
        left_col.rowconfigure(1, weight=0)

        # ------------------------------------------
        # CARD 2: Format & Options Card
        # ------------------------------------------
        card2 = ctk.CTkFrame(
            left_col,
            fg_color=APPLE_CARD,
            border_color=APPLE_CARD_BORDER,
            border_width=1,
            corner_radius=12,
        )
        card2.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
        card2.columnconfigure(0, weight=1)
        card2.columnconfigure(1, weight=1)

        # Section: Quality & Media Options
        self.quality_group_label = ctk.CTkLabel(
            card2,
            text=self._t("quality_group"),
            font=("Segoe UI", 12, "bold"),
            text_color=APPLE_FG,
        )
        self.quality_group_label.grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))

        # Format List button as a neat flat secondary button
        self.list_formats_button = ctk.CTkButton(
            card2,
            text=self._t("list_formats"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            text_color=APPLE_FG,
            corner_radius=8,
            height=26,
            width=120,
            font=("Segoe UI", 10),
            command=self.list_formats,
        )
        self.list_formats_button.grid(row=0, column=1, sticky="e", padx=(0, 14), pady=(12, 4))

        # Video quality
        self.video_quality_label = ctk.CTkLabel(
            card2,
            text=self._t("video_quality"),
            text_color=APPLE_FG,
            font=("Segoe UI", 11),
        )
        self.video_quality_label.grid(row=1, column=0, sticky="w", padx=(14, 6), pady=2)
        self.video_quality_combo = ctk.CTkComboBox(
            card2,
            variable=self.video_quality_var,
            values=["best", "2160", "1440", "1080", "720", "480", "360"],
            command=lambda e: self.save_settings(),
            fg_color=APPLE_INPUT_BG,
            border_color=APPLE_INPUT_BORDER,
            button_color=APPLE_INPUT_BORDER,
            button_hover_color="#48484A",
            dropdown_fg_color=APPLE_INPUT_BG,
            dropdown_text_color=APPLE_FG,
            text_color=APPLE_FG,
            corner_radius=8,
            height=28,
            state="readonly",
        )
        self.video_quality_combo.grid(row=1, column=1, sticky="we", padx=(6, 14), pady=2)

        # Audio quality
        self.audio_quality_label = ctk.CTkLabel(
            card2,
            text=self._t("audio_quality"),
            text_color=APPLE_FG,
            font=("Segoe UI", 11),
        )
        self.audio_quality_label.grid(row=2, column=0, sticky="w", padx=(14, 6), pady=2)
        self.audio_quality_combo = ctk.CTkComboBox(
            card2,
            variable=self.audio_quality_var,
            values=["best", "320", "256", "192", "160", "128", "96"],
            command=lambda e: self.save_settings(),
            fg_color=APPLE_INPUT_BG,
            border_color=APPLE_INPUT_BORDER,
            button_color=APPLE_INPUT_BORDER,
            button_hover_color="#48484A",
            dropdown_fg_color=APPLE_INPUT_BG,
            dropdown_text_color=APPLE_FG,
            text_color=APPLE_FG,
            corner_radius=8,
            height=28,
            state="readonly",
        )
        self.audio_quality_combo.grid(row=2, column=1, sticky="we", padx=(6, 14), pady=2)

        # Checkboxes: auto-numbering & metadata
        self.auto_number_var = tk.BooleanVar(value=self.auto_number_files)
        self.auto_number_check = ctk.CTkCheckBox(
            card2,
            text=self._t("auto_number_files"),
            variable=self.auto_number_var,
            command=self._on_auto_number_toggle,
            corner_radius=4,
            fg_color=APPLE_ACCENT,
            hover_color=APPLE_ACCENT_HOVER,
            text_color=APPLE_FG,
            font=("Segoe UI", 11),
        )
        self.auto_number_check.grid(row=3, column=0, columnspan=2, sticky="w", padx=14, pady=2)

        self.embed_metadata_check = ctk.CTkCheckBox(
            card2,
            text=self._t("embed_metadata"),
            variable=self.embed_metadata_var,
            command=self.save_settings,
            corner_radius=4,
            fg_color=APPLE_ACCENT,
            hover_color=APPLE_ACCENT_HOVER,
            text_color=APPLE_FG,
            font=("Segoe UI", 11),
        )
        self.embed_metadata_check.grid(row=4, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 6))

        self.download_playlist_check = ctk.CTkCheckBox(
            card2,
            text=self._t("download_playlist"),
            variable=self.download_playlist_var,
            command=self.save_settings,
            corner_radius=4,
            fg_color=APPLE_ACCENT,
            hover_color=APPLE_ACCENT_HOVER,
            text_color=APPLE_FG,
            font=("Segoe UI", 11),
        )
        self.download_playlist_check.grid(row=5, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 6))

        # Section: Subtitles
        self.subs_group_label = ctk.CTkLabel(
            card2,
            text=self._t("subs_group"),
            font=("Segoe UI", 12, "bold"),
            text_color=APPLE_FG,
        )
        self.subs_group_label.grid(row=6, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 4))

        self.subs_enable_check = ctk.CTkCheckBox(
            card2,
            text=self._t("subs_enable"),
            variable=self.subs_enabled_var,
            command=self._on_subs_enable_toggle,
            corner_radius=4,
            fg_color=APPLE_ACCENT,
            hover_color=APPLE_ACCENT_HOVER,
            text_color=APPLE_FG,
            font=("Segoe UI", 11),
        )
        self.subs_enable_check.grid(row=7, column=0, columnspan=2, sticky="w", padx=14, pady=2)

        self.subs_lang_label = ctk.CTkLabel(
            card2,
            text=self._t("subs_lang"),
            text_color=APPLE_FG,
            font=("Segoe UI", 11),
        )
        self.subs_lang_label.grid(row=8, column=0, sticky="w", padx=(14, 6), pady=2)
        self.subs_lang_combo = ctk.CTkComboBox(
            card2,
            variable=self.subs_lang_var,
            values=["auto", "ru", "en", "ru,en", "all"],
            command=lambda e: self.save_settings(),
            fg_color=APPLE_INPUT_BG,
            border_color=APPLE_INPUT_BORDER,
            button_color=APPLE_INPUT_BORDER,
            button_hover_color="#48484A",
            dropdown_fg_color=APPLE_INPUT_BG,
            dropdown_text_color=APPLE_FG,
            text_color=APPLE_FG,
            corner_radius=8,
            height=28,
            state="readonly",
        )
        self.subs_lang_combo.grid(row=8, column=1, sticky="we", padx=(6, 14), pady=2)

        self.subs_auto_check = ctk.CTkCheckBox(
            card2,
            text=self._t("subs_auto"),
            variable=self.subs_auto_var,
            command=self.save_settings,
            corner_radius=4,
            fg_color=APPLE_ACCENT,
            hover_color=APPLE_ACCENT_HOVER,
            text_color=APPLE_FG,
            font=("Segoe UI", 11),
        )
        self.subs_auto_check.grid(row=9, column=0, columnspan=2, sticky="w", padx=14, pady=2)

        self.subs_srt_check = ctk.CTkCheckBox(
            card2,
            text=self._t("subs_srt"),
            variable=self.subs_srt_var,
            command=self.save_settings,
            corner_radius=4,
            fg_color=APPLE_ACCENT,
            hover_color=APPLE_ACCENT_HOVER,
            text_color=APPLE_FG,
            font=("Segoe UI", 11),
        )
        self.subs_srt_check.grid(row=10, column=0, columnspan=2, sticky="w", padx=14, pady=(2, 6))

        # Section: Timecode Trimming
        self.trim_group_label = ctk.CTkLabel(
            card2,
            text=self._t("trim_group"),
            font=("Segoe UI", 12, "bold"),
            text_color=APPLE_FG,
        )
        self.trim_group_label.grid(row=11, column=0, columnspan=2, sticky="w", padx=14, pady=(10, 4))

        trim_bar = ctk.CTkFrame(card2, fg_color="transparent")
        trim_bar.grid(row=12, column=0, columnspan=2, sticky="we", padx=14, pady=(2, 4))

        self.trim_from_label = ctk.CTkLabel(
            trim_bar,
            text=self._t("trim_from"),
            text_color=APPLE_FG,
            font=("Segoe UI", 10),
        )
        self.trim_from_label.pack(side=tk.LEFT, padx=(0, 4))
        self.time_from_entry = ctk.CTkEntry(
            trim_bar,
            textvariable=self.time_from_var,
            width=70,
            height=28,
            fg_color=APPLE_INPUT_BG,
            border_color=APPLE_INPUT_BORDER,
            text_color=APPLE_FG,
            corner_radius=6,
        )
        self.time_from_entry.pack(side=tk.LEFT, padx=(0, 8))

        self.trim_to_label = ctk.CTkLabel(
            trim_bar,
            text=self._t("trim_to"),
            text_color=APPLE_FG,
            font=("Segoe UI", 10),
        )
        self.trim_to_label.pack(side=tk.LEFT, padx=(0, 4))
        self.time_to_entry = ctk.CTkEntry(
            trim_bar,
            textvariable=self.time_to_var,
            width=70,
            height=28,
            fg_color=APPLE_INPUT_BG,
            border_color=APPLE_INPUT_BORDER,
            text_color=APPLE_FG,
            corner_radius=6,
        )
        self.time_to_entry.pack(side=tk.LEFT, padx=(0, 6))

        self.trim_clear_button = ctk.CTkButton(
            trim_bar,
            text="✕",
            width=28,
            height=28,
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            command=self._clear_trim,
        )
        self.trim_clear_button.pack(side=tk.LEFT, padx=(0, 6))

        self.trim_hint_label = ctk.CTkLabel(
            trim_bar,
            text=self._t("trim_hint"),
            text_color=APPLE_MUTED,
            font=("Segoe UI", 9),
        )
        self.trim_hint_label.pack(side=tk.LEFT, padx=(0, 2))

        self.format_hint_label = ctk.CTkLabel(
            card2,
            text=self._t("format_hint"),
            justify="left",
            text_color=APPLE_MUTED,
            font=("Segoe UI", 9),
        )
        self.format_hint_label.grid(row=13, column=0, columnspan=2, sticky="we", padx=14, pady=(4, 10))

        # ------------------------------------------
        # CARD 3: Destination Folder Card
        # ------------------------------------------
        card3 = ctk.CTkFrame(
            left_col,
            fg_color=APPLE_CARD,
            border_color=APPLE_CARD_BORDER,
            border_width=1,
            corner_radius=12,
        )
        card3.grid(row=1, column=0, sticky="swe")
        card3.columnconfigure(0, weight=1)

        self.download_folder_label = ctk.CTkLabel(
            card3,
            text=self._t("download_folder"),
            font=("Segoe UI", 12, "bold"),
            text_color=APPLE_FG,
        )
        self.download_folder_label.grid(row=0, column=0, sticky="w", padx=14, pady=(10, 2))

        self.download_dir_label = ctk.CTkLabel(
            card3,
            text=self.default_download_dir,
            text_color=APPLE_MUTED,
            font=("Segoe UI", 9),
            anchor="w",
        )
        self.download_dir_label.grid(row=1, column=0, sticky="we", padx=14, pady=(2, 6))

        folder_btn_bar = ctk.CTkFrame(card3, fg_color="transparent")
        folder_btn_bar.grid(row=2, column=0, sticky="we", padx=14, pady=(0, 10))
        folder_btn_bar.columnconfigure(0, weight=1)
        folder_btn_bar.columnconfigure(1, weight=1)

        self.open_folder_button = ctk.CTkButton(
            folder_btn_bar,
            text=self._t("open_folder"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            text_color=APPLE_FG,
            corner_radius=8,
            height=28,
            command=self._open_download_dir,
        )
        self.open_folder_button.grid(row=0, column=0, sticky="w")

        self.change_folder_button = ctk.CTkButton(
            folder_btn_bar,
            text=self._t("change_folder"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            text_color=APPLE_FG,
            corner_radius=8,
            height=28,
            command=self._change_download_dir,
        )
        self.change_folder_button.grid(row=0, column=1, sticky="e")

        # --- RIGHT COLUMN (Preview + Tabview) ---
        right_col = ctk.CTkFrame(main_content, fg_color="transparent")
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        right_col.columnconfigure(0, weight=1)
        right_col.rowconfigure(0, weight=0)
        right_col.rowconfigure(1, weight=1)

        # Preview Card
        preview_card = ctk.CTkFrame(
            right_col,
            fg_color=APPLE_CARD,
            border_color=APPLE_CARD_BORDER,
            border_width=1,
            corner_radius=12,
        )
        preview_card.grid(row=0, column=0, sticky="nwe", pady=(0, 8))
        preview_card.columnconfigure(0, weight=1)

        self.preview_header_label = ctk.CTkLabel(
            preview_card,
            text=self._t("preview_panel_title"),
            font=("Segoe UI", 12, "bold"),
            text_color=APPLE_FG,
        )
        self.preview_header_label.pack(anchor="w", padx=14, pady=(10, 2))

        self.preview_status_label = ctk.CTkLabel(
            preview_card,
            text=self._t("preview_status_idle"),
            text_color=APPLE_MUTED,
            font=("Segoe UI", 9),
        )
        self.preview_status_label.pack(anchor="w", padx=14, pady=(0, 4))

        self.preview_title_label = ctk.CTkLabel(
            preview_card,
            text="",
            wraplength=380,
            justify="left",
            font=("Segoe UI", 10, "bold"),
            text_color=APPLE_FG,
        )
        self.preview_title_label.pack(anchor="w", padx=14)

        self.preview_channel_label = ctk.CTkLabel(
            preview_card,
            text="",
            wraplength=380,
            justify="left",
            text_color=APPLE_MUTED,
            font=("Segoe UI", 9),
        )
        self.preview_channel_label.pack(anchor="w", padx=14, pady=(0, 6))

        self.thumb_label = ctk.CTkLabel(preview_card, text="")
        self.thumb_label.pack(anchor="center", expand=True, padx=14, pady=(0, 10))
        self.thumb_label.bind("<Button-1>", self._open_thumbnail_popup)

        # Tabview container: fixed boundary to lock height and eliminate jumping
        tab_container = ctk.CTkFrame(right_col, fg_color="transparent", height=340)
        tab_container.grid(row=1, column=0, sticky="nsew")
        tab_container.grid_propagate(False)
        tab_container.pack_propagate(False)

        # Tabview tabs (Log, History, Queue)
        self.tabview = ctk.CTkTabview(
            tab_container,
            fg_color=APPLE_CARD,
            segmented_button_fg_color=("#E5E5EA", "#1E1E20"),
            segmented_button_selected_color=APPLE_ACCENT,
            segmented_button_selected_hover_color=APPLE_ACCENT_HOVER,
            corner_radius=12,
        )
        self.tabview.pack(fill=tk.BOTH, expand=True)
        self.tabview.grid_propagate(False)
        self.tabview.pack_propagate(False)

        self.tab_log_name = self._t("tab_log")
        self.tab_history_name = self._t("tab_history")
        self.tab_queue_name = self._t("tab_queue")

        self.tab_log = self.tabview.add(self.tab_log_name)
        self.tab_history = self.tabview.add(self.tab_history_name)
        self.tab_queue = self.tabview.add(self.tab_queue_name)
        self.tab_main = self.tab_log

        self.tab_log.pack_propagate(False)
        self.tab_history.pack_propagate(False)
        self.tab_queue.pack_propagate(False)
        self.tab_log.grid_propagate(False)
        self.tab_history.grid_propagate(False)
        self.tab_queue.grid_propagate(False)

        # --- tab_log ---
        log_header = ctk.CTkFrame(self.tab_log, fg_color="transparent")
        log_header.pack(fill="x", padx=6, pady=(6, 4))
        self.log_label = ctk.CTkLabel(
            log_header,
            text=self._t("log_label"),
            font=("Segoe UI", 11, "bold"),
            text_color=APPLE_FG,
        )
        self.log_label.pack(side=tk.LEFT)

        self.copy_log_button = ctk.CTkButton(
            log_header,
            text=self._t("copy_log"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=26,
            font=("Segoe UI", 9),
            command=self.copy_logs,
        )
        self.copy_log_button.pack(side=tk.RIGHT, padx=(4, 0))
        self.clear_log_button = ctk.CTkButton(
            log_header,
            text=self._t("clear_log"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=26,
            font=("Segoe UI", 9),
            command=self.clear_logs,
        )
        self.clear_log_button.pack(side=tk.RIGHT)

        self.log_text = ctk.CTkTextbox(
            self.tab_log,
            fg_color=APPLE_INPUT_BG,
            text_color=APPLE_FG,
            font=("Consolas", 11),
            corner_radius=8,
        )
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.log_text.tag_config("ok", foreground="#22c55e")
        self.log_text.tag_config("warn", foreground="#f59e0b")
        self.log_text.tag_config("err", foreground="#ef4444")
        self.log_text.tag_config("ts", foreground="#8E8E93")
        self.log_text.tag_config("cmd", foreground="#0A84FF" if self.theme_mode == "dark" else "#007AFF")
        self.log_text.configure(state="disabled")

        # --- tab_history ---
        self.history_label = ctk.CTkLabel(
            self.tab_history,
            text=self._t("history_title"),
            font=("Segoe UI", 11, "bold"),
            text_color=APPLE_FG,
        )
        self.history_label.pack(anchor="w", padx=6, pady=(6, 4))

        tree_frame = ctk.CTkFrame(self.tab_history, fg_color=APPLE_CARD, corner_radius=8)
        tree_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))

        cols = self._t("history_cols")
        self.history_tree = ttk.Treeview(
            tree_frame,
            columns=("#0", "#1", "#2"),
            show="headings",
            height=6,
        )
        for i, col in enumerate(cols):
            self.history_tree.heading(f"#{i}", text=col)
            self.history_tree.column(f"#{i}", width=120 if i < 2 else 340, anchor="w")
        self.history_tree.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self.history_tree.bind("<Double-1>", self.on_history_double_click)
        self.history_tree.bind("<Button-3>", self._show_history_context_menu)

        self.history_menu = tk.Menu(self.root, tearoff=0)
        self.history_menu.add_command(label=self._t("history_menu_open"), command=self.on_history_open_file)
        self.history_menu.add_command(label=self._t("history_menu_folder"), command=self.on_history_show_in_folder)
        self.history_menu.add_command(label=self._t("history_menu_copy_url"), command=self.on_history_copy_url)
        self.history_menu.add_separator()
        self.history_menu.add_command(label=self._t("history_menu_remove"), command=self.on_history_delete_selected)

        hist_actions = ctk.CTkFrame(self.tab_history, fg_color="transparent")
        hist_actions.pack(side=tk.BOTTOM, fill=tk.X, padx=6, pady=(6, 12))

        self.open_file_button = ctk.CTkButton(
            hist_actions,
            text=self._t("history_open_file"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=28,
            command=self.on_history_open_file,
        )
        self.open_file_button.pack(side=tk.LEFT, padx=(0, 4))

        self.show_folder_button = ctk.CTkButton(
            hist_actions,
            text=self._t("history_show_in_folder"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=28,
            command=self.on_history_show_in_folder,
        )
        self.show_folder_button.pack(side=tk.LEFT, padx=(0, 4))

        self.repeat_button = ctk.CTkButton(
            hist_actions,
            text=self._t("history_repeat"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=28,
            command=self.on_history_repeat_button,
        )
        self.repeat_button.pack(side=tk.RIGHT)

        self.refresh_history_tree()

        # --- tab_queue ---
        self.queue_label = ctk.CTkLabel(
            self.tab_queue,
            text=self._t("queue_label"),
            font=("Segoe UI", 11, "bold"),
            text_color=APPLE_FG,
        )
        self.queue_label.pack(anchor="w", padx=6, pady=(6, 4))

        self.queue_text = ctk.CTkTextbox(
            self.tab_queue,
            fg_color=APPLE_INPUT_BG,
            text_color=APPLE_FG,
            font=("Consolas", 11),
            corner_radius=8,
        )
        self.queue_text.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 4))
        self.queue_drag_active = False
        self.queue_drag_moved = False
        self.queue_drag_start_line = None
        self.queue_drag_target_line = None
        self._update_queue_drop_indicator_style()
        self.queue_text.bind("<Button-1>", self._on_queue_drag_start, add="+")
        self.queue_text.bind("<B1-Motion>", self._on_queue_drag_motion, add="+")
        self.queue_text.bind("<ButtonRelease-1>", self._on_queue_drag_drop, add="+")

        queue_actions = ctk.CTkFrame(self.tab_queue, fg_color="transparent")
        queue_actions.pack(fill=tk.X, padx=6, pady=(0, 6))

        self.queue_start_button = ctk.CTkButton(
            queue_actions,
            text=self._t("queue_start"),
            fg_color=APPLE_ACCENT,
            hover_color=APPLE_ACCENT_HOVER,
            corner_radius=6,
            height=28,
            command=self.start_queue,
        )
        self.queue_start_button.pack(side=tk.LEFT, padx=(0, 4))

        self.queue_remove_button = ctk.CTkButton(
            queue_actions,
            text=self._t("queue_remove_selected"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=28,
            command=self.remove_selected_queue_lines,
        )
        self.queue_remove_button.pack(side=tk.LEFT, padx=(0, 4))

        self.queue_up_button = ctk.CTkButton(
            queue_actions,
            text=self._t("queue_move_up"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=28,
            command=self.move_selected_queue_lines_up,
        )
        self.queue_up_button.pack(side=tk.LEFT, padx=(0, 4))

        self.queue_down_button = ctk.CTkButton(
            queue_actions,
            text=self._t("queue_move_down"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=28,
            command=self.move_selected_queue_lines_down,
        )
        self.queue_down_button.pack(side=tk.LEFT, padx=(0, 4))

        self.queue_clear_button = ctk.CTkButton(
            queue_actions,
            text=self._t("queue_clear"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=28,
            command=self.clear_queue_lines,
        )
        self.queue_clear_button.pack(side=tk.LEFT, padx=(0, 4))

        self.queue_save_button = ctk.CTkButton(
            queue_actions,
            text=self._t("queue_save"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=28,
            command=self.save_queue_to_file,
        )
        self.queue_save_button.pack(side=tk.RIGHT, padx=(4, 0))

        self.queue_load_button = ctk.CTkButton(
            queue_actions,
            text=self._t("queue_load"),
            fg_color=APPLE_BTN_BG,
            hover_color=APPLE_BTN_HOVER,
            corner_radius=6,
            height=28,
            command=self.load_queue_from_file,
        )
        self.queue_load_button.pack(side=tk.RIGHT, padx=(0, 4))

        self.queue_menu = tk.Menu(self.root, tearoff=0)
        self.queue_menu.add_command(label=self._t("queue_menu_paste"), command=self._paste_to_queue)
        self.queue_menu.add_command(
            label=self._t("queue_menu_remove_selected"),
            command=self.remove_selected_queue_lines,
        )
        self.queue_menu.add_command(label=self._t("queue_menu_clear"), command=self.clear_queue_lines)
        self.queue_text.bind("<Button-3>", self._show_queue_menu)

        # Context menu for Entry
        self.entry_menu = tk.Menu(self.root, tearoff=0)
        self.entry_menu.add_command(label=self._t("entry_menu_clear"), command=self._clear_entry_selection)
        self.entry_menu.add_command(label=self._t("entry_menu_paste"), command=self._paste_to_entry)
        self.root.bind_class("Entry", "<Button-3>", self._show_entry_menu)

        # Tooltips
        theme_getter = lambda: self.theme_mode
        ToolTip(self.subs_enable_check, lambda: self._t("tooltip_subs_enable"), theme_getter)
        ToolTip(self.subs_lang_label, lambda: self._t("tooltip_subs_lang"), theme_getter)
        ToolTip(self.subs_auto_check, lambda: self._t("tooltip_subs_auto"), theme_getter)
        ToolTip(self.subs_srt_check, lambda: self._t("tooltip_subs_srt"), theme_getter)
        ToolTip(self.mode_combo, lambda: self._t("tooltip_mode_combo"), theme_getter)
        ToolTip(self.time_from_entry, lambda: self._t("tooltip_trim"), theme_getter)
        ToolTip(self.time_to_entry, lambda: self._t("tooltip_trim"), theme_getter)
        ToolTip(self.embed_metadata_check, lambda: self._t("tooltip_embed_metadata"), theme_getter)

        self._bind_shortcuts()
        self.root.bind("<Configure>", self._on_root_resize)
        self._apply_root_resize()

    def _global_key_handler(self, event):
        """
        Глобальный хендлер для Ctrl+V, который работает независимо от раскладки.
        Проверяем маску Ctrl и keycode клавиши V.
        """
        if event.state & 0x4:
            if event.keycode == 86:
                widget = event.widget
                if isinstance(widget, (tk.Entry, ttk.Entry, tk.Text)):
                    try:
                        widget.event_generate("<<Paste>>")
                    except Exception:
                        pass
                    return "break"
        return None

    # ---------- история: обработчики ----------

    def on_history_double_click(self, event=None):
        item = self.history_tree.selection()
        if not item:
            return
        idx = self.history_tree.index(item[0])
        if idx >= len(self.history):
            return
        entry = self.history[idx]
        self._apply_history_entry(entry)

    def on_history_repeat_button(self):
        item = self.history_tree.selection()
        if not item:
            messagebox.showinfo(self._t("history_title"), self._t("history_empty"))
            return
        idx = self.history_tree.index(item[0])
        if idx >= len(self.history):
            return
        entry = self.history[idx]
        self._apply_history_entry(entry)

    def _apply_history_entry(self, entry: dict):
        self.url_entry.delete(0, tk.END)
        self.url_entry.insert(0, entry.get("url", ""))

        mode = entry.get("mode", "video_mp4")
        self.mode_var.set(mode)
        self._apply_mode_to_flags()
        self._update_mode_combo_values()

    def _show_history_context_menu(self, event):
        row_id = self.history_tree.identify_row(event.y)
        if row_id:
            self.history_tree.selection_set(row_id)
            try:
                self.history_menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.history_menu.grab_release()

    def on_history_open_file(self):
        item = self.history_tree.selection()
        if not item:
            return
        idx = self.history_tree.index(item[0])
        if idx >= len(self.history):
            return
        entry = self.history[idx]
        file_path = entry.get("file_path") or entry.get("path")
        if file_path and os.path.exists(file_path):
            try:
                os.startfile(file_path)
            except Exception as e:
                messagebox.showerror(self._t("error_title"), str(e))
        else:
            messagebox.showwarning(
                self._t("error_title"),
                self._t("history_file_not_found").format(path=file_path or "—"),
            )

    def on_history_show_in_folder(self):
        item = self.history_tree.selection()
        if not item:
            return
        idx = self.history_tree.index(item[0])
        if idx >= len(self.history):
            return
        entry = self.history[idx]
        file_path = entry.get("file_path") or entry.get("path")
        if file_path and os.path.isfile(file_path):
            try:
                subprocess.Popen(["explorer", f"/select,{os.path.normpath(file_path)}"])
            except Exception:
                try:
                    os.startfile(os.path.dirname(file_path))
                except Exception as e:
                    messagebox.showerror(self._t("error_title"), str(e))
        elif file_path and os.path.isdir(file_path):
            try:
                os.startfile(file_path)
            except Exception as e:
                messagebox.showerror(self._t("error_title"), str(e))
        else:
            messagebox.showwarning(
                self._t("error_title"),
                self._t("history_file_not_found").format(path=file_path or "—"),
            )

    def on_history_copy_url(self):
        item = self.history_tree.selection()
        if not item:
            return
        idx = self.history_tree.index(item[0])
        if idx >= len(self.history):
            return
        url = self.history[idx].get("url", "")
        if url:
            self.root.clipboard_clear()
            self.root.clipboard_append(url)

    def on_history_delete_selected(self):
        item = self.history_tree.selection()
        if not item:
            return
        idx = self.history_tree.index(item[0])
        if idx < len(self.history):
            del self.history[idx]
            self.save_history()
            self.refresh_history_tree()

    def _clear_trim(self):
        self.time_from_var.set("")
        self.time_to_var.set("")

    # ---------- режим ----------

    def _update_mode_combo_values(self):
        values = [
            self._t("mode_video_mp4"),
            self._t("mode_video_webm"),
            self._t("mode_audio_mp3"),
            self._t("mode_audio_m4a"),
            self._t("mode_audio_opus"),
            self._t("mode_subtitles_only"),
        ]

        self.mode_combo.configure(values=values)

        key_to_label = {
            "video_mp4": self._t("mode_video_mp4"),
            "video_webm": self._t("mode_video_webm"),
            "audio_mp3": self._t("mode_audio_mp3"),
            "audio_m4a": self._t("mode_audio_m4a"),
            "audio_opus": self._t("mode_audio_opus"),
            "only_subtitles": self._t("mode_subtitles_only"),
        }
        current = self.mode_var.get()
        if current not in key_to_label:
            current = self._label_to_mode_key(current)
            self.mode_var.set(current)
        disp = key_to_label.get(current, self._t("mode_video_mp4"))
        self.mode_display_var.set(disp)
        self.mode_combo.set(disp)

    def _label_to_mode_key(self, label: str) -> str:
        mapping = {
            self._t("mode_video_mp4"): "video_mp4",
            self._t("mode_video_webm"): "video_webm",
            self._t("mode_audio_mp3"): "audio_mp3",
            self._t("mode_audio_m4a"): "audio_m4a",
            self._t("mode_audio_opus"): "audio_opus",
            self._t("mode_subtitles_only"): "only_subtitles",
        }
        return mapping.get(label, "video_mp4")

    def _apply_mode_to_flags(self):
        mode = self.mode_var.get()
        if mode == "video_mp4":
            self.video_container_var.set("mp4")
        elif mode == "video_webm":
            self.video_container_var.set("webm")
        elif mode == "audio_mp3":
            self.audio_container_var.set("mp3")
        elif mode == "audio_m4a":
            self.audio_container_var.set("m4a")
        elif mode == "audio_opus":
            self.audio_container_var.set("opus")
        elif mode == "only_subtitles":
            self.subs_enabled_var.set(True)

    def _on_mode_combo_change(self, choice=None):
        selected_label = choice or self.mode_display_var.get()
        self.mode_var.set(self._label_to_mode_key(selected_label))
        self._apply_mode_to_flags()
        self._sync_quality_controls()
        self.save_settings()

    def _on_auto_number_toggle(self):
        self.auto_number_files = self.auto_number_var.get()
        self.save_settings()

    def _on_subs_enable_toggle(self):
        self._sync_quality_controls()
        self.save_settings()

    def _sync_quality_controls(self):
        mode = self.mode_var.get()
        if mode.startswith("audio_"):
            # Audio mode: disable video quality and entire subtitles section
            self.video_quality_combo.configure(state="disabled")
            if hasattr(self, "video_quality_label"):
                self.video_quality_label.configure(text_color=APPLE_DISABLED)
            self.audio_quality_combo.configure(state="readonly")
            if hasattr(self, "audio_quality_label"):
                self.audio_quality_label.configure(text_color=APPLE_FG)

            if hasattr(self, "subs_group_label"):
                self.subs_group_label.configure(text_color=APPLE_DISABLED)
            self.subs_enable_check.configure(state="disabled")
            if hasattr(self, "subs_lang_label"):
                self.subs_lang_label.configure(text_color=APPLE_DISABLED)
            if hasattr(self, "subs_lang_combo"):
                self.subs_lang_combo.configure(state="disabled")
            self.subs_auto_check.configure(state="disabled")
            self.subs_srt_check.configure(state="disabled")

        elif mode == "only_subtitles":
            # Only subtitles: disable video & audio quality, enable subtitles
            self.video_quality_combo.configure(state="disabled")
            if hasattr(self, "video_quality_label"):
                self.video_quality_label.configure(text_color=APPLE_DISABLED)
            self.audio_quality_combo.configure(state="disabled")
            if hasattr(self, "audio_quality_label"):
                self.audio_quality_label.configure(text_color=APPLE_DISABLED)

            if hasattr(self, "subs_group_label"):
                self.subs_group_label.configure(text_color=APPLE_FG)
            self.subs_enable_check.configure(state="normal")
            if hasattr(self, "subs_lang_label"):
                self.subs_lang_label.configure(text_color=APPLE_FG)
            if hasattr(self, "subs_lang_combo"):
                self.subs_lang_combo.configure(state="readonly")
            self.subs_auto_check.configure(state="normal")
            self.subs_srt_check.configure(state="normal")

        else:
            # Video mode: enable video, audio, and subtitles
            self.video_quality_combo.configure(state="readonly")
            if hasattr(self, "video_quality_label"):
                self.video_quality_label.configure(text_color=APPLE_FG)
            self.audio_quality_combo.configure(state="readonly")
            if hasattr(self, "audio_quality_label"):
                self.audio_quality_label.configure(text_color=APPLE_FG)

            if hasattr(self, "subs_group_label"):
                self.subs_group_label.configure(text_color=APPLE_FG)
            self.subs_enable_check.configure(state="normal")

            subs_enabled = self.subs_enabled_var.get()
            sub_state = "normal" if subs_enabled else "disabled"
            sub_combo_state = "readonly" if subs_enabled else "disabled"
            sub_text_color = APPLE_FG if subs_enabled else APPLE_DISABLED

            if hasattr(self, "subs_lang_label"):
                self.subs_lang_label.configure(text_color=sub_text_color)
            if hasattr(self, "subs_lang_combo"):
                self.subs_lang_combo.configure(state=sub_combo_state)
            self.subs_auto_check.configure(state=sub_state)
            self.subs_srt_check.configure(state=sub_state)

    # ---------- вспомогательные ----------

    def _open_download_dir(self):
        if os.name == "nt":
            try:
                os.startfile(self.default_download_dir)
            except Exception as e:
                self.log(self._t("open_folder_fail").format(err=e), tag="err")
        else:
            self.log(self._t("open_folder_non_windows"), tag="warn")

    def _change_download_dir(self):
        folder = filedialog.askdirectory(title=self._t("choose_download_folder"))
        if folder:
            self.default_download_dir = folder
            self.download_dir_label.configure(text=folder)
            self.log(self._t("log_download_folder_updated").format(path=folder), tag="ok")
            self.save_settings()

    def log(self, message: str, tag: str | None = None):
        if message is None:
            return
        message = str(message).strip()
        if not message:
            return

        if "[download]" in message and "%" in message:
            now = time.time()
            if message == self._last_progress_log and (now - self._last_progress_log_ts) < 0.6:
                return
            self._last_progress_log = message
            self._last_progress_log_ts = now

        # Normalize emoji prefixes
        raw = message
        message = (
            message.replace("ℹ️ ", "")
            .replace("✅ ", "")
            .replace("⚠️ ", "")
            .replace("⚠ ", "")
            .replace("❌ ", "")
        )

        ts = datetime.now().strftime("%H:%M:%S")

        level = "INFO"
        if tag in ("ok", "warn", "err"):
            level = tag.upper()
        else:
            u = raw.upper()
            mu = message.upper()
            if mu.lstrip().startswith("ERROR:") or "TRACEBACK" in mu or "SIGN IN TO CONFIRM" in mu:
                level = "ERR"
                tag = "err"
            elif mu.lstrip().startswith("WARNING:"):
                level = "WARN"
                tag = "warn"
            elif u.startswith("▶ ") or "▶ COMMAND" in u or "▶ КОМАНДА" in u:
                level = "CMD"
                tag = "cmd"
            elif "[OK]" in u:
                level = "OK"
                tag = "ok"
                message = message.replace("[OK]", "").strip()
            elif "[WARN]" in u:
                level = "WARN"
                tag = "warn"
                message = message.replace("[WARN]", "").strip()
            elif "[ERR]" in u:
                level = "ERR"
                tag = "err"
                message = message.replace("[ERR]", "").strip()
            elif "[INFO]" in u:
                level = "INFO"
                message = message.replace("[INFO]", "").strip()

        header = f"[{ts}] {level:<4} "

        self.log_text.configure(state="normal")
        try:
            self.log_text.insert("end", header, "ts")
            nl = chr(10)
            if tag:
                self.log_text.insert("end", message + nl, tag)
            else:
                self.log_text.insert("end", message + nl)
            self.log_text.see("end")
        finally:
            self.log_text.configure(state="disabled")

    def warn_once(self, key: str, message: str, every_s: float = 5.0):
        try:
            now = time.time()
            last = float(self._warn_throttle.get(key, 0.0))
            if (now - last) < float(every_s):
                return
            self._warn_throttle[key] = now
        except Exception:
            pass
        self.log(message, tag="warn")

    def open_settings(self):
        if getattr(self, "_settings_dialog", None) is not None:
            try:
                if self._settings_dialog.winfo_exists():
                    self._settings_dialog.focus()
                    self._settings_dialog.lift()
                    return
            except Exception:
                pass
        self._settings_dialog = SettingsDialog(self.root, self)

    def clear_logs(self):
        self.log_text.configure(state="normal")
        try:
            self.log_text.delete("1.0", "end")
            self.log(self._t("log_startup_ready"), tag="ok")
        finally:
            self.log_text.configure(state="disabled")

    def copy_logs(self):
        text = self.log_text.get("1.0", "end").strip()
        if not text:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log("✅ Log copied to clipboard", tag="ok")

    def get_ffmpeg_path(self):
        return env_service.get_ffmpeg_path(self)

    def get_executable_dir(self) -> str:
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def get_ytdlp_cmd(self) -> list[str]:
        return env_service.get_ytdlp_cmd(self)

    def _describe_ytdlp_source(self, ytdlp_cmd: list[str]) -> str:
        return env_service.describe_ytdlp_source(self, ytdlp_cmd)

    def get_cookies_path(self):
        return env_service.get_cookies_path(self)

    # ---------- статус окружения ----------
    def check_environment(self, verbose: bool = False):
        env_service.check_environment(self, verbose=verbose)

    def list_formats(self):
        url = self.url_entry.get().strip()
        download_service.list_formats(self, url)

    # ---------- превью ----------

    def fetch_video_info(self, url: str) -> dict | None:
        return preview_service.fetch_video_info(self, url)

    def update_preview_from_info(self, info: dict | None):
        preview_service.update_preview_from_info(self, info)

    def show_thumbnail_from_url(self, url: str):
        preview_service.show_thumbnail_from_url(self, url)

    def refresh_preview_thumbnail(self):
        preview_service.refresh_thumbnail_size(self)

    def _open_thumbnail_popup(self, event=None):
        if not self.thumb_full_image:
            return
        top = tk.Toplevel(self.root)
        top.title("Preview")
        lbl = ttk.Label(top, image=self.thumb_full_image)
        lbl.pack(padx=10, pady=10)

    # ---------- URL изменение ----------

    def _update_preview(self):
        url = self.url_entry.get().strip()
        if not url:
            if self.preview_status_label:
                self.preview_status_label.configure(text=self._t("preview_status_idle"))
            return

        self.preview_request_id += 1
        req_id = self.preview_request_id
        if self.preview_status_label:
            self.preview_status_label.configure(text=self._t("preview_status_loading"))

        def worker():
            info = self.fetch_video_info(url)

            def apply_if_latest():
                if req_id != self.preview_request_id:
                    return
                self.update_preview_from_info(info)
                if self.preview_status_label:
                    if info:
                        self.preview_status_label.configure(text=self._t("preview_status_ready"))
                    else:
                        self.preview_status_label.configure(text=self._t("preview_status_error"))

            self._enqueue_ui(apply_if_latest)

        threading.Thread(target=worker, daemon=True).start()

    # ---------- контекстное меню Entry ----------

    def _show_entry_menu(self, event):
        self._context_entry = event.widget
        try:
            self.entry_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.entry_menu.grab_release()

    def _clear_entry_selection(self):
        if hasattr(self, "_context_entry") and self._context_entry:
            self._context_entry.delete(0, tk.END)

    def _paste_to_entry(self):
        if hasattr(self, "_context_entry") and self._context_entry:
            try:
                text = self.root.clipboard_get()
            except tk.TclError:
                return
            self._context_entry.insert(tk.INSERT, text)

    def _show_queue_menu(self, event):
        try:
            self.queue_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.queue_menu.grab_release()

    def _paste_to_queue(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if not text:
            return
        existing = self.queue_text.get("1.0", "end").strip()
        merged = f"{existing}\n{text}" if existing else text
        self.queue_text.delete("1.0", "end")
        self.queue_text.insert("1.0", merged + "\n")

    # ---------- хоткеи ----------

    def _bind_shortcuts(self):
        self.root.bind("<Return>", self._on_enter)
        self.root.bind("<Escape>", self._on_esc)
        self.root.bind("<Control-l>", self._focus_url_entry)
        self.root.bind("<Control-L>", self._focus_url_entry)
        for cls in ("Entry", "Text", "CTkEntry"):
            self.root.bind_class(cls, "<Control-v>", self._on_ctrl_v)
            self.root.bind_class(cls, "<Control-V>", self._on_ctrl_v)
        self.root.bind_all("<Key>", self._global_key_handler, add="+")

    def change_theme(self, mode: str):
        mode = mode.lower()
        if getattr(self, "_theme_changing", False):
            return
        self._theme_changing = True
        try:
            self.theme_mode = mode
            ctk.set_appearance_mode(mode)
            # Explicitly update ttk.Style for standard widgets like Treeview manually:
            self.update_ttk_styles(mode)
        except Exception as e:
            print(f"Theme switch warning: {e}")
        finally:
            self._theme_changing = False

    def update_ttk_styles(self, mode: str):
        try:
            style = ttk.Style()
            is_dark = mode.lower() == "dark"

            surface = "#242426" if is_dark else "#FFFFFF"
            surface2 = "#2C2C2E" if is_dark else "#E5E5EA"
            border = "#38383A" if is_dark else "#D1D1D6"
            fg = "#FFFFFF" if is_dark else "#000000"
            select_bg = "#0A84FF" if is_dark else "#007AFF"

            try:
                style.theme_use("clam")
            except Exception:
                pass

            # Remove outer rectangular outline by stripping field border element
            try:
                style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
            except Exception:
                pass

            style.configure(
                "Treeview",
                background=surface,
                fieldbackground=surface,
                foreground=fg,
                bordercolor=surface,
                lightcolor=surface,
                darkcolor=surface,
                borderwidth=0,
                relief="flat",
                rowheight=28,
            )
            style.configure(
                "Treeview.Heading",
                background=surface2,
                foreground=fg,
                bordercolor=surface2,
                lightcolor=surface2,
                darkcolor=surface2,
                borderwidth=0,
                font=("Segoe UI", 10, "bold"),
                relief="flat",
            )
            style.map(
                "Treeview",
                background=[("selected", select_bg)],
                foreground=[("selected", "#FFFFFF")],
            )

            if hasattr(self, "queue_menu"):
                self.queue_menu.configure(background=surface, foreground=fg, activebackground=select_bg, activeforeground="#FFFFFF")
            if hasattr(self, "entry_menu"):
                self.entry_menu.configure(background=surface, foreground=fg, activebackground=select_bg, activeforeground="#FFFFFF")
            if hasattr(self, "history_menu"):
                self.history_menu.configure(background=surface, foreground=fg, activebackground=select_bg, activeforeground="#FFFFFF")
            if hasattr(self, "log_text"):
                self.log_text.tag_config("cmd", foreground=select_bg)
            if hasattr(self, "_update_queue_drop_indicator_style"):
                self._update_queue_drop_indicator_style()
        except Exception as e:
            print(f"Update ttk styles warning: {e}")

    def _apply_theme_style(self):
        self.update_ttk_styles(self.theme_mode)

    def _on_root_resize(self, event=None):
        if event is not None and event.widget is not self.root:
            return
        if getattr(self, "_resizing_running", False) or getattr(self, "_theme_changing", False):
            return

        try:
            current_size = (self.root.winfo_width(), self.root.winfo_height())
        except Exception:
            return

        if current_size == getattr(self, "_last_root_size", None):
            return
        self._last_root_size = current_size

        if hasattr(self, "_resize_after_id") and self._resize_after_id is not None:
            try:
                self.root.after_cancel(self._resize_after_id)
            except Exception:
                pass

        self._resize_after_id = self.root.after(30, self._apply_root_resize)

    def _schedule_thumbnail_refresh(self):
        if getattr(self, "_resizing", False) or getattr(self, "_theme_changing", False):
            return
        if self._thumb_resize_after_id is not None:
            try:
                self.root.after_cancel(self._thumb_resize_after_id)
            except Exception:
                pass
        self._thumb_resize_after_id = self.root.after(220, self._run_scheduled_thumbnail_refresh)

    def _run_scheduled_thumbnail_refresh(self):
        self._thumb_resize_after_id = None
        if getattr(self, "_theme_changing", False) or not getattr(self, "thumb_source_image", None):
            return
        try:
            self.refresh_preview_thumbnail()
        except Exception:
            pass

    def _apply_root_resize(self):
        self._resize_after_id = None
        if not hasattr(self, "preview_title_label") or self.preview_title_label is None:
            return

        if getattr(self, "_resizing_running", False) or getattr(self, "_theme_changing", False):
            return
        self._resizing_running = True
        try:
            step = 24
            preview_width_raw = max(260, self.preview_title_label.winfo_toplevel().winfo_width() // 3)
            preview_width = (preview_width_raw // step) * step
            if preview_width != self._last_preview_wrap:
                self.preview_title_label.configure(wraplength=preview_width)
                self.preview_channel_label.configure(wraplength=preview_width)
                self._last_preview_wrap = preview_width
                self._schedule_thumbnail_refresh()

            folder_width_raw = max(280, (self.preview_title_label.winfo_toplevel().winfo_width() // 2) - 40)
            folder_width = (folder_width_raw // step) * step
            if folder_width != self._last_dir_wrap:
                self.download_dir_label.configure(wraplength=folder_width)
                self._last_dir_wrap = folder_width
        except Exception:
            pass
        finally:
            self._resizing_running = False

    def _enqueue_ui(self, callback):
        self.ui_queue.put(callback)

    def _process_ui_queue(self):
        processed = 0
        max_per_tick = 120
        while True:
            if processed >= max_per_tick:
                break
            try:
                callback = self.ui_queue.get_nowait()
            except thread_queue.Empty:
                break
            try:
                callback()
                processed += 1
            except Exception as e:
                self.warn_once("ui_callback_error", f"⚠️ UI callback error: {e}", every_s=3.0)
        try:
            if self.root.winfo_exists():
                self.root.after(15, self._process_ui_queue)
        except Exception:
            pass

    def _on_enter(self, event):
        self.download_video()

    def _on_esc(self, event):
        if self.download_thread and self.download_thread.is_alive():
            self.cancel_download()

    def _on_ctrl_v(self, event):
        w = event.widget
        try:
            w.event_generate("<<Paste>>")
        except Exception:
            pass
        return "break"

    def _focus_url_entry(self, event=None):
        self.url_entry.focus_set()
        self.url_entry.select_range(0, tk.END)
        return "break"

    def _paste_url(self):
        try:
            text = self.root.clipboard_get().strip()
        except tk.TclError:
            return
        if text:
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, text)
            self._on_url_key()

    def _clear_url(self):
        self.url_entry.delete(0, tk.END)
        self._on_url_key()

    # ---------- скачивание (одиночное) ----------

    def download_video(self, from_queue=False, url_override=None, mode_override=None):
        download_service.download_video(self, from_queue, url_override, mode_override)

    def cancel_download(self):
        download_service.cancel_download(self)

    # ---------- очередь ----------

    def start_queue(self):
        if self.queue_running:
            messagebox.showinfo(self._t("tab_queue"), self._t("queue_running"))
            return

        text = self.queue_text.get("1.0", "end")
        urls = [line.strip() for line in text.splitlines() if line.strip()]
        if not urls:
            return

        self.queue_running = True
        self.queue_start_button.configure(state="disabled")
        self.status_label.configure(text=self._t("queue_status").format(cur=0, total=len(urls)))

        base_mode = self.mode_var.get()
        self.queue_thread = threading.Thread(target=self._queue_worker, args=(urls, base_mode), daemon=True)
        self.queue_thread.start()

    def _queue_worker(self, urls, base_mode):
        total = len(urls)
        for i, url in enumerate(urls, start=1):
            if self.cancel_requested:
                break

            def update_status(i=i, url=url):
                self.status_label.configure(text=self._t("queue_status").format(cur=i, total=total))
                self.log(f"[{i}/{total}] {url}")
            self._enqueue_ui(update_status)

            start_evt = threading.Event()

            def start_item_download():
                self.download_video(from_queue=True, url_override=url, mode_override=base_mode)
                start_evt.set()

            self._enqueue_ui(start_item_download)
            start_evt.wait()
            if self.download_thread:
                self.download_thread.join()

        def finish():
            self.queue_running = False
            self.queue_start_button.configure(state="normal")
            self.status_label.configure(text=self._t("queue_done"))
            self.download_button.configure(state="normal", text=self._t("download"))
            self.cancel_button.configure(state="disabled")
        self._enqueue_ui(finish)

    def remove_selected_queue_lines(self):
        try:
            selected = self.queue_text.tag_ranges("sel")
            if not selected:
                messagebox.showinfo(self._t("tab_queue"), self._t("queue_empty_select"))
                return
            start = self.queue_text.index("sel.first linestart")
            end = self.queue_text.index("sel.last lineend+1c")
            self.queue_text.delete(start, end)
        except tk.TclError:
            messagebox.showinfo(self._t("tab_queue"), self._t("queue_empty_select"))

    def move_selected_queue_lines_up(self):
        try:
            _ = self.queue_text.tag_ranges("sel")
            start_line = int(self.queue_text.index("sel.first").split(".")[0])
            end_idx = self.queue_text.index("sel.last")
            end_line, end_col = map(int, end_idx.split("."))
            if end_col == 0 and end_line > start_line:
                end_line -= 1
        except tk.TclError:
            messagebox.showinfo(self._t("tab_queue"), self._t("queue_empty_select"))
            return

        if start_line <= 1:
            return

        lines = self.queue_text.get("1.0", "end").splitlines()
        block = lines[start_line - 1:end_line]
        if not block:
            return
        moved = lines[: start_line - 2] + block + [lines[start_line - 2]] + lines[end_line:]
        self.queue_text.delete("1.0", "end")
        self.queue_text.insert("1.0", "\n".join(moved) + ("\n" if moved else ""))
        self.queue_text.tag_remove("sel", "1.0", "end")
        self.queue_text.tag_add(
            "sel",
            f"{start_line - 1}.0",
            f"{start_line - 1 + len(block)}.0",
        )
        self.queue_text.mark_set("insert", f"{start_line - 1}.0")

    def move_selected_queue_lines_down(self):
        try:
            _ = self.queue_text.tag_ranges("sel")
            start_line = int(self.queue_text.index("sel.first").split(".")[0])
            end_idx = self.queue_text.index("sel.last")
            end_line, end_col = map(int, end_idx.split("."))
            if end_col == 0 and end_line > start_line:
                end_line -= 1
        except tk.TclError:
            messagebox.showinfo(self._t("tab_queue"), self._t("queue_empty_select"))
            return

        lines = self.queue_text.get("1.0", "end").splitlines()
        if end_line >= len(lines):
            return

        block = lines[start_line - 1:end_line]
        if not block:
            return
        moved = lines[: start_line - 1] + [lines[end_line]] + block + lines[end_line + 1:]
        self.queue_text.delete("1.0", "end")
        self.queue_text.insert("1.0", "\n".join(moved) + ("\n" if moved else ""))
        self.queue_text.tag_remove("sel", "1.0", "end")
        self.queue_text.tag_add(
            "sel",
            f"{start_line + 1}.0",
            f"{start_line + 1 + len(block)}.0",
        )
        self.queue_text.mark_set("insert", f"{start_line + 1}.0")

    def _selected_queue_block_bounds(self):
        try:
            start_line = int(self.queue_text.index("sel.first").split(".")[0])
            end_idx = self.queue_text.index("sel.last")
            end_line, end_col = map(int, end_idx.split("."))
            if end_col == 0 and end_line > start_line:
                end_line -= 1
            return start_line, end_line
        except tk.TclError:
            return None, None

    def _on_queue_drag_start(self, event):
        self._clear_queue_drop_indicator()
        start_line, end_line = self._selected_queue_block_bounds()
        click_line = int(self.queue_text.index(f"@{event.x},{event.y}").split(".")[0])
        self.queue_drag_start_line = click_line
        self.queue_drag_moved = False
        self.queue_drag_active = (
            start_line is not None and end_line is not None and start_line <= click_line <= end_line
        )

    def _on_queue_drag_motion(self, event):
        if not self.queue_drag_active:
            return
        cur_line = int(self.queue_text.index(f"@{event.x},{event.y}").split(".")[0])
        if self.queue_drag_start_line is not None and cur_line != self.queue_drag_start_line:
            self.queue_drag_moved = True
        self._show_queue_drop_indicator(cur_line)

    def _on_queue_drag_drop(self, event):
        if not self.queue_drag_active or not self.queue_drag_moved:
            self.queue_drag_active = False
            self._clear_queue_drop_indicator()
            return

        start_line, end_line = self._selected_queue_block_bounds()
        if start_line is None:
            self.queue_drag_active = False
            self._clear_queue_drop_indicator()
            return

        lines = self.queue_text.get("1.0", "end").splitlines()
        if not lines:
            self.queue_drag_active = False
            self._clear_queue_drop_indicator()
            return

        drop_line = int(self.queue_text.index(f"@{event.x},{event.y}").split(".")[0])
        drop_line = max(1, min(drop_line, len(lines) + 1))

        block = lines[start_line - 1:end_line]
        remaining = lines[:start_line - 1] + lines[end_line:]

        insert_pos = drop_line - 1
        if drop_line > end_line:
            insert_pos -= (end_line - start_line + 1)
        insert_pos = max(0, min(insert_pos, len(remaining)))

        moved = remaining[:insert_pos] + block + remaining[insert_pos:]
        new_start = insert_pos + 1

        self.queue_text.delete("1.0", "end")
        self.queue_text.insert("1.0", "\n".join(moved) + ("\n" if moved else ""))
        self.queue_text.tag_remove("sel", "1.0", "end")
        self.queue_text.tag_add("sel", f"{new_start}.0", f"{new_start + len(block)}.0")
        self.queue_text.mark_set("insert", f"{new_start}.0")
        self.queue_drag_active = False
        self._clear_queue_drop_indicator()

    def _show_queue_drop_indicator(self, line_no):
        self._clear_queue_drop_indicator()
        lines = self.queue_text.get("1.0", "end").splitlines()
        if not lines:
            return
        clamped = max(1, min(line_no, len(lines)))
        self.queue_drag_target_line = clamped
        self.queue_text.tag_add("queue_drop_target", f"{clamped}.0", f"{clamped}.0 lineend+1c")

    def _clear_queue_drop_indicator(self):
        self.queue_drag_target_line = None
        self.queue_text.tag_remove("queue_drop_target", "1.0", "end")

    def _update_queue_drop_indicator_style(self):
        bg = "#2f5f8a" if self.theme_mode == "dark" else "#b7d7ff"
        self.queue_text.tag_config("queue_drop_target", background=bg)

    def clear_queue_lines(self):
        self.queue_text.delete("1.0", "end")

    def save_queue_to_file(self):
        path = filedialog.asksaveasfilename(
            title=self._t("queue_save"),
            defaultextension=".txt",
            filetypes=[(self._t("queue_filetypes"), "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            content = self.queue_text.get("1.0", "end").strip()
            with open(path, "w", encoding="utf-8") as f:
                f.write(content + ("\n" if content else ""))
            self.log(f"✅ {self._t('queue_save')}: {path}", tag="ok")
        except Exception as e:
            self.log(f"⚠️ {self._t('queue_save')} failed: {e}", tag="err")

    def load_queue_from_file(self):
        path = filedialog.askopenfilename(
            title=self._t("queue_load"),
            filetypes=[(self._t("queue_filetypes"), "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.queue_text.delete("1.0", "end")
            self.queue_text.insert("1.0", content)
            self.log(f"✅ {self._t('queue_load')}: {path}", tag="ok")
        except Exception as e:
            self.log(f"⚠️ {self._t('queue_load')} failed: {e}", tag="err")

    def on_closing(self):
        try:
            self.root.destroy()
        finally:
            import os
            os._exit(0)


if __name__ == "__main__":
    root = ctk.CTk()
    root.title("YouTube Converter")
    app = YouTubeConverterApp(root)
    root.mainloop()
