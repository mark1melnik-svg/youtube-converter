import os
import shutil
import subprocess
import sys
import importlib.util
from services.platform_utils import subprocess_window_kwargs


def _find_first_existing(candidates: list[str]) -> str | None:
    seen: set[str] = set()
    for p in candidates:
        if not p:
            continue
        q = os.path.normpath(p)
        if q in seen:
            continue
        seen.add(q)
        if os.path.isfile(p):
            return p
    return None


def get_node_cmd(app) -> str | None:
    roots = _frozen_binary_roots(app)
    roots.append(app.get_executable_dir())
    roots.append(os.getcwd())

    candidates: list[str] = []
    for base_dir in roots:
        candidates.extend(
            [
                os.path.join(base_dir, "node.exe"),
                os.path.join(base_dir, "node"),
                os.path.join(base_dir, "node", "node.exe"),
                os.path.join(base_dir, "node", "node"),
                os.path.join(base_dir, "bin", "node.exe"),
                os.path.join(base_dir, "bin", "node"),
            ]
        )

    local = _find_first_existing(candidates)
    if local:
        return local

    return shutil.which("node") or shutil.which("node.exe")


def _get_node_version(app) -> tuple[str | None, str | None]:
    try:
        node = get_node_cmd(app)
        if not node:
            return None, None
        proc = subprocess.run(
            [node, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
            **subprocess_window_kwargs(),
        )
        if proc.returncode != 0:
            return None, node
        ver = (proc.stdout or "").strip()
        return (ver or None), node
    except Exception:
        return None, None


def _analyze_cookies_txt(cookies_path: str) -> dict:
    """
    Best-effort validation for Netscape cookies.txt.
    Returns dict with signals used for UI logging.
    """
    result = {
        "exists": False,
        "is_netscape": False,
        "youtube_lines": 0,
        "google_lines": 0,
        "names": set(),
        "looks_like_auth": False,
    }
    try:
        if not os.path.isfile(cookies_path):
            return result
        result["exists"] = True

        with open(cookies_path, "r", encoding="utf-8", errors="ignore") as f:
            head = f.read(4096)
            result["is_netscape"] = "netscape http cookie file" in head.lower()
            rest = f.read(400_000)
        text = (head + rest).splitlines()

        # Netscape format: domain \t flag \t path \t secure \t expiration \t name \t value
        for line in text:
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain = (parts[0] or "").lower()
            name = (parts[5] or "").strip()
            if not name:
                continue
            if "youtube.com" in domain:
                result["youtube_lines"] += 1
            if "google.com" in domain:
                result["google_lines"] += 1
            if result["youtube_lines"] or result["google_lines"]:
                result["names"].add(name.upper())

        required_any = {
            "__SECURE-1PSID",
            "__SECURE-3PSID",
            "SID",
            "HSID",
            "SSID",
            "APISID",
            "SAPISID",
        }
        present = result["names"]
        # For YouTube auth, google.com session cookies are usually required.
        # youtube-only exports often trigger "Sign in to confirm you're not a bot".
        result["looks_like_auth"] = (
            bool(present.intersection(required_any))
            and result["youtube_lines"] >= 1
            and result["google_lines"] >= 1
        )
        return result
    except Exception:
        return result


def _has_ytdlp_ejs() -> bool:
    try:
        # Package name from upstream repo: yt-dlp-ejs (import name: yt_dlp_ejs)
        return importlib.util.find_spec("yt_dlp_ejs") is not None
    except Exception:
        return False


def get_executable_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _frozen_binary_roots(app) -> list[str]:
    """
    Where bundled tools live. PyInstaller one-dir puts binaries under sys._MEIPASS (.../_internal);
    dirname(sys.executable) is the folder with the launcher .exe — check both.
    """
    roots: list[str] = []
    seen: set[str] = set()
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            p = os.path.normpath(meipass)
            if p not in seen:
                seen.add(p)
                roots.append(p)
        exe_dir = os.path.normpath(os.path.dirname(sys.executable))
        if exe_dir not in seen:
            seen.add(exe_dir)
            roots.append(exe_dir)
        # PyInstaller one-dir layout keeps binaries in "<exe_dir>/_internal".
        internal_dir = os.path.normpath(os.path.join(exe_dir, "_internal"))
        if internal_dir not in seen:
            seen.add(internal_dir)
            roots.append(internal_dir)
    else:
        p = os.path.normpath(app.get_executable_dir())
        if p not in seen:
            roots.append(p)
    return roots


def get_ffmpeg_path(app):
    """
    Ищет ffmpeg.exe: сначала строго локально в папках программы,
    а если не находит — заглядывает в глобальный Windows PATH.
    """
    roots = _frozen_binary_roots(app)
    for base_dir in roots:
        ffmpeg_path = os.path.join(base_dir, "ffmpeg.exe")
        if os.path.isfile(ffmpeg_path):
            return ffmpeg_path

    # Дополнительно проверяем текущий рабочий каталог
    cwd_ffmpeg = os.path.join(os.getcwd(), "ffmpeg.exe")
    if os.path.isfile(cwd_ffmpeg):
        return cwd_ffmpeg

    # Если локально пусто, ищем в глобальной системе Windows
    system_ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if system_ffmpeg:
        return system_ffmpeg

    hint = roots[0] if roots else app.get_executable_dir()
    app.log(app._t("ffmpeg_not_found").format(base=hint), tag="warn")
    return None


def get_ffmpeg_path(app):
    """
    Ищет ffmpeg.exe: сначала локально (в корне или в папке bin), 
    а затем в глобальной системе Windows PATH.
    """
    roots = _frozen_binary_roots(app)
    
    # Собираем все локальные варианты папок для поиска
    all_dirs = roots + [os.getcwd()]
    
    for base_dir in all_dirs:
        # Проверяем и в самом корне, и внутри папки "bin"
        candidates = [
            os.path.join(base_dir, "ffmpeg.exe"),
            os.path.join(base_dir, "bin", "ffmpeg.exe")
        ]
        for p in candidates:
            if os.path.isfile(p):
                return p

    # Если локально пусто, ищем в глобальной системе Windows
    system_ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if system_ffmpeg:
        return system_ffmpeg

    hint = roots if roots else app.get_executable_dir()
    app.log(app._t("ffmpeg_not_found").format(base=hint), tag="warn")
    return None

def get_ytdlp_cmd(app) -> list[str]:
    """
    Ищет yt-dlp.exe: сначала локально (в корне или в папке bin),
    а затем подтягивает глобальную утилиту из системы.
    """
    roots = _frozen_binary_roots(app)
    all_dirs = roots + [os.getcwd()]
    
    candidates: list[str] = []
    for base_dir in all_dirs:
        candidates.extend(
            [
                os.path.join(base_dir, "yt-dlp.exe"),
                os.path.join(base_dir, "yt-dlp.EXE"),
                os.path.join(base_dir, "bin", "yt-dlp.exe"),      # <-- Ищем в папке bin
                os.path.join(base_dir, "bin", "yt-dlp.EXE"),      # <-- Ищем в папке bin
                os.path.join(base_dir, "yt-dlp"),
                os.path.join(base_dir, "ytdlp.exe"),
                os.path.join(base_dir, "ytdlp"),
            ]
        )

    seen: set[str] = set()
    for candidate in candidates:
        q = os.path.normpath(candidate) if candidate else ""
        if not q or q in seen:
            continue
        seen.add(q)
        if os.path.isfile(candidate):
            return [candidate]

    # Если локально утилиты нет — берём глобальную из Windows PATH
    found = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if found:
        return [found]

    return ["yt-dlp"]




def describe_ytdlp_source(app, ytdlp_cmd: list[str]) -> str:
    if not ytdlp_cmd:
        return "unknown"
    cmd0 = ytdlp_cmd[0]
    try:
        cmd_abs = os.path.abspath(cmd0)
        cwd_abs = os.path.abspath(os.getcwd())
        for base in _frozen_binary_roots(app):
            base_abs = os.path.abspath(base)
            if cmd_abs.startswith(base_abs):
                return "local (app dir)"
        if cmd_abs.startswith(cwd_abs):
            return "local (cwd)"
    except Exception:
        pass
    if os.path.isabs(cmd0):
        return "PATH (resolved absolute)"
    return "PATH (command name)"


def get_cookies_path(app):
    def pick_from_dir(dir_path: str) -> str | None:
        try:
            if not dir_path or not os.path.isdir(dir_path):
                return None

            # Prefer explicit / youtube-only exports first, then fall back.
            patterns = [
                "cookies.txt",
                "www.youtube.com_cookies.txt",
                "*youtube*_cookies*.txt",
                "*youtube*cookies*.txt",
                "cookies*.txt",
            ]
            candidates: list[str] = []
            for pat in patterns:
                try:
                    # Use os.scandir + fnmatch to avoid glob quirks on Windows paths
                    import fnmatch

                    for ent in os.scandir(dir_path):
                        if not ent.is_file():
                            continue
                        if fnmatch.fnmatch(ent.name.lower(), pat.lower()):
                            candidates.append(ent.path)
                except Exception:
                    continue

            if not candidates:
                return None

            candidates = list(dict.fromkeys(candidates))  # de-dupe while preserving order

            # Pick the best cookies file by inspecting contents (avoid grabbing random .txt)
            best_path = None
            best_score = -1
            for p in candidates:
                info = _analyze_cookies_txt(p)
                if not info.get("exists"):
                    continue
                # Hard filter: must be Netscape OR at least contain youtube/google cookie lines
                if not info.get("is_netscape") and (info.get("youtube_lines", 0) + info.get("google_lines", 0) == 0):
                    continue

                score = 0
                if info.get("is_netscape"):
                    score += 20
                if info.get("looks_like_auth"):
                    score += 100
                elif info.get("youtube_lines", 0) > 0 and info.get("google_lines", 0) == 0:
                    score -= 40
                score += min(30, info.get("youtube_lines", 0))
                score += min(30, info.get("google_lines", 0))
                try:
                    # Prefer fresher exports when quality is similar
                    score += int(min(20, os.path.getmtime(p) // 1_000_000_000))
                except Exception:
                    pass

                if score > best_score:
                    best_score = score
                    best_path = p

            if best_path:
                return best_path

            # As a last resort, prefer newest among named candidates
            candidates.sort(key=lambda x: os.path.getmtime(x), reverse=True)
            return candidates[0]
        except Exception:
            return None

    # 1) If user set a path, it may be a file OR a directory.
    if getattr(app, "cookies_dir", None):
        p = app.cookies_dir
        try:
            if os.path.isfile(p):
                return p
            if os.path.isdir(p):
                picked = pick_from_dir(p)
                if picked:
                    return picked
            # If they pasted something like ".../cookies" without extension, try a few fixes
            if os.path.isfile(p + ".txt"):
                return p + ".txt"
        except Exception:
            pass

    # 2) Fall back to app directory
    base_dir = app.get_executable_dir()
    picked = pick_from_dir(base_dir)
    if picked:
        return picked

    return None


def check_environment(app):
    ytdlp_status = "❌"
    ytdlp_extra = ""
    try:
        ytdlp_cmd = app.get_ytdlp_cmd()
        app.log(f"🔍 Проверка yt-dlp: {ytdlp_cmd}", tag="ok")
        proc = subprocess.run(
            ytdlp_cmd + ["--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="ignore",
            check=False,
            **subprocess_window_kwargs(),
        )

        if proc.returncode != 0:
            app.log(f"❌ yt-dlp вернул код {proc.returncode}", tag="err")
            app.log(f"   stderr: {proc.stderr}", tag="err")
        else:
            ver = (proc.stdout or "").strip()
            ytdlp_status = "✅"
            if ver:
                ytdlp_extra = f" {ver}"
                app.log(f"✅ yt-dlp найден: {ver}", tag="ok")
            else:
                app.log("✅ yt-dlp найден (версия неизвестна)", tag="ok")

    except FileNotFoundError as e:
        app.log(f"❌ yt-dlp не найден: {e}", tag="err")
    except Exception as e:
        app.log(f"❌ Ошибка при проверке yt-dlp: {e}", tag="err")

    app.env_ytdlp_label.config(text=f"yt-dlp: {ytdlp_status}{ytdlp_extra}")

    ffmpeg_status = "❌"
    try:
        ffmpeg_path = app.get_ffmpeg_path()
        if ffmpeg_path:
            app.log(f"✅ ffmpeg найден локально: {ffmpeg_path}", tag="ok")
            ffmpeg_status = "✅"
        else:
            proc = subprocess.run(
                ["ffmpeg", "-version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="ignore",
                check=False,
                **subprocess_window_kwargs(),
            )
            if proc.returncode == 0:
                ffmpeg_status = "✅"
                app.log("✅ ffmpeg найден в системном PATH", tag="ok")
            else:
                app.log("❌ ffmpeg не найден в PATH", tag="warn")
    except FileNotFoundError:
        app.log("⚠️ ffmpeg не найден (не критично для аудио)", tag="warn")
    except Exception as e:
        app.log(f"⚠️ Ошибка при проверке ffmpeg: {e}", tag="warn")

    app.env_ffmpeg_label.config(text=f"ffmpeg: {ffmpeg_status}")

    node_ver, node_cmd = _get_node_version(app)
    if node_ver:
        src = "PATH"
        try:
            if node_cmd:
                n_abs = os.path.abspath(node_cmd)
                for base in _frozen_binary_roots(app):
                    if n_abs.startswith(os.path.abspath(base)):
                        src = "local (app dir)"
                        break
        except Exception:
            pass
        app.log(f"✅ Node.js найден: {node_ver} [{src}]", tag="ok")
        app.env_node_label.config(text=f"node: ✅ {node_ver}")
    else:
        app.log("ℹ️ Node.js не найден (важно для YouTube signature/EJS)", tag="warn")
        app.env_node_label.config(text="node: ❌")

    if _has_ytdlp_ejs():
        app.log("✅ yt-dlp-ejs установлен (EJS solver scripts)", tag="ok")
    else:
        # Optional package; noisy as WARN — keep actionable but not alarming.
        app.log("ℹ️ yt-dlp-ejs не найден (опционально): pip install -U yt-dlp-ejs", tag="ok")

    cookies_path = app.get_cookies_path()
    if cookies_path:
        info = _analyze_cookies_txt(cookies_path)
        if info["looks_like_auth"]:
            app.log(
                f"✅ cookies.txt найден (auth ok; yt={info['youtube_lines']}, google={info['google_lines']}): {cookies_path}",
                tag="ok",
            )
            app.env_cookies_label.config(text="cookies.txt: ✅ (auth)")
        else:
            if not info["is_netscape"]:
                app.log(f"⚠️ cookies.txt найден, но формат не Netscape (нужен cookies.txt): {cookies_path}", tag="warn")
                app.env_cookies_label.config(text="cookies.txt: ⚠ (format)")
            elif info["youtube_lines"] > 0 and info["google_lines"] == 0:
                app.log(
                    f"⚠️ cookies.txt найден, но в нем нет google.com cookies (yt={info['youtube_lines']}, google=0): {cookies_path}. "
                    "Переэкспортируй cookies с включенными *.google.com + *.youtube.com.",
                    tag="warn",
                )
                app.env_cookies_label.config(text="cookies.txt: ⚠ (no google auth)")
            else:
                app.log(
                    f"⚠️ cookies.txt найден, но auth cookies мало/нет (yt={info['youtube_lines']}, google={info['google_lines']}): {cookies_path}",
                    tag="warn",
                )
                app.env_cookies_label.config(text="cookies.txt: ⚠ (weak auth)")
    else:
        app.log("ℹ️ cookies.txt не найден (не обязателен)", tag="warn")
        app.env_cookies_label.config(text="cookies.txt: ❌")
