import io
import json
import os
import subprocess
import urllib.request

from PIL import Image, ImageTk
from services.platform_utils import has_executable, subprocess_window_kwargs
from services import env_service


def _get_node_for_app(app) -> str | None:
    try:
        return env_service.get_node_cmd(app)
    except Exception:
        return None


def _ytdlp_env(app) -> dict | None:
    node_cmd = _get_node_for_app(app)
    if not node_cmd:
        return None
    try:
        node_dir = os.path.dirname(os.path.abspath(node_cmd))
        env = os.environ.copy()
        cur_path = env.get("PATH", "")
        parts = cur_path.split(os.pathsep) if cur_path else []
        if node_dir not in parts:
            env["PATH"] = node_dir + (os.pathsep + cur_path if cur_path else "")
        return env
    except Exception:
        return None


def _add_js_runtime_hints(cmd: list[str], app=None) -> list[str]:
    updated = cmd[:]
    if "--js-runtimes" in updated:
        return updated

    if has_executable("deno"):
        updated += ["--js-runtimes", "deno"]
        if "--remote-components" not in updated:
            updated += ["--remote-components", "ejs:npm"]
    elif has_executable("bun"):
        updated += ["--js-runtimes", "bun"]
        if "--remote-components" not in updated:
            updated += ["--remote-components", "ejs:npm"]
    elif (_get_node_for_app(app) is not None if app is not None else has_executable("node")):
        updated += ["--js-runtimes", "node"]
        if "--remote-components" not in updated:
            updated += ["--remote-components", "ejs:github"]
    return updated


def fetch_video_info(app, url: str) -> dict | None:
    try:
        ytdlp_cmd = app.get_ytdlp_cmd()
        try:
            app._enqueue_ui(
                lambda: app.log(
                    f"ℹ️ yt-dlp for preview: {ytdlp_cmd[0]} [{app._describe_ytdlp_source(ytdlp_cmd)}]",
                    tag="ok",
                )
            )
        except Exception:
            pass

        cmd = ytdlp_cmd + [
            "--extractor-args",
            "youtube:player_client=web,web_safari",
            "-J",
            url,
        ]
        cookies_path = None
        try:
            cookies_path = app.get_cookies_path()
        except Exception:
            cookies_path = None
        if cookies_path:
            cmd += ["--cookies", cookies_path]

        cmd = _add_js_runtime_hints(cmd, app)

        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
            env=_ytdlp_env(app),
            **subprocess_window_kwargs(),
        )
        if proc.returncode != 0:
            err = (proc.stderr or "").strip()
            if err:
                lines = [l.rstrip() for l in err.splitlines() if l.strip()]
                snippet = lines[0] if lines else err

                def emit_preview_errors():
                    try:
                        app.warn_once("preview_failed_header", f"Preview failed: {snippet}", every_s=4.0)
                        for l in lines[:18]:
                            lu = l.upper()
                            if lu.startswith("ERROR") or "SIGN IN TO CONFIRM" in lu:
                                app.log(l, tag="err")
                            elif lu.startswith("WARNING"):
                                app.log(l, tag="warn")
                            else:
                                app.log(l)

                        low = err.lower()
                        if "no longer valid" in low or "rotated" in low:
                            app.log(
                                "Cookies look stale/rotated — re-export cookies.txt from the same browser session "
                                "right before download (include google.com + youtube.com).",
                                tag="warn",
                            )
                        if "sign in to confirm youre not a bot" in low or "confirm youre not a bot" in low:
                            app.log(
                                "YouTube bot-check — likely invalid/expired cookies OR missing google session cookies.",
                                tag="warn",
                            )
                        if "failed to decrypt with dpapi" in low:
                            app.log(
                                "DPAPI decrypt failed for browser cookies — rely on fresh cookies.txt export instead "
                                "(or run yt-dlp/app under the same Windows user/session as the browser).",
                                tag="warn",
                            )
                    except Exception:
                        pass

                try:
                    app._enqueue_ui(emit_preview_errors)
                except Exception:
                    pass
            return None
        return json.loads(proc.stdout)
    except Exception as e:
        try:
            app._enqueue_ui(lambda err=str(e): app.log(f"⚠️ Ошибка получения информации о видео: {err}", tag="warn"))
        except Exception:
            pass
        return None


def update_preview_from_info(app, info: dict | None):
    if not info:
        app.thumb_image = None
        app.thumb_full_image = None
        app.thumb_source_image = None
        app.thumb_label.config(image="")
        if app.preview_title_label:
            app.preview_title_label.config(text="")
        if app.preview_channel_label:
            app.preview_channel_label.config(text="")
        return

    title = info.get("title") or ""
    uploader = info.get("uploader") or ""
    if app.preview_title_label:
        app.preview_title_label.config(
            text=title[:80] + ("…" if len(title) > 80 else "")
        )
    if app.preview_channel_label:
        app.preview_channel_label.config(text=uploader)

    thumb = info.get("thumbnail")
    thumbs = info.get("thumbnails") or []
    thumb_url = thumb or (thumbs[-1].get("url") if thumbs else None)
    if thumb_url:
        show_thumbnail_from_url(app, thumb_url)
    else:
        app.thumb_image = None
        app.thumb_full_image = None
        app.thumb_source_image = None
        app.thumb_label.config(image="")


def show_thumbnail_from_url(app, url: str):
    if not url:
        return
    try:
        with urllib.request.urlopen(url) as resp:
            data = resp.read()
        img = Image.open(io.BytesIO(data)).convert("RGB")
        app.thumb_source_image = img

        refresh_thumbnail_size(app)
    except Exception as e:
        app.log(f"⚠️ Не удалось загрузить превью: {e}", tag="warn")


def refresh_thumbnail_size(app):
    source = getattr(app, "thumb_source_image", None)
    if source is None:
        return

    w, h = source.size
    panel_width = max(260, app.thumb_label.winfo_width() - 12)
    max_preview_width = min(520, panel_width)
    if w > max_preview_width:
        ratio = max_preview_width / w
        img_small = source.resize((max_preview_width, int(h * ratio)), Image.LANCZOS)
    else:
        img_small = source

    max_full_width = min(900, max(520, app.root.winfo_width() - 120))
    if w > max_full_width:
        ratio = max_full_width / w
        img_full = source.resize((max_full_width, int(h * ratio)), Image.LANCZOS)
    else:
        img_full = source.copy()

    app.thumb_full_image = ImageTk.PhotoImage(img_full)
    app.thumb_image = ImageTk.PhotoImage(img_small)
    app.thumb_label.config(image=app.thumb_image)
