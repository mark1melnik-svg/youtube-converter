import os
import tkinter as tk
from tkinter import ttk, filedialog

import sv_ttk


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self.transient(parent)
        self.grab_set()
        self.title(self.app._t("settings_title"))
        self.geometry("760x620")
        self.minsize(700, 520)
        self.resizable(True, True)

        self._apply_dialog_theme()

        container = ttk.Frame(self, style="SettingsDlg.TFrame")
        container.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(container, highlightthickness=0, borderwidth=0)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vscroll = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        vscroll.grid(row=0, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=vscroll.set)

        main_frame = ttk.Frame(self.canvas, style="SettingsDlg.TFrame")
        self._canvas_window = self.canvas.create_window((0, 0), window=main_frame, anchor="nw")
        self._main_frame = main_frame

        main_frame.bind("<Configure>", self._on_frame_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.bind_all("<MouseWheel>", self._on_mousewheel)

        downloads_frame = ttk.LabelFrame(main_frame, text=self.app._t("settings_section_downloads"), style="SettingsDlg.TLabelframe")
        downloads_frame.grid(row=0, column=0, sticky="we", pady=(0, 14))
        downloads_frame.columnconfigure(0, weight=1)

        ttk.Label(downloads_frame, text=self.app._t("default_download_folder"), style="SettingsDlg.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6), padx=12
        )
        path_frame = ttk.Frame(downloads_frame, style="SettingsDlg.TFrame")
        path_frame.grid(row=1, column=0, sticky="we", padx=12)
        self.download_path_entry = ttk.Entry(path_frame, style="SettingsDlg.TEntry")
        self.download_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.download_path_entry.insert(0, self.app.default_download_dir)
        ttk.Button(path_frame, text=self.app._t("browse"), command=self.browse_folder, style="SettingsDlg.TButton").pack(side=tk.LEFT, padx=8)
        ttk.Button(path_frame, text=self.app._t("open_folder"), command=self.open_download_folder, style="SettingsDlg.TButton").pack(side=tk.LEFT)

        ttk.Label(downloads_frame, text=self.app._t("cookies_folder"), style="SettingsDlg.TLabel").grid(
            row=2, column=0, sticky="w", pady=(18, 6), padx=12
        )
        cookies_frame = ttk.Frame(downloads_frame, style="SettingsDlg.TFrame")
        cookies_frame.grid(row=3, column=0, sticky="we", padx=12)
        self.cookies_path_entry = ttk.Entry(cookies_frame, style="SettingsDlg.TEntry")
        self.cookies_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        if getattr(self.app, "cookies_dir", None):
            self.cookies_path_entry.insert(0, self.app.cookies_dir)
        ttk.Button(cookies_frame, text=self.app._t("browse"), command=self.browse_cookies_folder, style="SettingsDlg.TButton").pack(
            side=tk.LEFT, padx=8
        )

        ttk.Label(downloads_frame, text=self.app._t("filename_template"), style="SettingsDlg.TLabel").grid(
            row=4, column=0, sticky="w", pady=(18, 6), padx=12
        )
        self.filename_template_entry = ttk.Entry(downloads_frame, style="SettingsDlg.TEntry")
        self.filename_template_entry.grid(row=5, column=0, sticky="we", padx=12)
        self.filename_template_entry.insert(0, getattr(self.app, "filename_template", "%(title)s"))
        ttk.Label(
            downloads_frame,
            text=self.app._t("filename_template_hint"),
            style="SettingsDlg.Hint.TLabel",
        ).grid(row=6, column=0, sticky="w", pady=(4, 4), padx=12)

        interface_frame = ttk.LabelFrame(main_frame, text=self.app._t("settings_section_interface"), style="SettingsDlg.TLabelframe")
        interface_frame.grid(row=1, column=0, sticky="we", pady=(0, 14))
        interface_frame.columnconfigure(0, weight=1)
        self.open_folder_var = tk.BooleanVar(value=getattr(self.app, "open_folder_after_download", False))
        ttk.Checkbutton(
            interface_frame,
            text=self.app._t("open_folder_after"),
            variable=self.open_folder_var,
            style="SettingsDlg.TCheckbutton",
        ).grid(row=0, column=0, sticky="w", pady=(12, 8), padx=12)

        self.theme_var = tk.StringVar(value=self.app.theme_mode)
        theme_frame = ttk.LabelFrame(interface_frame, text="Тема / Theme", style="SettingsDlg.TLabelframe")
        theme_frame.grid(row=1, column=0, sticky="we", pady=(10, 8), padx=12)
        ttk.Radiobutton(theme_frame, text=self.app._t("theme_dark"), value="dark", variable=self.theme_var, style="SettingsDlg.TRadiobutton").pack(
            side=tk.LEFT, padx=10, pady=6
        )
        ttk.Radiobutton(theme_frame, text=self.app._t("theme_light"), value="light", variable=self.theme_var, style="SettingsDlg.TRadiobutton").pack(
            side=tk.LEFT, padx=10, pady=6
        )

        self.lang_var = tk.StringVar(value=self.app.lang)
        lang_frame = ttk.LabelFrame(interface_frame, text="Язык / Language", style="SettingsDlg.TLabelframe")
        lang_frame.grid(row=2, column=0, sticky="we", pady=(6, 8), padx=12)
        ttk.Radiobutton(lang_frame, text="RU", value="ru", variable=self.lang_var, style="SettingsDlg.TRadiobutton").pack(
            side=tk.LEFT, padx=10, pady=6
        )
        ttk.Radiobutton(lang_frame, text="EN", value="en", variable=self.lang_var, style="SettingsDlg.TRadiobutton").pack(
            side=tk.LEFT, padx=10, pady=6
        )

        actions_frame = ttk.Frame(main_frame, style="SettingsDlg.TFrame")
        actions_frame.grid(row=2, column=0, sticky="e", pady=(14, 2))
        ttk.Button(actions_frame, text=self.app._t("close"), command=self.destroy, style="SettingsDlg.TButton").pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(actions_frame, text=self.app._t("ok"), command=self.on_ok, style="SettingsDlg.TButton").pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(actions_frame, text=self.app._t("apply"), command=self.on_apply, style="SettingsDlg.TButton").pack(side=tk.RIGHT)

        main_frame.columnconfigure(0, weight=1)

    def _on_frame_configure(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _apply_dialog_theme(self):
        is_dark = self.app.theme_mode == "dark"
        # Match main app palette (pleasant modern look)
        if is_dark:
            bg = "#1f1f1f"
            surface = "#242424"
            surface2 = "#2a2a2a"
            border = "#353535"
            fg = "#f2f2f2"
            fg_muted = "#b7b7b7"
            accent = "#4cc2ff"
        else:
            bg = "#f5f6f8"
            surface = "#ffffff"
            surface2 = "#f7f8fa"
            border = "#d9dde3"
            fg = "#111111"
            fg_muted = "#5b5f66"
            accent = "#2563eb"
        style = ttk.Style(self)
        self.configure(bg=bg)
        style.configure("SettingsDlg.TFrame", background=bg)
        style.configure("SettingsDlg.TLabelframe", background=surface, bordercolor=border, borderwidth=1, relief="flat", padding=10)
        style.configure("SettingsDlg.TLabelframe.Label", background=surface, foreground=fg, font=("Segoe UI", 10, "bold"))
        style.configure("SettingsDlg.TLabel", background=surface, foreground=fg, font=("Segoe UI", 10))
        style.configure("SettingsDlg.Hint.TLabel", background=surface, foreground=fg_muted, font=("Segoe UI", 9))
        style.configure("SettingsDlg.TEntry", fieldbackground=surface2, foreground=fg, bordercolor=border, relief="flat", padding=8)
        style.configure("SettingsDlg.TButton", padding=(14, 9), background=surface2, foreground=fg, borderwidth=1, relief="flat", bordercolor=border)
        style.map("SettingsDlg.TButton", background=[("active", surface), ("pressed", surface2)], bordercolor=[("active", accent), ("pressed", accent)])
        style.configure("SettingsDlg.TCheckbutton", background=bg, foreground=fg)
        style.configure("SettingsDlg.TRadiobutton", background=bg, foreground=fg)
        if hasattr(self, "canvas"):
            self.canvas.configure(bg=bg, highlightthickness=0, borderwidth=0)

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._canvas_window, width=event.width)

    def _on_mousewheel(self, event):
        try:
            if event.widget.winfo_toplevel() is not self:
                return
        except tk.TclError:
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def destroy(self):
        try:
            self.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        super().destroy()

    def open_download_folder(self):
        path = self.download_path_entry.get().strip() or self.app.default_download_dir
        if os.name == "nt":
            try:
                os.startfile(path)
            except Exception as e:
                self.app.log(self.app._t("open_folder_fail").format(err=e), tag="err")
        else:
            self.app.log(self.app._t("open_folder_non_windows"), tag="warn")

    def browse_folder(self):
        folder = filedialog.askdirectory(title=self.app._t("choose_download_folder"))
        if folder:
            self.download_path_entry.delete(0, tk.END)
            self.download_path_entry.insert(0, folder)
            self.app.default_download_dir = folder
            self.app.download_dir_label.config(text=folder)
            self.app.log(self.app._t("log_download_folder_updated").format(path=folder), tag="ok")

    def browse_cookies_folder(self):
        path = filedialog.askopenfilename(
            title=self.app._t("choose_cookies_folder"),
            filetypes=[("cookies txt", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.cookies_path_entry.delete(0, tk.END)
            self.cookies_path_entry.insert(0, path)
            self.app.cookies_dir = path
            self.app.log(self.app._t("log_cookies_folder").format(path=path), tag="ok")

    def on_ok(self):
        self.on_apply()
        self.destroy()

    def on_apply(self):
        new_dir = self.download_path_entry.get().strip()
        if new_dir and new_dir != self.app.default_download_dir:
            self.app.default_download_dir = new_dir
            self.app.download_dir_label.config(text=new_dir)
            self.app.log(self.app._t("log_download_folder_updated").format(path=new_dir), tag="ok")

        cookies_dir = self.cookies_path_entry.get().strip()
        if cookies_dir != (self.app.cookies_dir or ""):
            self.app.cookies_dir = cookies_dir or None
            if cookies_dir:
                self.app.log(self.app._t("log_cookies_folder").format(path=cookies_dir), tag="ok")

        new_template = self.filename_template_entry.get().strip() or "%(title)s"
        if new_template != self.app.filename_template:
            self.app.filename_template = new_template

        self.app.open_folder_after_download = self.open_folder_var.get()

        new_theme = self.theme_var.get()
        if new_theme != self.app.theme_mode:
            self.app.theme_mode = new_theme
            if new_theme == "dark":
                sv_ttk.set_theme("dark")
            else:
                sv_ttk.set_theme("light")
            if hasattr(self.app, "_apply_theme_style"):
                self.app._apply_theme_style()
            self._apply_dialog_theme()
            if hasattr(self.app, "_update_queue_drop_indicator_style"):
                self.app._update_queue_drop_indicator_style()

        new_lang = self.lang_var.get()
        if new_lang != self.app.lang:
            self.app.lang = new_lang
            self.app.retranslate_ui()

        self.app.save_settings()
