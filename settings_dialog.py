import os
import tkinter as tk
from tkinter import filedialog
import customtkinter as ctk


class SettingsDialog(ctk.CTkToplevel):
    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._initial_theme = getattr(self.app, "theme_mode", "dark")
        # transient() keeps the dialog above the parent without blocking input (no grab_set).
        # This fixes the "opens behind main window" bug on Windows DWM.
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.on_cancel)
        self.bind("<Destroy>", self._on_destroy)
        self.title(self.app._t("settings_title"))
        self.resizable(False, False)

        # Dynamic appearance palette tuples: (light, dark)
        self._bg_main = ("#F2F2F7", "#161618")
        self._bg_card = ("#FFFFFF", "#242426")
        self._border_card = ("#E5E5EA", "#323234")
        self._btn_secondary = ("#E5E5EA", "#2C2C2E")
        self._btn_secondary_hover = ("#D1D1D6", "#3A3A3C")
        self._btn_primary = ("#007AFF", "#0A84FF")
        self._btn_primary_hover = ("#0051A8", "#0066CC")
        self._text_color = ("#000000", "#FFFFFF")
        self._text_muted = ("#8E8E93", "#8E8E93")
        self._input_bg = ("#E5E5EA", "#1C1C1E")
        self._input_border = ("#D1D1D6", "#38383A")

        self.configure(fg_color=self._bg_main)

        self._setup_ui()

        # Centering over parent based on true bounding box
        self.update_idletasks()
        w = 640
        h = max(680, self.winfo_reqheight() + 28)
        pw = max(parent.winfo_width(), 640)
        ph = max(parent.winfo_height(), h)
        px = parent.winfo_rootx()
        py = parent.winfo_rooty()
        x = max(40, px + (pw - w) // 2)
        y = max(40, py + (ph - h) // 2)
        self.geometry(f"{w}x{h}+{x}+{y}")

        self.lift()
        self.focus_force()

    def _setup_ui(self):
        main_container = ctk.CTkFrame(self, fg_color="transparent")
        main_container.pack(fill="both", expand=True, padx=20, pady=(16, 16))

        # --- SECTION 1: Downloads & Encoding ---
        card_downloads = ctk.CTkFrame(
            main_container,
            fg_color=self._bg_card,
            border_color=self._border_card,
            border_width=1,
            corner_radius=12,
        )
        card_downloads.pack(fill="x", pady=(0, 12), padx=0)

        ctk.CTkLabel(
            card_downloads,
            text=self.app._t("settings_section_downloads"),
            font=("Segoe UI", 13, "bold"),
            text_color=self._text_color,
        ).pack(anchor="w", padx=16, pady=(12, 8))

        # Download path row
        ctk.CTkLabel(
            card_downloads,
            text=self.app._t("default_download_folder"),
            font=("Segoe UI", 11),
            text_color=self._text_muted,
        ).pack(anchor="w", padx=16, pady=(2, 2))

        path_frame = ctk.CTkFrame(card_downloads, fg_color="transparent")
        path_frame.pack(fill="x", padx=16, pady=(0, 10))
        self.download_path_entry = ctk.CTkEntry(
            path_frame,
            fg_color=self._input_bg,
            border_color=self._input_border,
            text_color=self._text_color,
            corner_radius=8,
            height=32,
        )
        self.download_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.download_path_entry.insert(0, self.app.default_download_dir)

        ctk.CTkButton(
            path_frame,
            text=self.app._t("browse"),
            command=self.browse_folder,
            fg_color=self._btn_secondary,
            hover_color=self._btn_secondary_hover,
            text_color=self._text_color,
            corner_radius=8,
            width=85,
            height=32,
        ).pack(side="left")

        # Cookies path row
        ctk.CTkLabel(
            card_downloads,
            text=self.app._t("cookies_folder"),
            font=("Segoe UI", 11),
            text_color=self._text_muted,
        ).pack(anchor="w", padx=16, pady=(2, 2))

        cookies_frame = ctk.CTkFrame(card_downloads, fg_color="transparent")
        cookies_frame.pack(fill="x", padx=16, pady=(0, 10))
        self.cookies_path_entry = ctk.CTkEntry(
            cookies_frame,
            fg_color=self._input_bg,
            border_color=self._input_border,
            text_color=self._text_color,
            corner_radius=8,
            height=32,
        )
        self.cookies_path_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        if getattr(self.app, "cookies_dir", None):
            self.cookies_path_entry.insert(0, self.app.cookies_dir)

        ctk.CTkButton(
            cookies_frame,
            text=self.app._t("browse"),
            command=self.browse_cookies_folder,
            fg_color=self._btn_secondary,
            hover_color=self._btn_secondary_hover,
            text_color=self._text_color,
            corner_radius=8,
            width=85,
            height=32,
        ).pack(side="left")

        # Filename template row (simplified dropdown)
        ctk.CTkLabel(
            card_downloads,
            text=self.app._t("filename_template"),
            font=("Segoe UI", 11),
            text_color=self._text_muted,
        ).pack(anchor="w", padx=16, pady=(2, 2))

        self.preset_map = {
            self.app._t("template_preset_title"): "%(title)s",
            self.app._t("template_preset_uploader_title"): "%(uploader)s - %(title)s",
            self.app._t("template_preset_title_id"): "%(title)s [%(id)s]",
        }

        current_tmpl = getattr(self.app, "filename_template", "%(title)s")
        current_preset = self.app._t("template_preset_title")
        for label, val in self.preset_map.items():
            if current_tmpl == val:
                current_preset = label
                break
            elif "%(title,id)s" in current_tmpl and val == "%(title)s [%(id)s]":
                current_preset = label
                break

        self.preset_menu = ctk.CTkOptionMenu(
            card_downloads,
            values=list(self.preset_map.keys()),
            fg_color=self._input_bg,
            button_color=self._btn_secondary,
            button_hover_color=self._btn_secondary_hover,
            text_color=self._text_color,
            dropdown_fg_color=self._bg_card,
            dropdown_hover_color=self._btn_primary,
            dropdown_text_color=self._text_color,
            corner_radius=8,
            height=32,
        )
        self.preset_menu.set(current_preset)
        self.preset_menu.pack(fill="x", padx=16, pady=(0, 10))

        # Hardware acceleration row
        ctk.CTkLabel(
            card_downloads,
            text=self.app._t("settings_hw_accel"),
            font=("Segoe UI", 11),
            text_color=self._text_muted,
        ).pack(anchor="w", padx=16, pady=(2, 2))

        self.hw_map = {
            self.app._t("hw_accel_auto"): "auto",
            self.app._t("hw_accel_nvenc"): "nvenc",
            self.app._t("hw_accel_qsv"): "qsv",
            self.app._t("hw_accel_amf"): "amf",
            self.app._t("hw_accel_disabled"): "disabled",
        }

        current_hw = getattr(self.app, "hw_accel", "auto")
        current_hw_label = self.app._t("hw_accel_auto")
        for label, val in self.hw_map.items():
            if current_hw == val:
                current_hw_label = label
                break

        self.hw_menu = ctk.CTkOptionMenu(
            card_downloads,
            values=list(self.hw_map.keys()),
            fg_color=self._input_bg,
            button_color=self._btn_secondary,
            button_hover_color=self._btn_secondary_hover,
            text_color=self._text_color,
            dropdown_fg_color=self._bg_card,
            dropdown_hover_color=self._btn_primary,
            dropdown_text_color=self._text_color,
            corner_radius=8,
            height=32,
        )
        self.hw_menu.set(current_hw_label)
        self.hw_menu.pack(fill="x", padx=16, pady=(0, 14))

        # --- SECTION 2: Interface & Behavior ---
        card_interface = ctk.CTkFrame(
            main_container,
            fg_color=self._bg_card,
            border_color=self._border_card,
            border_width=1,
            corner_radius=12,
        )
        card_interface.pack(fill="x", pady=(0, 14), padx=0)

        ctk.CTkLabel(
            card_interface,
            text=self.app._t("settings_section_interface"),
            font=("Segoe UI", 13, "bold"),
            text_color=self._text_color,
        ).pack(anchor="w", padx=16, pady=(12, 8))

        # Checkboxes
        self.open_folder_var = tk.BooleanVar(value=getattr(self.app, "open_folder_after_download", False))
        ctk.CTkCheckBox(
            card_interface,
            text=self.app._t("open_folder_after"),
            variable=self.open_folder_var,
            fg_color=self._btn_primary,
            hover_color=self._btn_primary_hover,
            border_color="#636366",
            text_color=self._text_color,
            font=("Segoe UI", 11),
            corner_radius=4,
        ).pack(anchor="w", padx=16, pady=(2, 6))

        auto_paste_val = False
        if hasattr(self.app, "auto_paste_clipboard_var") and self.app.auto_paste_clipboard_var is not None:
            auto_paste_val = bool(self.app.auto_paste_clipboard_var.get())
        self.auto_paste_var = tk.BooleanVar(value=auto_paste_val)
        ctk.CTkCheckBox(
            card_interface,
            text=self.app._t("auto_paste_clipboard"),
            variable=self.auto_paste_var,
            fg_color=self._btn_primary,
            hover_color=self._btn_primary_hover,
            border_color="#636366",
            text_color=self._text_color,
            font=("Segoe UI", 11),
            corner_radius=4,
        ).pack(anchor="w", padx=16, pady=(2, 10))

        # Selectors: Theme & Language
        selectors_frame = ctk.CTkFrame(card_interface, fg_color="transparent")
        selectors_frame.pack(fill="x", padx=16, pady=(0, 14))

        # Theme selector
        theme_sub = ctk.CTkFrame(selectors_frame, fg_color="transparent")
        theme_sub.pack(side="left", padx=(0, 24))
        ctk.CTkLabel(
            theme_sub,
            text=self.app._t("settings_theme"),
            font=("Segoe UI", 11),
            text_color=self._text_muted,
        ).pack(anchor="w", pady=(0, 4))
        self.theme_var = tk.StringVar(value=self.app.theme_mode)
        self.theme_segmented = ctk.CTkSegmentedButton(
            theme_sub,
            values=["dark", "light"],
            variable=self.theme_var,
            selected_color=self._btn_primary,
            selected_hover_color=self._btn_primary_hover,
            unselected_color=self._btn_secondary,
            unselected_hover_color=self._btn_secondary_hover,
            text_color=self._text_color,
            corner_radius=8,
            height=30,
        )
        self.theme_segmented.pack(anchor="w")

        # Language selector
        lang_sub = ctk.CTkFrame(selectors_frame, fg_color="transparent")
        lang_sub.pack(side="left")
        ctk.CTkLabel(
            lang_sub,
            text=self.app._t("settings_lang"),
            font=("Segoe UI", 11),
            text_color=self._text_muted,
        ).pack(anchor="w", pady=(0, 4))
        self.lang_var = tk.StringVar(value=self.app.lang.upper())
        self.lang_segmented = ctk.CTkSegmentedButton(
            lang_sub,
            values=["RU", "EN"],
            variable=self.lang_var,
            selected_color=self._btn_primary,
            selected_hover_color=self._btn_primary_hover,
            unselected_color=self._btn_secondary,
            unselected_hover_color=self._btn_secondary_hover,
            text_color=self._text_color,
            corner_radius=8,
            height=30,
        )
        self.lang_segmented.pack(anchor="w")

        # --- BOTTOM ACTIONS: Save & Cancel ---
        actions_frame = ctk.CTkFrame(main_container, fg_color="transparent")
        actions_frame.pack(fill="x", side="bottom", pady=(14, 24))

        ctk.CTkButton(
            actions_frame,
            text=self.app._t("settings_cancel"),
            command=self.on_cancel,
            fg_color=self._btn_secondary,
            hover_color=self._btn_secondary_hover,
            text_color=self._text_color,
            corner_radius=8,
            width=100,
            height=34,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkButton(
            actions_frame,
            text=self.app._t("settings_save"),
            command=self.on_save,
            fg_color=self._btn_primary,
            hover_color=self._btn_primary_hover,
            text_color="#FFFFFF",
            font=("Segoe UI", 11, "bold"),
            corner_radius=8,
            width=110,
            height=34,
        ).pack(side="right")

    def _safe_grab_release(self):
        try:
            self.grab_release()
        except Exception:
            pass

    def _on_destroy(self, event=None):
        if event is None or event.widget is self:
            self._safe_grab_release()

    def destroy(self):
        self._safe_grab_release()
        try:
            super().destroy()
        except Exception:
            pass
        if getattr(self.app, "_settings_dialog", None) is self:
            self.app._settings_dialog = None

    def browse_folder(self):
        folder = filedialog.askdirectory(
            title=self.app._t("choose_download_folder"),
            initialdir=self.download_path_entry.get().strip() or self.app.default_download_dir,
        )
        if folder:
            self.download_path_entry.delete(0, tk.END)
            self.download_path_entry.insert(0, folder)

    def browse_cookies_folder(self):
        path = filedialog.askopenfilename(
            title=self.app._t("choose_cookies_folder"),
            filetypes=[("cookies txt", "*.txt"), ("All files", "*.*")],
        )
        if path:
            self.cookies_path_entry.delete(0, tk.END)
            self.cookies_path_entry.insert(0, path)

    def on_cancel(self):
        self._safe_grab_release()
        self.destroy()

    def on_save(self):
        self._safe_grab_release()
        new_dir = self.download_path_entry.get().strip()
        if new_dir and new_dir != self.app.default_download_dir:
            self.app.default_download_dir = new_dir
            if hasattr(self.app, "download_dir_label"):
                self.app.download_dir_label.configure(text=new_dir)
            self.app.log(self.app._t("log_download_folder_updated").format(path=new_dir), tag="ok")

        cookies_dir = self.cookies_path_entry.get().strip()
        if cookies_dir != (self.app.cookies_dir or ""):
            self.app.cookies_dir = cookies_dir or None
            if cookies_dir:
                self.app.log(self.app._t("log_cookies_folder").format(path=cookies_dir), tag="ok")

        selected_preset = self.preset_menu.get()
        self.app.filename_template = self.preset_map.get(selected_preset, "%(title)s")

        selected_hw = self.hw_menu.get()
        self.app.hw_accel = self.hw_map.get(selected_hw, "auto")

        self.app.open_folder_after_download = self.open_folder_var.get()
        if hasattr(self.app, "auto_paste_clipboard_var"):
            self.app.auto_paste_clipboard_var.set(self.auto_paste_var.get())

        new_theme = self.theme_var.get().lower()
        self.app.change_theme(new_theme)

        new_lang = self.lang_var.get().lower()
        if new_lang != self.app.lang:
            self.app.lang = new_lang
            if hasattr(self.app, "retranslate_ui"):
                self.app.retranslate_ui()

        self.app.save_settings()
        self.destroy()
