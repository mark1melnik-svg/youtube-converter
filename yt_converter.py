import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext, StringVar
from datetime import datetime
from pathlib import Path
import os
import sys
import subprocess
import threading
import re
import queue as thread_queue
import time

import sv_ttk
from i18n import get_translations
from settings_dialog import SettingsDialog
from services import config_service, download_service, env_service, preview_service
from tooltip import ToolTip


# ---------- Основное приложение ----------

class YouTubeConverterApp:
    def __init__(self, root):
        self.root = root

        self.lang = "ru"
        self.trans = get_translations()

        self.root.title(self._t("app_title") + " v1.0.0")
        self.root.geometry("1180x760")
        self.root.minsize(980, 620)
        self.root.option_add("*Font", "{Segoe UI} 10")

        self.default_download_dir = str(Path.home() / "Downloads")
        self.cookies_dir = None
        self.open_folder_after_download = False
        # Use a robust default in case title is missing due to YouTube restrictions
        self.filename_template = "%(title,id)s"
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

        self.current_process: subprocess.Popen | None = None
        self.download_thread: threading.Thread | None = None
        self.cancel_requested = False

        self.progress_re = re.compile(r'(\d+(?:\.\d+)?)%')  # XX.X%


        self.theme_mode = "dark"

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

        self.load_settings()
        self.load_history()

        if self.theme_mode == "light":
            sv_ttk.set_theme("light")
        else:
            sv_ttk.set_theme("dark")

        self.setup_ui()
        self._apply_theme_style()
        self._apply_mode_to_flags()
        self._sync_quality_controls()
        self.root.after(50, self._process_ui_queue)
        self.check_environment()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _on_url_key(self, event=None):
        """Очистка + debounce для автопревью."""
        self.preview_request_id += 1
        self.thumb_image = None
        self.thumb_full_image = None
        self.thumb_label.config(image="")
        if self.preview_title_label:
            self.preview_title_label.config(text="")
        if self.preview_channel_label:
            self.preview_channel_label.config(text="")
        if self.preview_status_label:
            self.preview_status_label.config(text=self._t("preview_status_idle"))

        if hasattr(self, "_preview_timeout"):
            self.root.after_cancel(self._preview_timeout)
        self._preview_timeout = self.root.after(800, self._update_preview)


    # ---------- i18n ----------
    def _t(self, key: str):
        return self.trans.get(self.lang, {}).get(key, key)
    def retranslate_ui(self):
        self.root.title(self._t("app_title") + " v1.0.0")

        self.url_label.config(text=self._t("url"))
        self.mode_label.config(text=self._t("mode"))
        self._update_mode_combo_values()
        self.paste_url_button.config(text=self._t("paste_url"))
        self.clear_url_button.config(text=self._t("clear_url"))

        self.download_button.config(text=self._t("download"))
        self.cancel_button.config(text=self._t("cancel"))
        self.settings_button.config(text=self._t("settings"))
        self.status_label.config(text=self._t("status_ready"))

        self.notebook.tab(self.tab_main, text=self._t("tab_main"))
        self.notebook.tab(self.tab_adv, text=self._t("tab_adv"))
        self.notebook.tab(self.tab_log, text=self._t("tab_log"))
        self.notebook.tab(self.tab_history, text=self._t("tab_history"))
        self.notebook.tab(self.tab_queue, text=self._t("tab_queue"))

        self.download_folder_label.config(text=self._t("download_folder"))
        self.open_folder_button.config(text=self._t("open_folder"))
        self.change_folder_button.config(text=self._t("change_folder"))

        self.subs_group_label.config(text=self._t("subs_group"))
        self.quality_group_label.config(text=self._t("quality_group"))
        self.video_quality_label.config(text=self._t("video_quality"))
        self.audio_quality_label.config(text=self._t("audio_quality"))
        self.auto_number_check.config(text=self._t("auto_number_files"))
        self.subs_enable_check.config(text=self._t("subs_enable"))
        self.subs_lang_label.config(text=self._t("subs_lang"))
        self.subs_auto_check.config(text=self._t("subs_auto"))
        self.subs_srt_check.config(text=self._t("subs_srt"))

        self.format_hint_label.config(text=self._t("format_hint"))

        self.log_label.config(text=self._t("log_label"))
        self.clear_log_button.config(text=self._t("clear_log"))
        self.copy_log_button.config(text=self._t("copy_log"))

        self.history_label.config(text=self._t("history_title"))
        self.repeat_button.config(text=self._t("history_repeat"))

        self.queue_label.config(text=self._t("queue_label"))
        self.queue_start_button.config(text=self._t("queue_start"))
        self.queue_remove_button.config(text=self._t("queue_remove_selected"))
        self.queue_up_button.config(text=self._t("queue_move_up"))
        self.queue_down_button.config(text=self._t("queue_move_down"))
        self.queue_clear_button.config(text=self._t("queue_clear"))
        self.queue_save_button.config(text=self._t("queue_save"))
        self.queue_load_button.config(text=self._t("queue_load"))
        self.queue_menu.entryconfigure(0, label=self._t("queue_menu_paste"))
        self.queue_menu.entryconfigure(1, label=self._t("queue_menu_remove_selected"))
        self.queue_menu.entryconfigure(2, label=self._t("queue_menu_clear"))
        self.preview_header_label.config(text=self._t("preview_panel_title"))
        self.preview_status_label.config(text=self._t("preview_status_idle"))
        self.check_env_button.config(text=self._t("check_environment"))
        self.list_formats_button.config(text=self._t("list_formats"))
        if hasattr(self, "entry_menu"):
            self.entry_menu.entryconfigure(0, label=self._t("entry_menu_clear"))
            self.entry_menu.entryconfigure(1, label=self._t("entry_menu_paste"))

        cols = self._t("history_cols")
        for i, col in enumerate(cols):
            self.history_tree.heading(f"#{i}", text=col)

    # ---------- настройки / история ----------

    def _get_output_template_with_choice(self, download_path: str, ext: str = "%(ext)s"):
        base_tmpl = (self.filename_template or "%(title,id)s").strip()
        if not base_tmpl:
            base_tmpl = "%(title,id)s"
        base_path = Path(download_path)
        if self.auto_number_files:
            return str(base_path / f"{base_tmpl} (%(autonumber)s).{ext}")
        return str(base_path / f"{base_tmpl}.{ext}")

    def load_settings(self):
        config_service.load_settings(self)

    def save_settings(self):
        config_service.save_settings(self)

    def load_history(self):
        config_service.load_history(self)

    def save_history(self):
        config_service.save_history(self)

    def add_history_entry(self, url: str, mode: str, download_path: str):
        config_service.add_history_entry(self, url, mode, download_path)

    def refresh_history_tree(self):
        config_service.refresh_history_tree(self)

    # ---------- UI ----------

    def setup_ui(self):
        container = ttk.Frame(self.root)
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        container.columnconfigure(0, weight=1)

        card = ttk.Frame(container)
        card.grid(row=0, column=0, sticky="nwe")
        card.columnconfigure(1, weight=1)

        self.url_label = ttk.Label(card, text=self._t("url"))
        self.url_label.grid(row=0, column=0, sticky="w", padx=(0, 5))
        self.url_entry = ttk.Entry(card)
        self.url_entry.grid(row=0, column=1, sticky="we", padx=(0, 5))
        self.url_entry.bind("<Key>", self._on_url_key)
        self.download_button = ttk.Button(card, text=self._t("download"), command=self.download_video)
        self.download_button.grid(row=0, column=2, padx=(0, 5))
        self.cancel_button = ttk.Button(card, text=self._t("cancel"), command=self.cancel_download, state="disabled")
        self.cancel_button.grid(row=0, column=3)

        self.mode_label = ttk.Label(card, text=self._t("mode"))
        self.mode_label.grid(row=1, column=0, sticky="w", padx=(0, 5), pady=(6, 0))

        self.mode_combo = ttk.Combobox(
            card,
            textvariable=self.mode_display_var,
            state="readonly",
            width=24,
            style="Settings.TCombobox",
        )
        self._update_mode_combo_values()
        self.mode_combo.grid(row=1, column=1, sticky="we", padx=(0, 5), pady=(6, 0))
        self.mode_combo.bind("<<ComboboxSelected>>", self._on_mode_change)

        self.paste_url_button = ttk.Button(card, text=self._t("paste_url"), command=self._paste_url)
        self.paste_url_button.grid(row=1, column=2, padx=(0, 5), pady=(6, 0))
        self.clear_url_button = ttk.Button(card, text=self._t("clear_url"), command=self._clear_url)
        self.clear_url_button.grid(row=1, column=3, padx=(0, 5), pady=(6, 0))

        self.settings_button = ttk.Button(card, text=self._t("settings"), command=self.open_settings)
        self.settings_button.grid(row=1, column=4, padx=(0, 5), pady=(6, 0))

        self.status_label = ttk.Label(card, text=self._t("status_ready"), font=("Segoe UI", 9))
        self.status_label.grid(row=2, column=0, columnspan=5, sticky="w", pady=(12, 4))

        self._progress_shell = ttk.Frame(card, style="ProgShell.TFrame")
        self._progress_shell.grid(row=3, column=0, columnspan=5, sticky="we", pady=(0, 2))
        self._progress_shell.columnconfigure(0, weight=1)
        self.progress = ttk.Progressbar(
            self._progress_shell,
            orient=tk.HORIZONTAL,
            mode="determinate",
            maximum=100,
            value=0,
            style="App.Horizontal.TProgressbar",
        )
        self.progress.grid(row=0, column=0, sticky="we", padx=3, pady=5)

        sep = ttk.Separator(container, orient="horizontal")
        sep.grid(row=1, column=0, sticky="we", pady=8)

        bottom = ttk.Frame(container)
        bottom.grid(row=2, column=0, sticky="nsew")
        container.rowconfigure(2, weight=1)
        bottom.columnconfigure(0, weight=3)
        bottom.columnconfigure(1, weight=2)

        self.notebook = ttk.Notebook(bottom, style="App.TNotebook")
        self.notebook.grid(row=0, column=0, sticky="nsew", padx=(0, 8), pady=(0, 0))

        self.tab_main = ttk.Frame(self.notebook)
        self.tab_adv = ttk.Frame(self.notebook)
        self.tab_log = ttk.Frame(self.notebook)
        self.tab_history = ttk.Frame(self.notebook)
        self.tab_queue = ttk.Frame(self.notebook)

        self.notebook.add(self.tab_main, text=self._t("tab_main"))
        self.notebook.add(self.tab_adv, text=self._t("tab_adv"))
        self.notebook.add(self.tab_log, text=self._t("tab_log"))
        self.notebook.add(self.tab_history, text=self._t("tab_history"))
        self.notebook.add(self.tab_queue, text=self._t("tab_queue"))
        self.notebook.hide(self.tab_adv)

        preview_frame = ttk.Frame(bottom)
        preview_frame.grid(row=0, column=1, sticky="nsew")
        bottom.rowconfigure(0, weight=1)

        self.preview_header_label = ttk.Label(preview_frame, text=self._t("preview_panel_title"))
        self.preview_header_label.pack(anchor="w", pady=(0, 2))
        self.preview_status_label = ttk.Label(preview_frame, text=self._t("preview_status_idle"))
        self.preview_status_label.pack(anchor="w", pady=(0, 4))

        self.preview_title_label = ttk.Label(preview_frame, text="", wraplength=260, justify="left")
        self.preview_title_label.pack(anchor="w")

        self.preview_channel_label = ttk.Label(preview_frame, text="", wraplength=260, justify="left")
        self.preview_channel_label.pack(anchor="w", pady=(0, 4))

        self.thumb_label = ttk.Label(preview_frame)
        self.thumb_label.pack(anchor="center", expand=True)
        self.thumb_label.bind("<Button-1>", self._open_thumbnail_popup)

        sep2 = ttk.Separator(preview_frame, orient="horizontal")
        sep2.pack(fill="x", pady=6)

        # Environment status is shown on the main tab (bottom info block).

        self.check_env_button = ttk.Button(preview_frame, text=self._t("check_environment"), command=self.check_environment)
        self.check_env_button.pack(anchor="e", pady=(2, 0))

        self.list_formats_button = ttk.Button(preview_frame, text=self._t("list_formats"), command=self.list_formats)
        self.list_formats_button.pack(anchor="e", pady=(6, 0))

        # --- tab_main ---
        # Left block: quality, right block: subtitles.
        quality_frame = ttk.LabelFrame(self.tab_main, text=self._t("quality_group"), style="Settings.TLabelframe")
        quality_frame.grid(row=0, column=0, sticky="nsew", padx=(8, 4), pady=(10, 6))
        quality_frame.columnconfigure(1, weight=1)

        subs_frame = ttk.LabelFrame(self.tab_main, text=self._t("subs_group"), style="Settings.TLabelframe")
        subs_frame.grid(row=0, column=1, sticky="nsew", padx=(4, 8), pady=(10, 6))
        subs_frame.columnconfigure(1, weight=1)

        self.subs_group_label = subs_frame
        self.subs_enable_check = ttk.Checkbutton(
            subs_frame,
            text=self._t("subs_enable"),
            variable=self.subs_enabled_var,
            command=self.save_settings,
            style="Settings.TCheckbutton",
        )
        self.subs_enable_check.grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 2))

        self.subs_lang_label = ttk.Label(subs_frame, text=self._t("subs_lang"), style="Settings.TLabel")
        self.subs_lang_label.grid(row=1, column=0, sticky="w", padx=6, pady=(2, 2))
        subs_lang_combo = ttk.Combobox(
            subs_frame,
            textvariable=self.subs_lang_var,
            values=["auto", "ru", "en", "ru,en", "all"],
            state="readonly",
            width=14,
            style="Settings.TCombobox",
        )
        subs_lang_combo.grid(row=1, column=1, sticky="we", padx=6, pady=(2, 2))
        subs_lang_combo.bind("<<ComboboxSelected>>", lambda e: self.save_settings())

        self.subs_auto_check = ttk.Checkbutton(
            subs_frame,
            text=self._t("subs_auto"),
            variable=self.subs_auto_var,
            command=self.save_settings,
            style="Settings.TCheckbutton",
        )
        self.subs_auto_check.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 2))

        self.subs_srt_check = ttk.Checkbutton(
            subs_frame,
            text=self._t("subs_srt"),
            variable=self.subs_srt_var,
            command=self.save_settings,
            style="Settings.TCheckbutton",
        )
        self.subs_srt_check.grid(row=3, column=0, columnspan=2, sticky="w", padx=6, pady=(2, 6))

        self.quality_group_label = quality_frame
        self.video_quality_label = ttk.Label(quality_frame, text=self._t("video_quality"), style="Settings.TLabel")
        self.video_quality_label.grid(row=0, column=0, sticky="w", padx=6, pady=(6, 2))
        self.video_quality_combo = ttk.Combobox(
            quality_frame,
            textvariable=self.video_quality_var,
            values=["best", "2160", "1440", "1080", "720", "480", "360"],
            state="readonly",
            width=14,
            style="Settings.TCombobox",
        )
        self.video_quality_combo.grid(row=0, column=1, sticky="we", padx=6, pady=(6, 2))
        self.video_quality_combo.bind("<<ComboboxSelected>>", lambda e: self.save_settings())

        self.audio_quality_label = ttk.Label(quality_frame, text=self._t("audio_quality"), style="Settings.TLabel")
        self.audio_quality_label.grid(row=1, column=0, sticky="w", padx=6, pady=(2, 2))
        self.audio_quality_combo = ttk.Combobox(
            quality_frame,
            textvariable=self.audio_quality_var,
            values=["best", "320", "256", "192", "160", "128", "96"],
            state="readonly",
            width=14,
            style="Settings.TCombobox",
        )
        self.audio_quality_combo.grid(row=1, column=1, sticky="we", padx=6, pady=(2, 2))
        self.audio_quality_combo.bind("<<ComboboxSelected>>", lambda e: self.save_settings())

        self.auto_number_var = tk.BooleanVar(value=self.auto_number_files)
        self.auto_number_check = ttk.Checkbutton(
            quality_frame,
            text=self._t("auto_number_files"),
            variable=self.auto_number_var,
            command=self._on_auto_number_toggle,
            style="Settings.TCheckbutton",
        )
        self.auto_number_check.grid(row=2, column=0, columnspan=2, sticky="w", padx=6, pady=(4, 6))

        self.format_hint_label = ttk.Label(
            self.tab_main,
            text=self._t("format_hint"),
            justify="left",
            style="Hint.TLabel",
        )
        self.format_hint_label.grid(row=1, column=0, columnspan=2, sticky="we", padx=8, pady=(6, 4))

        self.download_folder_label = ttk.Label(self.tab_main, text=self._t("download_folder"))
        self.download_folder_label.grid(row=2, column=0, sticky="w", padx=8, pady=(8, 2))

        self.download_dir_label = ttk.Label(self.tab_main, text=self.default_download_dir)
        self.download_dir_label.grid(row=2, column=1, sticky="we", padx=8, pady=(8, 2))

        folder_actions = ttk.Frame(self.tab_main)
        folder_actions.grid(row=3, column=0, columnspan=2, sticky="we", padx=8, pady=(4, 6))
        folder_actions.columnconfigure(0, weight=1)
        folder_actions.columnconfigure(1, weight=1)

        self.open_folder_button = ttk.Button(folder_actions, text=self._t("open_folder"), command=self._open_download_dir)
        self.open_folder_button.grid(row=0, column=0, sticky="w")

        self.change_folder_button = ttk.Button(folder_actions, text=self._t("change_folder"), command=self._change_download_dir)
        self.change_folder_button.grid(row=0, column=1, sticky="e")

        info_frame = ttk.Frame(self.tab_main)
        info_frame.grid(row=4, column=0, columnspan=2, sticky="we", padx=8, pady=(0, 8))
        info_frame.columnconfigure(0, weight=1)
        info_frame.columnconfigure(1, weight=1)

        self.env_ytdlp_label = ttk.Label(info_frame, text="yt-dlp: ⏳")
        self.env_ytdlp_label.grid(row=0, column=0, sticky="w", pady=(0, 1))
        self.env_ffmpeg_label = ttk.Label(info_frame, text="ffmpeg: ⏳")
        self.env_ffmpeg_label.grid(row=1, column=0, sticky="w", pady=(0, 1))
        self.env_node_label = ttk.Label(info_frame, text="node: ⏳")
        self.env_node_label.grid(row=0, column=1, sticky="w", pady=(0, 1))
        self.env_cookies_label = ttk.Label(info_frame, text="cookies.txt: ⏳")
        self.env_cookies_label.grid(row=1, column=1, sticky="w", pady=(0, 1))

        for i in range(2):
            self.tab_main.columnconfigure(i, weight=1)
        self.tab_main.rowconfigure(0, weight=1)

        # Tooltips с i18n и темой
        theme_getter = lambda: self.theme_mode
        ToolTip(self.subs_enable_check,
                lambda: self._t("tooltip_subs_enable"),
                theme_getter)
        ToolTip(self.subs_lang_label,
                lambda: self._t("tooltip_subs_lang"),
                theme_getter)
        ToolTip(self.subs_auto_check,
                lambda: self._t("tooltip_subs_auto"),
                theme_getter)
        ToolTip(self.subs_srt_check,
                lambda: self._t("tooltip_subs_srt"),
                theme_getter)
        ToolTip(self.mode_combo,
                lambda: self._t("tooltip_mode_combo"),
                theme_getter)

        # --- tab_log ---
        self.log_label = ttk.Label(self.tab_log, text=self._t("log_label"), font=("Segoe UI", 12, "bold"))
        self.log_label.pack(side=tk.TOP, anchor="w", padx=5, pady=(5, 0))
        self.log_text = scrolledtext.ScrolledText(self.tab_log, height=15)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.log_text.tag_config("ok", foreground="#1f8b4c")
        self.log_text.tag_config("warn", foreground="#d8a000")
        self.log_text.tag_config("err", foreground="#cc3333")
        self.log_text.configure(state="disabled", cursor="arrow")

        self.clear_log_button = ttk.Button(self.tab_log, text=self._t("clear_log"), command=self.clear_logs)
        self.clear_log_button.pack(side=tk.RIGHT, padx=5, pady=5)
        self.copy_log_button = ttk.Button(self.tab_log, text=self._t("copy_log"), command=self.copy_logs)
        self.copy_log_button.pack(side=tk.RIGHT, padx=5, pady=5)

        # --- tab_history ---
        self.history_label = ttk.Label(self.tab_history, text=self._t("history_title"), font=("Segoe UI", 12, "bold"))
        self.history_label.pack(anchor="w", padx=5, pady=(5, 2))

        cols = self._t("history_cols")
        self.history_tree = ttk.Treeview(
            self.tab_history,
            columns=("#0", "#1", "#2"),
            show="headings",
            height=10,
        )
        for i, col in enumerate(cols):
            self.history_tree.heading(f"#{i}", text=col)
            self.history_tree.column(f"#{i}", width=120 if i < 2 else 400, anchor="w")
        self.history_tree.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        history_scroll = ttk.Scrollbar(self.tab_history, orient="vertical", command=self.history_tree.yview)
        self.history_tree.configure(yscrollcommand=history_scroll.set)
        history_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.history_tree.bind("<Double-1>", self.on_history_double_click)

        self.repeat_button = ttk.Button(self.tab_history, text=self._t("history_repeat"),
                                        command=self.on_history_repeat_button)
        self.repeat_button.pack(anchor="e", padx=5, pady=(0, 5))

        self.refresh_history_tree()

        # --- tab_queue ---
        self.queue_label = ttk.Label(self.tab_queue, text=self._t("queue_label"))
        self.queue_label.pack(anchor="w", padx=5, pady=(5, 2))

        self.queue_text = scrolledtext.ScrolledText(self.tab_queue, height=10)
        self.queue_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))
        self.queue_drag_active = False
        self.queue_drag_moved = False
        self.queue_drag_start_line = None
        self.queue_drag_target_line = None
        self._update_queue_drop_indicator_style()
        self.queue_text.bind("<Button-1>", self._on_queue_drag_start, add="+")
        self.queue_text.bind("<B1-Motion>", self._on_queue_drag_motion, add="+")
        self.queue_text.bind("<ButtonRelease-1>", self._on_queue_drag_drop, add="+")

        self.queue_start_button = ttk.Button(self.tab_queue, text=self._t("queue_start"),
                                             command=self.start_queue)
        self.queue_start_button.pack(anchor="e", padx=5, pady=(0, 4))

        queue_actions = ttk.Frame(self.tab_queue)
        queue_actions.pack(fill=tk.X, padx=5, pady=(0, 5))

        self.queue_remove_button = ttk.Button(
            queue_actions,
            text=self._t("queue_remove_selected"),
            command=self.remove_selected_queue_lines,
        )
        self.queue_remove_button.pack(side=tk.LEFT, padx=(0, 4))

        self.queue_up_button = ttk.Button(
            queue_actions,
            text=self._t("queue_move_up"),
            command=self.move_selected_queue_lines_up,
        )
        self.queue_up_button.pack(side=tk.LEFT, padx=(0, 4))

        self.queue_down_button = ttk.Button(
            queue_actions,
            text=self._t("queue_move_down"),
            command=self.move_selected_queue_lines_down,
        )
        self.queue_down_button.pack(side=tk.LEFT, padx=(0, 4))

        self.queue_clear_button = ttk.Button(
            queue_actions,
            text=self._t("queue_clear"),
            command=self.clear_queue_lines,
        )
        self.queue_clear_button.pack(side=tk.LEFT, padx=(0, 4))

        self.queue_save_button = ttk.Button(
            queue_actions,
            text=self._t("queue_save"),
            command=self.save_queue_to_file,
        )
        self.queue_save_button.pack(side=tk.RIGHT, padx=(4, 0))

        self.queue_load_button = ttk.Button(
            queue_actions,
            text=self._t("queue_load"),
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

        # контекстное меню для Entry
        self.entry_menu = tk.Menu(self.root, tearoff=0)
        self.entry_menu.add_command(label=self._t("entry_menu_clear"), command=self._clear_entry_selection)
        self.entry_menu.add_command(label=self._t("entry_menu_paste"), command=self._paste_to_entry)
        self.root.bind_class("TEntry", "<Button-3>", self._show_entry_menu)

        self._bind_shortcuts()
        self.root.bind("<Configure>", self._on_root_resize)
        self._apply_root_resize()

    def _global_key_handler(self, event):
        """
        Глобальный хендлер для Ctrl+V, который работает независимо от раскладки.
        Проверяем маску Ctrl и keycode клавиши V.
        """
        # 0x4 — маска Ctrl на Windows (Control_L/Control_R) [web:744]
        if event.state & 0x4:
            # На Windows keycode 86 соответствует физической клавише 'V'
            # независимо от текущей раскладки. [web:739][web:736]
            if event.keycode == 86:
                widget = event.widget
                # Ограничиваемся полями ввода (Entry/Text), чтобы не ловить везде
                if isinstance(widget, (tk.Entry, ttk.Entry, tk.Text)):
                    try:
                        widget.event_generate("<<Paste>>")
                    except Exception:
                        pass
                    return "break"
        # иначе — не вмешиваемся
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

        self.notebook.select(self.tab_main)

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

        self.mode_combo["values"] = values

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
        self.mode_display_var.set(key_to_label.get(current, self._t("mode_video_mp4")))

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

    def _on_mode_change(self, event=None):
        selected_label = self.mode_display_var.get()
        self.mode_var.set(self._label_to_mode_key(selected_label))
        self._apply_mode_to_flags()
        self._sync_quality_controls()
        self.save_settings()

    def _on_auto_number_toggle(self):
        self.auto_number_files = self.auto_number_var.get()
        self.save_settings()

    def _sync_quality_controls(self):
        mode = self.mode_var.get()
        if mode == "only_subtitles":
            self.video_quality_combo.configure(state="disabled")
            self.audio_quality_combo.configure(state="disabled")
            
            # Блокируем только главный чекбокс включения субтитров
            self.subs_enable_check.configure(state="disabled")
        else:
            self.video_quality_combo.configure(state="readonly")
            self.audio_quality_combo.configure(state="readonly")
            
            # Возвращаем его в нормальное состояние
            self.subs_enable_check.configure(state="normal")




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
            self.download_dir_label.config(text=folder)
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

        # Normalize emoji prefixes (keep meaning, change presentation).
        raw = message
        message = (
            message.replace("ℹ️ ", "")
            .replace("✅ ", "")
            .replace("⚠️ ", "")
            .replace("⚠ ", "")
            .replace("❌ ", "")
        )

        ts = datetime.now().strftime("%H:%M:%S")

        # Pick level/tag (explicit tag wins).
        level = "INFO"
        if tag in ("ok", "warn", "err"):
            level = tag.upper()
        else:
            u = raw.upper()
            mu = message.upper()
            # yt-dlp-ish lines (stdout/stderr passthrough)
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
            self.log_text.insert(tk.END, header, "ts")
            if tag:
                self.log_text.insert(tk.END, message + "\n", tag)
            else:
                self.log_text.insert(tk.END, message + "\n")
            self.log_text.see(tk.END)
        finally:
            self.log_text.configure(state="disabled")

    def warn_once(self, key: str, message: str, every_s: float = 5.0):
        """
        Log a warning at most once per time window.
        Useful to avoid spamming the log when a UI callback fails repeatedly.
        """
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
        SettingsDialog(self.root, self)

    def clear_logs(self):
        self.log_text.configure(state="normal")
        try:
            self.log_text.delete("1.0", tk.END)
        finally:
            self.log_text.configure(state="disabled")

    def copy_logs(self):
        text = self.log_text.get("1.0", tk.END).strip()
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
    def check_environment(self):
        env_service.check_environment(self)

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
        """Загружает превью для текущего URL в фоновом потоке."""
        url = self.url_entry.get().strip()
        if not url:
            if self.preview_status_label:
                self.preview_status_label.config(text=self._t("preview_status_idle"))
            return

        self.preview_request_id += 1
        req_id = self.preview_request_id
        if self.preview_status_label:
            self.preview_status_label.config(text=self._t("preview_status_loading"))

        def worker():
            info = self.fetch_video_info(url)

            def apply_if_latest():
                if req_id != self.preview_request_id:
                    return
                self.update_preview_from_info(info)
                if self.preview_status_label:
                    if info:
                        self.preview_status_label.config(text=self._t("preview_status_ready"))
                    else:
                        self.preview_status_label.config(text=self._t("preview_status_error"))

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
        existing = self.queue_text.get("1.0", tk.END).strip()
        merged = f"{existing}\n{text}" if existing else text
        self.queue_text.delete("1.0", tk.END)
        self.queue_text.insert("1.0", merged + "\n")

    # ---------- хоткеи ----------

    def _bind_shortcuts(self):
        self.root.bind("<Return>", self._on_enter)
        self.root.bind("<Escape>", self._on_esc)
        self.root.bind("<Control-l>", self._focus_url_entry)
        self.root.bind("<Control-L>", self._focus_url_entry)
        for cls in ("TEntry", "Entry", "Text"):
            self.root.bind_class(cls, "<Control-v>", self._on_ctrl_v)
            self.root.bind_class(cls, "<Control-V>", self._on_ctrl_v)
        # Global key handler for Ctrl+V across different keyboard layouts.
        self.root.bind_all("<Key>", self._global_key_handler, add="+")

    def _on_root_resize(self, event=None):
        # 1. Железный барьер: игнорируем перерисовки внутренних кнопок, текста и вкладок
        if event is not None and event.widget is not self.root:
            return

        try:
            current_size = (self.root.winfo_width(), self.root.winfo_height())
        except Exception:
            return

        # 2. Если физический размер окна не изменился, ничего не делаем
        if current_size == getattr(self, "_last_root_size", None):
            return
        self._last_root_size = current_size

        # 3. Сбрасываем старый таймер, пока пользователь все еще активно тянет окно
        if hasattr(self, "_resize_after_id") and self._resize_after_id is not None:
            try:
                self.root.after_cancel(self._resize_after_id)
            except Exception:
                pass

        # 4. Перерисовываем интерфейс ТОЛЬКО через 300мс ПОСЛЕ ТОГО, как окно остановилось
        self._resize_after_id = self.root.after(300, self._apply_root_resize)

    def _schedule_thumbnail_refresh(self):
        # Resizing image on every Configure event is expensive.
        # Coalesce rapid resize events into a single thumbnail refresh.
        if getattr(self, "_resizing", False):
            return
        if self._thumb_resize_after_id is not None:
            try:
                self.root.after_cancel(self._thumb_resize_after_id)
            except Exception:
                pass
        self._thumb_resize_after_id = self.root.after(220, self._run_scheduled_thumbnail_refresh)

    def _run_scheduled_thumbnail_refresh(self):
        self._thumb_resize_after_id = None
        if not getattr(self, "thumb_source_image", None):
            return
        try:
            self.refresh_preview_thumbnail()
        except Exception:
            pass

    def _apply_root_resize(self):
        self._resize_after_id = None
        if not hasattr(self, "preview_title_label"):
            return

        # Защита от повторного входа во время изменения параметров виджетов
        if getattr(self, "_resizing_running", False):
            return
        self._resizing_running = True

        step = 24
        try:
            # Безопасный расчет ширины превью
            preview_width_raw = max(220, self.preview_title_label.winfo_toplevel().winfo_width() // 4)
            preview_width = (preview_width_raw // step) * step
            if preview_width != self._last_preview_wrap:
                self.preview_title_label.configure(wraplength=preview_width)
                self.preview_channel_label.configure(wraplength=preview_width)
                self._last_preview_wrap = preview_width
                self._schedule_thumbnail_refresh()
        except Exception:
            pass

        try:
            # Безопасный расчет ширины пути папки
            main_width_raw = max(280, self.tab_main.winfo_width() - 180)
            main_width = (main_width_raw // step) * step
            if main_width != self._last_dir_wrap:
                self.download_dir_label.configure(wraplength=main_width)
                self._last_dir_wrap = main_width
        except Exception:
            pass

        self._resizing_running = False

    def _apply_theme_style(self):
        style = ttk.Style()
        is_dark = self.theme_mode == "dark"

        # --- palette (pleasant, high-contrast, modern) ---
        if is_dark:
            bg = "#1f1f1f"
            surface = "#252525"
            surface2 = "#2b2b2b"
            border = "#3a3a3a"
            fg = "#f2f2f2"
            fg_muted = "#b7b7b7"
            fg_disabled = "#7a7a7a"
            accent = "#4cc2ff"
            select_bg = "#333333"
        else:
            bg = "#f5f6f8"
            surface = "#ffffff"
            surface2 = "#f0f2f6"
            border = "#d6d9df"
            fg = "#111111"
            fg_muted = "#5b5f66"
            fg_disabled = "#9aa0a6"
            accent = "#2563eb"
            select_bg = "#e7edf8"

        self.root.configure(bg=bg)
        self.root.option_add("*Font", "{Segoe UI} 10")

        # Base containers
        style.configure("TFrame", background=bg)
        style.configure("Card.TFrame", background=bg)
        shell_bg = "#eceef2" if not is_dark else "#262626"
        style.configure("ProgShell.TFrame", background=shell_bg)

        # Labels
        style.configure("TLabel", background=bg, foreground=fg)
        style.configure("Muted.TLabel", background=bg, foreground=fg_muted)
        style.configure("Hint.TLabel", background=bg, foreground=fg_muted, font=("Segoe UI", 9))

        # Labelframe "cards"
        style.configure("TLabelframe", background=bg, borderwidth=1, relief="solid", bordercolor=border)
        style.configure("TLabelframe.Label", background=bg, foreground=fg, font=("Segoe UI", 10, "bold"))
        style.configure("Settings.TLabelframe", background=bg, borderwidth=1, relief="solid", bordercolor=border)
        style.configure("Settings.TLabelframe.Label", background=bg, foreground=fg, font=("Segoe UI", 10, "bold"))
        style.configure("Settings.TLabel", background=bg, foreground=fg)
        style.configure("Settings.TCheckbutton", background=bg, foreground=fg)

        # Buttons
        style.configure(
            "TButton",
            padding=(12, 8),
            background=surface2,
            foreground=fg,
            borderwidth=1,
            relief="solid",
            bordercolor=border,
            focusthickness=0,
        )
        style.map(
            "TButton",
            background=[("active", surface), ("pressed", surface2), ("disabled", bg)],
            foreground=[("disabled", fg_disabled)],
            bordercolor=[("active", accent), ("pressed", accent)],
        )

        # Entry / Combobox (unified)
        style.configure("TEntry", fieldbackground=surface, foreground=fg, bordercolor=border, lightcolor=border, darkcolor=border)
        style.map("TEntry", fieldbackground=[("disabled", bg)], foreground=[("disabled", fg_disabled)])

        style.configure(
            "Settings.TCombobox",
            fieldbackground=surface,
            background=surface,
            foreground=fg,
            arrowcolor=fg_muted,
            padding=(8, 5),
            bordercolor=border,
            lightcolor=border,
            darkcolor=border,
        )
        style.map(
            "Settings.TCombobox",
            fieldbackground=[("readonly", surface), ("disabled", bg)],
            background=[("readonly", surface), ("disabled", bg)],
            foreground=[("readonly", fg), ("disabled", fg_disabled)],
            arrowcolor=[("readonly", fg_muted), ("disabled", fg_disabled)],
            bordercolor=[("focus", accent), ("active", accent), ("!focus", border)],
        )

        # Checkbutton / Radiobutton (keep readable)
        style.map("TCheckbutton", foreground=[("disabled", fg_disabled)])
        style.map("TRadiobutton", foreground=[("disabled", fg_disabled)])

        # Notebook
        style.configure("App.TNotebook", background=bg, borderwidth=0)
        style.configure(
            "App.TNotebook.Tab",
            padding=(14, 10),
            background=surface2,
            foreground=fg_muted,
            borderwidth=1,
            relief="solid",
            bordercolor=border,
            font=("Segoe UI", 10, "bold"),
        )
        style.map(
            "App.TNotebook.Tab",
            background=[("selected", surface), ("active", surface)],
            foreground=[("selected", fg), ("active", fg)],
            bordercolor=[("selected", accent), ("active", accent)],
        )

        # Treeview
        style.configure("Treeview", background=surface, fieldbackground=surface, foreground=fg, bordercolor=border, borderwidth=1, rowheight=28)
        style.configure("Treeview.Heading", background=surface2, foreground=fg, bordercolor=border, font=("Segoe UI", 10, "bold"))
        style.map("Treeview", background=[("selected", select_bg)], foreground=[("selected", fg)])

        # Scrollbars / separators / progress
        style.configure("TSeparator", background=border)
        style.configure("Vertical.TScrollbar", background=bg, troughcolor=bg, bordercolor=bg, arrowcolor=fg_muted)
        style.configure("Horizontal.TScrollbar", background=bg, troughcolor=bg, bordercolor=bg, arrowcolor=fg_muted)
        # Default progress bar (other widgets using plain TProgressbar)
        style.configure("Horizontal.TProgressbar", background=accent, troughcolor=surface2, bordercolor=border, lightcolor=accent, darkcolor=accent)
        # Main download strip: thin, calm track + flat monochrome fill (minimal, reads well in both themes).
        if is_dark:
            trough_prog = "#323232"
            bar_fill = "#d4d4d8"
        else:
            trough_prog = "#e8eaef"
            bar_fill = "#52525b"
        for parent_style in ("Horizontal.TProgressbar", "Accent.Horizontal.TProgressbar", "TProgressbar"):
            try:
                style.layout("App.Horizontal.TProgressbar", style.layout(parent_style))
                break
            except tk.TclError:
                continue
        style.configure(
            "App.Horizontal.TProgressbar",
            troughcolor=trough_prog,
            bordercolor=trough_prog,
            background=bar_fill,
            lightcolor=bar_fill,
            darkcolor=bar_fill,
        )
        try:
            style.configure("App.Horizontal.TProgressbar", thickness=6, borderwidth=0)
        except tk.TclError:
            try:
                style.configure("App.Horizontal.TProgressbar", thickness=6)
            except tk.TclError:
                pass

        # Combobox dropdown list
        self.root.option_add("*TCombobox*Listbox*Background", surface)
        self.root.option_add("*TCombobox*Listbox*Foreground", fg)
        self.root.option_add("*TCombobox*Listbox*selectBackground", select_bg)
        self.root.option_add("*TCombobox*Listbox*selectForeground", fg)
        self.root.option_add("*TCombobox*Listbox*disabledForeground", fg_disabled)

        # Text areas
        self.log_text.configure(
            bg=surface,
            fg=fg,
            insertbackground=fg,
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            font=("Consolas", 11),
            selectbackground=select_bg,
            selectforeground=fg,
        )
        self.queue_text.configure(
            bg=surface,
            fg=fg,
            insertbackground=fg,
            relief="solid",
            borderwidth=1,
            highlightthickness=0,
            font=("Consolas", 11),
            selectbackground=select_bg,
            selectforeground=fg,
        )

        # Log tag colors (keep meaningful)
        ok_c = "#22c55e" if is_dark else "#15803d"
        warn_c = "#f59e0b" if is_dark else "#b45309"
        err_c = "#ef4444" if is_dark else "#b91c1c"
        self.log_text.tag_config("ok", foreground=ok_c)
        self.log_text.tag_config("warn", foreground=warn_c)
        self.log_text.tag_config("err", foreground=err_c)
        self.log_text.tag_config("ts", foreground=fg_muted)
        self.log_text.tag_config("cmd", foreground=accent)

        # Apply muted styles to specific labels
        try:
            self.preview_status_label.configure(style="Muted.TLabel")
            self.preview_channel_label.configure(style="Muted.TLabel")
        except Exception:
            pass
        try:
            self.status_label.configure(style="Muted.TLabel")
        except Exception:
            pass
        try:
            self.download_dir_label.configure(style="Muted.TLabel")
        except Exception:
            pass

        # Menus (right-click)
        try:
            self.queue_menu.configure(background=surface, foreground=fg, activebackground=select_bg, activeforeground=fg)
            self.entry_menu.configure(background=surface, foreground=fg, activebackground=select_bg, activeforeground=fg)
        except Exception:
            pass

        # Ensure notebook uses our theme
        try:
            self.notebook.configure(style="App.TNotebook")
        except Exception:
            pass

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
        if self.root.winfo_exists():
            self.root.after(15, self._process_ui_queue)

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
        self.url_entry.selection_range(0, tk.END)
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

    def _download_worker(
        self, url, fmt, download_path, audio_only, v_cont,
        a_cont, ffmpeg_path, cookies_path, mode, from_queue,
        subs_enabled, subs_lang, subs_auto, subs_srt,
    ):
        download_service.download_worker(
            self, url, fmt, download_path, audio_only, v_cont,
            a_cont, ffmpeg_path, cookies_path, mode, from_queue,
            subs_enabled, subs_lang, subs_auto, subs_srt,
        )

    # ---------- очередь ----------

    def start_queue(self):
        if self.queue_running:
            messagebox.showinfo(self._t("tab_queue"), self._t("queue_running"))
            return

        text = self.queue_text.get("1.0", tk.END)
        urls = [line.strip() for line in text.splitlines() if line.strip()]
        if not urls:
            return

        self.queue_running = True
        self.queue_start_button.config(state="disabled")
        self.status_label.config(text=self._t("queue_status").format(cur=0, total=len(urls)))

        base_mode = self.mode_var.get()
        self.queue_thread = threading.Thread(target=self._queue_worker, args=(urls, base_mode), daemon=True)
        self.queue_thread.start()

    def _queue_worker(self, urls, base_mode):
        total = len(urls)
        for i, url in enumerate(urls, start=1):
            if self.cancel_requested:
                break

            def update_status(i=i, url=url):
                self.status_label.config(text=self._t("queue_status").format(cur=i, total=total))
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
            self.queue_start_button.config(state="normal")
            self.status_label.config(text=self._t("queue_done"))
            self.download_button.config(state="normal", text=self._t("download"))
            self.cancel_button.config(state="disabled")
        self._enqueue_ui(finish)

    def remove_selected_queue_lines(self):
        try:
            selected = self.queue_text.tag_ranges(tk.SEL)
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
            _ = self.queue_text.tag_ranges(tk.SEL)
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

        lines = self.queue_text.get("1.0", tk.END).splitlines()
        block = lines[start_line - 1:end_line]
        if not block:
            return
        moved = lines[: start_line - 2] + block + [lines[start_line - 2]] + lines[end_line:]
        self.queue_text.delete("1.0", tk.END)
        self.queue_text.insert("1.0", "\n".join(moved) + ("\n" if moved else ""))
        self.queue_text.tag_remove(tk.SEL, "1.0", tk.END)
        self.queue_text.tag_add(
            tk.SEL,
            f"{start_line - 1}.0",
            f"{start_line - 1 + len(block)}.0",
        )
        self.queue_text.mark_set(tk.INSERT, f"{start_line - 1}.0")

    def move_selected_queue_lines_down(self):
        try:
            _ = self.queue_text.tag_ranges(tk.SEL)
            start_line = int(self.queue_text.index("sel.first").split(".")[0])
            end_idx = self.queue_text.index("sel.last")
            end_line, end_col = map(int, end_idx.split("."))
            if end_col == 0 and end_line > start_line:
                end_line -= 1
        except tk.TclError:
            messagebox.showinfo(self._t("tab_queue"), self._t("queue_empty_select"))
            return

        lines = self.queue_text.get("1.0", tk.END).splitlines()
        if end_line >= len(lines):
            return

        block = lines[start_line - 1:end_line]
        if not block:
            return
        moved = lines[: start_line - 1] + [lines[end_line]] + block + lines[end_line + 1:]
        self.queue_text.delete("1.0", tk.END)
        self.queue_text.insert("1.0", "\n".join(moved) + ("\n" if moved else ""))
        self.queue_text.tag_remove(tk.SEL, "1.0", tk.END)
        self.queue_text.tag_add(
            tk.SEL,
            f"{start_line + 1}.0",
            f"{start_line + 1 + len(block)}.0",
        )
        self.queue_text.mark_set(tk.INSERT, f"{start_line + 1}.0")

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

        lines = self.queue_text.get("1.0", tk.END).splitlines()
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

        self.queue_text.delete("1.0", tk.END)
        self.queue_text.insert("1.0", "\n".join(moved) + ("\n" if moved else ""))
        self.queue_text.tag_remove(tk.SEL, "1.0", tk.END)
        self.queue_text.tag_add(tk.SEL, f"{new_start}.0", f"{new_start + len(block)}.0")
        self.queue_text.mark_set(tk.INSERT, f"{new_start}.0")
        self.queue_drag_active = False
        self._clear_queue_drop_indicator()

    def _show_queue_drop_indicator(self, line_no):
        self._clear_queue_drop_indicator()
        lines = self.queue_text.get("1.0", tk.END).splitlines()
        if not lines:
            return
        clamped = max(1, min(line_no, len(lines)))
        self.queue_drag_target_line = clamped
        self.queue_text.tag_add("queue_drop_target", f"{clamped}.0", f"{clamped}.0 lineend+1c")

    def _clear_queue_drop_indicator(self):
        self.queue_drag_target_line = None
        self.queue_text.tag_remove("queue_drop_target", "1.0", tk.END)

    def _update_queue_drop_indicator_style(self):
        bg = "#2f5f8a" if self.theme_mode == "dark" else "#b7d7ff"
        self.queue_text.tag_config("queue_drop_target", background=bg)

    def clear_queue_lines(self):
        self.queue_text.delete("1.0", tk.END)

    def save_queue_to_file(self):
        path = filedialog.asksaveasfilename(
            title=self._t("queue_save"),
            defaultextension=".txt",
            filetypes=[(self._t("queue_filetypes"), "*.txt"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            content = self.queue_text.get("1.0", tk.END).strip()
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
            self.queue_text.delete("1.0", tk.END)
            self.queue_text.insert("1.0", content)
            self.log(f"✅ {self._t('queue_load')}: {path}", tag="ok")
        except Exception as e:
            self.log(f"⚠️ {self._t('queue_load')} failed: {e}", tag="err")

    def on_closing(self):
        if messagebox.askokcancel("Выход", "Вы действительно хотите выйти?"):
            self.root.destroy()



if __name__ == "__main__":
    root = tk.Tk()
    root.title("YouTube Converter")
    app = YouTubeConverterApp(root)
    app.log(f"🔥 Created at: {datetime.now()}", tag="ok")
    app.log("🔗 Version: v1.0.0 (History + Queue + Tooltips i18n + Preview + Env status)", tag="ok")
    root.mainloop()