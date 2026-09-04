import os
import shutil
import subprocess


def subprocess_window_kwargs() -> dict:
    if os.name != "nt":
        return {}
    kwargs: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    kwargs["startupinfo"] = startupinfo
    return kwargs


def has_executable(exe: str) -> bool:
    return bool(shutil.which(exe) or shutil.which(exe + ".exe"))
