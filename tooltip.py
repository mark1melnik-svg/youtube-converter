import tkinter as tk


class ToolTip:
    def __init__(self, widget, text_getter, theme_getter, delay=500):
        """
        text_getter: callable -> str (берёт актуальный текст, учитывая язык)
        theme_getter: callable -> str ("dark"/"light")
        """
        self.widget = widget
        self.text_getter = text_getter
        self.theme_getter = theme_getter
        self.delay = delay
        self._job = None
        self.tipwindow = None
        widget.bind("<Enter>", self._on_enter)
        widget.bind("<Leave>", self._on_leave)
        widget.bind("<ButtonPress>", self._on_leave)

    def _on_enter(self, event=None):
        self._schedule()

    def _on_leave(self, event=None):
        self._unschedule()
        self._hide_tip()

    def _schedule(self):
        self._unschedule()
        self._job = self.widget.after(self.delay, self._show_tip)

    def _unschedule(self):
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show_tip(self):
        if self.tipwindow:
            return

        text = self.text_getter()
        if not text:
            return

        try:
            x, y, _, cy = self.widget.bbox("insert")
        except Exception:
            x, y, cy = 0, 0, 0
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + cy + 20

        theme = self.theme_getter() or "dark"
        if theme == "dark":
            bg = "#2d2d30"
            fg = "#ffffff"
        else:
            bg = "#ffffe0"
            fg = "#000000"

        self.tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")

        label = tk.Label(
            tw,
            text=text,
            justify="left",
            bg=bg,
            fg=fg,
            relief="solid",
            borderwidth=1,
            padx=5,
            pady=3,
        )
        label.pack(ipadx=1)

    def _hide_tip(self):
        tw = self.tipwindow
        if tw:
            tw.destroy()
            self.tipwindow = None
