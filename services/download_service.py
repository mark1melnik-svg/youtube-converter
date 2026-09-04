import os
import subprocess
import threading
import shutil
from tkinter import messagebox
from services.platform_utils import subprocess_window_kwargs
from services import env_service


def _has_node() -> bool:
    return bool(shutil.which("node") or shutil.which("node.exe"))


def _has_deno() -> bool:
    return bool(shutil.which("deno") or shutil.which("deno.exe"))


def _has_bun() -> bool:
    return bool(shutil.which("bun") or shutil.which("bun.exe"))


def _get_node_for_app(app) -> str | None:
    try:
        return env_service.get_node_cmd(app)
    except Exception:
        return shutil.which("node") or shutil.which("node.exe")


def _ytdlp_env(app) -> dict | None:
    node_cmd = _get_node_for_app(app)
    if not node_cmd:
        return None
    try:
        node_dir = os.path.dirname(os.path.abspath(node_cmd))
        if not node_dir:
            return None
        env = os.environ.copy()
        cur_path = env.get("PATH", "")
        parts = cur_path.split(os.pathsep) if cur_path else []
        if node_dir not in parts:
            env["PATH"] = node_dir + (os.pathsep + cur_path if cur_path else "")
        return env
    except Exception:
        return None


def _browser_cookie_source_available(browser: str) -> bool:
    b = (browser or "").lower()
    try:
        if os.name != "nt":
            return True
        home = os.path.expanduser("~")
        local = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        roaming = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")

        if b == "chrome":
            return os.path.isdir(os.path.join(local, "Google", "Chrome", "User Data"))
        if b == "edge":
            return os.path.isdir(os.path.join(local, "Microsoft", "Edge", "User Data"))
        if b == "brave":
            return os.path.isdir(os.path.join(local, "BraveSoftware", "Brave-Browser", "User Data"))
        if b == "firefox":
            return os.path.isdir(os.path.join(roaming, "Mozilla", "Firefox", "Profiles")) or os.path.isdir(
                os.path.join(local, "Packages", "Mozilla.Firefox_n80bbvh6b1yt2", "LocalCache", "Roaming", "Mozilla", "Firefox", "Profiles")
            )
        return True
    except Exception:
        return True


def _cmd_has_flag(cmd, flag: str) -> bool:
    try:
        return flag in cmd
    except Exception:
        return False


def _add_js_runtime_hints(cmd, app=None):
    """
    EJS signature solving in yt-dlp may require explicitly enabling JS runtime
    and/or allowing remote component resolution.
    """
    updated = cmd[:]
    # Prefer deno when available (recommended by upstream; enabled by default),
    # otherwise fall back to node. Bun can also do npm remote components.
    if _has_deno():
        if not _cmd_has_flag(updated, "--js-runtimes"):
            # Make it explicit for reproducibility across installs.
            updated += ["--js-runtimes", "deno"]
        if not _cmd_has_flag(updated, "--remote-components"):
            updated += ["--remote-components", "ejs:npm"]
    elif _has_bun():
        if not _cmd_has_flag(updated, "--js-runtimes"):
            updated += ["--js-runtimes", "bun"]
        if not _cmd_has_flag(updated, "--remote-components"):
            updated += ["--remote-components", "ejs:npm"]
    elif (_get_node_for_app(app) if app is not None else _has_node()):
        if not _cmd_has_flag(updated, "--js-runtimes"):
            updated += ["--js-runtimes", "node"]
        if not _cmd_has_flag(updated, "--remote-components"):
            # npm remote components are for deno/bun; github works across runtimes.
            updated += ["--remote-components", "ejs:github"]
    return updated


def list_formats(app, url: str):
    url = (url or "").strip()
    if not url:
        app.log(app._t("enter_url_error_log"), tag="err")
        return

    def worker():
        try:
            ytdlp_cmd = app.get_ytdlp_cmd()
            cookies_path = app.get_cookies_path()
            cmd = ytdlp_cmd + [
                "--extractor-args", "youtube:player_client=web,web_safari",
                "--list-formats",
                url,
            ]
            if cookies_path:
                cmd += ["--cookies", cookies_path]
            cmd = _add_js_runtime_hints(cmd, app)

            app._enqueue_ui(lambda c=cmd: app.log(app._t("log_cmd").format(cmd=" ".join(c))))

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                env=_ytdlp_env(app),
                **subprocess_window_kwargs(),
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                if app.cancel_requested:
                    break
                app._enqueue_ui(lambda l=line.rstrip("\n"): app.log(l))
            rc = proc.wait()
            app._enqueue_ui(lambda r=rc: app.log(f"ℹ️ yt-dlp exit code: {r}"))
        except Exception as e:
            app._enqueue_ui(lambda err=str(e): app.log(app._t("common_error").format(msg=err), tag="err"))

    threading.Thread(target=worker, daemon=True).start()


def _strip_flag_with_value(cmd, flag):
    result = []
    skip = 0
    for part in cmd:
        if skip:
            skip -= 1
            continue
        if part == flag:
            skip = 1
            continue
        result.append(part)
    return result


def _replace_flag_with_value(cmd, flag, value):
    result = []
    i = 0
    while i < len(cmd):
        part = cmd[i]
        if part == flag and i + 1 < len(cmd):
            result.extend([flag, value])
            i += 2
            continue
        result.append(part)
        i += 1
    return result


def _looks_like_youtube_bot_check(output_lines):
    text = "\n".join(output_lines).lower()
    markers = [
        "sign in to confirm you're not a bot",
        "sign in to confirm you’re not a bot",
        "sign in to confirm youre not a bot",
        "confirm you’re not a bot",
        "use --cookies-from-browser or --cookies",
        "http error 429",
        "too many requests",
    ]
    return any(m in text for m in markers)


def _retry_botcheck_with_alt_clients(app, cmd, audio_only):
    """
    If cookies are present but YouTube still triggers bot-check,
    some clients may succeed where web fails.
    """
    alt = _replace_flag_with_value(cmd, "--extractor-args", "youtube:player_client=android,web")
    alt = _with_soft_video_format(alt, audio_only)
    alt = _add_js_runtime_hints(alt, app)
    app._enqueue_ui(lambda: app.log("⚠️ Bot-check with cookies. Retrying on Android/Web clients...", tag="warn"))
    app._enqueue_ui(lambda c=alt: app.log(app._t("log_cmd").format(cmd=" ".join(c))))
    return alt


def _looks_like_signature_or_format_failure(output_lines):
    text = "\n".join(output_lines).lower()
    markers = [
        "signature solving failed",
        "n challenge solving failed",
        "only images are available for download",
        "requested format is not available",
    ]
    return any(m in text for m in markers)


def _looks_like_dpapi_failure(output_lines):
    text = "\n".join(output_lines).lower()
    return "failed to decrypt with dpapi" in text


def _looks_like_no_overwrite_skip(output_lines) -> bool:
    """
    yt-dlp emits various messages when it refuses to overwrite.
    We treat those as a "completed but skipped" outcome for UX purposes.
    """
    text = "\n".join(output_lines).lower()
    markers = [
        "file is already in target directory",
        "has already been downloaded",
        "already exists",
        "not overwriting",
        "skipping",
    ]
    return any(m in text for m in markers)


def _estimate_produced_files(output_lines) -> int:
    """
    Best-effort estimate of how many files were produced.
    Used only to advance autonumber across runs.
    """
    try:
        # Common markers from yt-dlp:
        # - "[download] Destination: ..."
        # - "[Merger] Merging formats into ..."
        # - "[ExtractAudio] Destination: ..."
        # - "Deleting original file ..."
        text = "\n".join(output_lines)
        dest = sum(1 for l in output_lines if "Destination:" in l)
        merge = sum(1 for l in output_lines if "Merging formats into" in l)
        # Some runs only show merge line (no Destination), so take max.
        produced = max(dest, merge)
        if produced <= 0 and _looks_like_no_overwrite_skip(output_lines):
            return 0
        return produced if produced > 0 else 1
    except Exception:
        return 1

def _with_soft_video_format(cmd, audio_only):
    if audio_only:
        return _replace_flag_with_value(cmd, "-f", "bestaudio/best")
    return _replace_flag_with_value(cmd, "-f", "bestvideo+bestaudio/best")


def _cookies_has_google_domain(cookies_path: str | None) -> bool:
    if not cookies_path or not os.path.isfile(cookies_path):
        return False
    try:
        with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
            data = f.read(400_000).lower()
        return ("google.com" in data) or (".google." in data)
    except Exception:
        return False


def _build_video_format_expr(container: str, max_height: str) -> str:
    # Prefer exact/near target height, then allow lower as fallback.
    if container == "webm":
        primary = "bestvideo[vcodec^=vp9][ext=webm]/bestvideo[ext=webm]/bestvideo"
        mux_audio = "bestaudio[ext=webm]/bestaudio"
    else:
        primary = "bestvideo[ext=mp4]/bestvideo[vcodec*=avc1]/bestvideo"
        mux_audio = "bestaudio[ext=m4a]/bestaudio"

    if max_height == "best":
        return f"({primary})+({mux_audio})/best"

    # Restrict to requested max resolution, keeping stream preference order.
    return (
        f"({primary})[height<={max_height}]+({mux_audio})/"
        f"best[height<={max_height}]/best"
    )


def download_video(app, from_queue=False, url_override=None, mode_override=None):
    if not from_queue:
        if app.download_thread and app.download_thread.is_alive():
            messagebox.showwarning(app._t("download_title"), app._t("already_downloading"))
            return

    url = (url_override or app.url_entry.get()).strip()
    if not url:
        app.log(app._t("enter_url_error_log"), tag="err")
        messagebox.showerror(app._t("error_title"), app._t("enter_url_error_box"))
        return

    if mode_override:
        app.mode_var.set(mode_override)
        app._apply_mode_to_flags()
        app._sync_quality_controls()
        app._update_mode_combo_values()

    mode = app.mode_var.get()
    valid_modes = {"video_mp4", "video_webm", "audio_mp3", "audio_m4a", "audio_opus", "only_subtitles"}
    if mode not in valid_modes:
        mode = app._label_to_mode_key(mode)
        app.mode_var.set(mode)
        app._update_mode_combo_values()
    download_path = app.default_download_dir
    app.log(app._t("download_folder_log").format(path=download_path))
    video_quality = app.video_quality_var.get()
    audio_quality = app.audio_quality_var.get()

    audio_only = mode.startswith("audio_")
    if mode == "video_mp4":
        fmt = _build_video_format_expr("mp4", video_quality)
    elif mode == "video_webm":
        fmt = _build_video_format_expr("webm", video_quality)
    elif mode == "only_subtitles":
        fmt = "best" 
    else:
        if mode == "audio_m4a":
            fmt = "bestaudio[ext=m4a]/bestaudio"
        elif mode == "audio_opus":
            fmt = "bestaudio[acodec*=opus]/bestaudio"
        else:
            fmt = "bestaudio/best"


    # Preview updates already run in background; avoid blocking UI here.
    app._update_preview()

    if not from_queue:
        app.download_button.config(state="disabled", text=app._t("status_downloading"))
    app.cancel_button.config(state="normal")
    app.status_label.config(text=app._t("status_downloading"))
    app.progress.configure(mode="determinate", maximum=100, value=0)

    app.log("=" * 50)
    app.log(app._t("start_cli"))
    app.log(app._t("log_type").format(type=mode))
    app.log(app._t("log_fmt").format(fmt=fmt))
    app.log(app._t("log_audio_only").format(audio_only=audio_only))
    app.log(f"   Video quality: {video_quality}")
    app.log(f"   Audio quality: {audio_quality}")
    app.log(f"   Auto numbering: {app.auto_number_files}")
    app.log(app._t("log_folder").format(path=download_path))
    app.log("=" * 50)

    app.cancel_requested = False
    v_cont = app.video_container_var.get().lower()
    a_cont = app.audio_container_var.get().lower()
    subs_enabled = app.subs_enabled_var.get()
    subs_lang = app.subs_lang_var.get()
    subs_auto = app.subs_auto_var.get()
    subs_srt = app.subs_srt_var.get()
    ffmpeg_path = app.get_ffmpeg_path()
    cookies_path = app.get_cookies_path()
    cookies_has_google = _cookies_has_google_domain(cookies_path)
    if cookies_path and not cookies_has_google:
        app.log(
            "⚠️ cookies.txt не содержит google.com cookies. "
            "Это часто вызывает YouTube bot-check даже при валидном youtube.com.",
            tag="warn",
        )

    app.download_thread = threading.Thread(
        target=download_worker,
        args=(
            app, url, fmt, download_path, audio_only, v_cont, a_cont,
            ffmpeg_path, cookies_path, mode, from_queue,
            subs_enabled, subs_lang, subs_auto, subs_srt, audio_quality, video_quality, cookies_has_google,
        ),
        daemon=True,
    )
    app.download_thread.start()


def cancel_download(app):
    app.cancel_requested = True
    if app.current_process and app.current_process.poll() is None:
        try:
            app.log("⏹ terminate yt-dlp...", tag="warn")
            app.current_process.terminate()
        except Exception as e:
            app.log(f"⚠️ {e}", tag="err")


def download_worker(
    app, url, fmt, download_path, audio_only, v_cont,
    a_cont, ffmpeg_path, cookies_path, mode, from_queue,
    subs_enabled, subs_lang, subs_auto, subs_srt, audio_quality, video_quality, cookies_has_google,
):
    try:
        outtmpl = app._get_output_template_with_choice(download_path, "%(ext)s")

        ytdlp_cmd = app.get_ytdlp_cmd()
        app._enqueue_ui(
            lambda c=ytdlp_cmd: app.log(
                f"ℹ️ yt-dlp selected: {c[0]} [{app._describe_ytdlp_source(c)}]",
                tag="ok",
            )
        )
        run_env = _ytdlp_env(app)
        try:
            ver_proc = subprocess.run(
                ytdlp_cmd + ["--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
                env=run_env,
                **subprocess_window_kwargs(),
            )
            version = (ver_proc.stdout or "").strip()
            if ver_proc.returncode == 0 and version:
                app._enqueue_ui(lambda v=version: app.log(f"ℹ️ yt-dlp version: {v}", tag="ok"))
            elif ver_proc.returncode != 0 and ver_proc.stderr:
                app._enqueue_ui(lambda e=ver_proc.stderr.strip(): app.log(f"⚠️ yt-dlp version check failed: {e}", tag="warn"))
        except Exception as e:
            app._enqueue_ui(lambda err=str(e): app.log(f"⚠️ yt-dlp version check error: {err}", tag="warn"))

        cmd = ytdlp_cmd + [
            "--extractor-args", "youtube:player_client=web,web_safari",
            "--extractor-retries", "3",
            "--retries", "3",
            url,
            "-o", outtmpl,
        ]

        if ffmpeg_path:
            cmd += ["--ffmpeg-location", ffmpeg_path]
        if cookies_path:
            cmd += ["--cookies", cookies_path]

        if ffmpeg_path:
            cmd += ["--ffmpeg-location", ffmpeg_path]
        if cookies_path:
            cmd += ["--cookies", cookies_path]

        if mode == "only_subtitles":
            cmd += ["--skip-download"]
        elif audio_only:
            cmd += ["--extract-audio", "--audio-format", a_cont, "-f", fmt]
            if audio_quality != "best":
                cmd += ["--audio-quality", f"{audio_quality}K"]
                if a_cont in ("mp3", "m4a"):
                    cmd += ["--postprocessor-args", f"-b:a {audio_quality}k"]

        else:
            cmd += ["-f", fmt]
            if v_cont in ("mp4", "webm"):
                cmd += ["--merge-output-format", v_cont, "--remux-video", v_cont]
            if video_quality != "best":
                cmd += ["-S", f"res:{video_quality},vcodec,acodec"]
        if app.auto_number_files:
            start_no = int(getattr(app, "autonumber_next", 1) or 1)
            cmd += ["--autonumber-start", str(start_no)]
        else:
            # Safety: never silently overwrite existing files when numbering is off.
            cmd += ["--no-overwrites"]

        if subs_enabled:
            langs = subs_lang
            if langs == "auto":
                langs = "ru" if app.lang == "ru" else "en"
            elif langs == "all":
                langs = "all,-live_chat"
            cmd += ["--write-subs", "--sub-langs", langs]
            if subs_auto:
                cmd += ["--write-auto-subs"]
            if subs_srt:
                cmd += ["--convert-subs", "srt"]

        # Enable JS challenge solving proactively (YouTube EJS)
        cmd = _add_js_runtime_hints(cmd, app)

        if app.auto_number_files:
            sn = int(getattr(app, "autonumber_next", 1) or 1)
            app._enqueue_ui(
                lambda s=sn: app.log(
                    f"ℹ️ Автонумерация: старт yt-dlp = {s} (сохранённый счётчик autonumber_next)",
                    tag="ok",
                )
            )

        app._enqueue_ui(lambda: app.log(app._t("log_cmd").format(cmd=" ".join(cmd))))

        def run_attempt(attempt_cmd):
            captured = []
            app.current_process = subprocess.Popen(
                attempt_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
                env=run_env,
                **subprocess_window_kwargs(),
            )

            assert app.current_process.stdout is not None
            for line in app.current_process.stdout:
                if app.cancel_requested:
                    break
                line = line.rstrip("\n")
                captured.append(line)

                m = app.progress_re.search(line)
                if m:
                    try:
                        pct = float(m.group(1))
                    except ValueError:
                        pct = 0.0
                    app._enqueue_ui(lambda p=pct: app.progress.configure(value=p))

                app._enqueue_ui(lambda l=line: app.log(l))

            ret_code = app.current_process.wait()
            app.current_process = None
            app._enqueue_ui(lambda r=ret_code: app.log(f"ℹ️ yt-dlp exit code: {r}"))
            return ret_code, captured

        ret, output_lines = run_attempt(cmd)

        def retry_with_browser_cookies(base_cmd):
            nonlocal ret, output_lines
            app._enqueue_ui(lambda: app.log("⚠️ YouTube bot-check detected, retrying with browser cookies...", tag="warn"))
            retry_base = _strip_flag_with_value(base_cmd, "--cookies")
            retry_base = _replace_flag_with_value(
                retry_base,
                "--extractor-args",
                "youtube:player_client=web,web_safari"
            )
            for browser in ("chrome", "edge", "firefox", "brave"):
                if app.cancel_requested:
                    break
                if not _browser_cookie_source_available(browser):
                    app._enqueue_ui(lambda b=browser: app.log(f"ℹ️ Browser profile not found for {b}, skipping.", tag="warn"))
                    continue
                if _looks_like_dpapi_failure(output_lines) and browser in ("edge",):
                    app._enqueue_ui(lambda: app.log("ℹ️ Skipping Edge retry after DPAPI failure.", tag="warn"))
                    continue
                retry_cmd = retry_base + ["--cookies-from-browser", browser]
                app._enqueue_ui(lambda b=browser: app.log(f"ℹ️ Retry with --cookies-from-browser {b}", tag="ok"))
                app._enqueue_ui(lambda c=retry_cmd: app.log(app._t("log_cmd").format(cmd=" ".join(c))))
                ret, output_lines = run_attempt(retry_cmd)
                if ret == 0:
                    return
                if browser == "chrome" and _looks_like_dpapi_failure(output_lines):
                    app._enqueue_ui(
                        lambda: app.log(
                            "⚠️ Chrome cookies недоступны из-за DPAPI. "
                            "Пропускаю остальные browser-retry и жду новый cookies.txt с google.com + youtube.com.",
                            tag="warn",
                        )
                    )
                    return

            if _looks_like_dpapi_failure(output_lines):
                app._enqueue_ui(
                    lambda: app.log(
                        "⚠️ DPAPI мешает взять cookies из Chrome/Edge. "
                        "Лучший вариант: переэкспортируй cookies.txt (Netscape) из залогиненного профиля YouTube "
                        "или установи Firefox, залогинься и используй --cookies-from-browser firefox.",
                        tag="warn",
                    )
                )

        if ret != 0 and _looks_like_youtube_bot_check(output_lines):
            # If user provided cookies.txt but web client still triggers bot-check,
            # try alternate clients first (often avoids DPAPI/browser extraction).
            if "--cookies" in cmd:
                alt_cmd = _retry_botcheck_with_alt_clients(app, cmd, audio_only)
                ret, output_lines = run_attempt(alt_cmd)
            if ret != 0 and _looks_like_youtube_bot_check(output_lines) and cookies_has_google:
                retry_with_browser_cookies(cmd)
            elif ret != 0 and _looks_like_youtube_bot_check(output_lines) and not cookies_has_google:
                app._enqueue_ui(
                    lambda: app.log(
                        "⚠️ Останавливаю авто-ретраи: текущий cookies.txt без google.com cookies. "
                        "Сначала переэкспортируй cookies, затем повтори.",
                        tag="warn",
                    )
                )

        if ret != 0 and _looks_like_signature_or_format_failure(output_lines):
            app._enqueue_ui(lambda: app.log("⚠️ Signature/format failure detected, retrying with alternate clients...", tag="warn"))
            base_retry_cmd = cmd[:]
            base_retry_cmd = _replace_flag_with_value(
                base_retry_cmd,
                "--extractor-args",
                "youtube:player_client=android,web"
            )
            base_retry_cmd = _with_soft_video_format(base_retry_cmd, audio_only)
            base_retry_cmd = _add_js_runtime_hints(base_retry_cmd, app)

            app._enqueue_ui(lambda c=base_retry_cmd: app.log(app._t("log_cmd").format(cmd=" ".join(c))))
            ret, output_lines = run_attempt(base_retry_cmd)

        if ret != 0 and _looks_like_signature_or_format_failure(output_lines):
            app._enqueue_ui(
                lambda: app.log(
                    "⚠️ Still signature-limited. Retrying without cookies on TV/Android clients...",
                    tag="warn",
                )
            )
            no_cookie_retry = _strip_flag_with_value(cmd, "--cookies")
            no_cookie_retry = _replace_flag_with_value(
                no_cookie_retry,
                "--extractor-args",
                "youtube:player_client=tv,android,web",
            )
            no_cookie_retry = _with_soft_video_format(no_cookie_retry, audio_only)
            no_cookie_retry = _add_js_runtime_hints(no_cookie_retry, app)
            app._enqueue_ui(lambda c=no_cookie_retry: app.log(app._t("log_cmd").format(cmd=" ".join(c))))
            ret, output_lines = run_attempt(no_cookie_retry)

        if ret != 0 and _looks_like_youtube_bot_check(output_lines):
            retry_with_browser_cookies(cmd)

        if ret != 0 and _looks_like_dpapi_failure(output_lines):
            app._enqueue_ui(
                lambda: app.log(
                    "⚠️ DPAPI decrypt failure for browser cookies. "
                    "Try running app and browser under same Windows user/session or re-export cookies.txt.",
                    tag="warn",
                )
            )

        if ret != 0 and _looks_like_signature_or_format_failure(output_lines):
            app._enqueue_ui(
                lambda: app.log(
                    (
                        "⚠️ Challenge solving is still failing. "
                        + ("Node.js не найден — установи Node.js (LTS) или положи node.exe рядом с приложением, затем повтори."
                           if not _get_node_for_app(app)
                           else "Похоже, YouTube режет форматы даже при наличии JS runtime — попробуй обновить yt-dlp и переэкспортировать cookies.txt.")
                    ),
                    tag="warn",
                )
            )

        if app.cancel_requested:
            def on_cancel():
                app.log(app._t("download_canceled"), tag="warn")
                app.status_label.config(text=app._t("status_canceled"))
                app.progress.configure(value=0)
            app._enqueue_ui(on_cancel)
            return

        if ret != 0:
            def on_error():
                app.log(app._t("cli_error").format(code=ret), tag="err")
                messagebox.showerror(app._t("download_error_title"), app._t("download_error_box"))
                app.status_label.config(text=app._t("status_error"))
                app.progress.configure(value=0)
                app.notebook.select(app.tab_log)
            app._enqueue_ui(on_error)
        else:
            # Estimate number of produced files to advance autonumber across runs.
            produced = _estimate_produced_files(output_lines)

            def on_ok():
                app.log(app._t("download_done_log"), tag="ok")
                app.status_label.config(text=app._t("status_done"))
                app.progress.configure(value=100)
                if (not app.auto_number_files) and _looks_like_no_overwrite_skip(output_lines):
                    app.log(
                        "ℹ️ Файл уже существует — перезапись запрещена. "
                        "Включи нумерацию файлов, если хочешь сохранять копии.",
                        tag="warn",
                    )
                if app.auto_number_files:
                    try:
                        prev = int(getattr(app, "autonumber_next", 1) or 1)
                        inc = int(produced) if int(produced) > 0 else 1
                        app.autonumber_next = prev + inc
                        app.log(
                            f"ℹ️ Автонумерация: +{inc} → следующий номер будет {app.autonumber_next}",
                            tag="ok",
                        )
                    except Exception:
                        app.autonumber_next = int(getattr(app, "autonumber_next", 1) or 1) + 1
                        app.log(
                            f"ℹ️ Автонумерация: счётчик → {app.autonumber_next}",
                            tag="ok",
                        )
                app.save_settings()
                app.add_history_entry(url, mode, download_path)
                if app.open_folder_after_download and os.name == "nt":
                    try:
                        os.startfile(download_path)
                    except Exception as e:
                        app.log(app._t("open_folder_fail").format(err=e), tag="err")
            app._enqueue_ui(on_ok)

    except Exception as e:
        msg = str(e)

        def on_exc():
            app.log(app._t("common_error").format(msg=msg), tag="err")
            messagebox.showerror(app._t("error_title"), msg)
            app.status_label.config(text=app._t("status_error"))
            app.progress.configure(value=0)
            app.notebook.select(app.tab_log)
        app._enqueue_ui(on_exc)

    finally:
        def on_finish():
            if not from_queue:
                app.download_button.config(state="normal", text=app._t("download"))
            app.cancel_button.config(state="disabled")
        app._enqueue_ui(on_finish)
        app.current_process = None
        app.cancel_requested = False
