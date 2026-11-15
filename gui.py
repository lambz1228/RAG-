"""生物领域知识库系统 · 桌面入口。

Flask API 在后台线程运行，Chrome/Edge 以 App Mode 打开独立窗口。
支持 PyInstaller 打包为 exe。

用法:
    python gui.py              # 开发模式
    build.bat                  # 打包为 dist/BioKB.exe
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path


def _bundle_root() -> Path:
    """开发模式=仓库根；PyInstaller onefile=解压目录。"""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def _runtime_root() -> Path:
    """可写目录：exe 旁 / 源码根。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


_BUNDLE = _bundle_root()
_RUNTIME = _runtime_root()
_SYS_DIR = _BUNDLE / "src"

for _p in (str(_SYS_DIR), str(_BUNDLE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 运行态库放到 exe 旁，避免写进只读解压目录
from biokb.config import Settings  # noqa: E402

_WINDOW_SIZE = "1400,900"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_ready(url: str, timeout: float = 45.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=1)
            return True
        except Exception:
            time.sleep(0.4)
    return False


def _find_browser() -> str | None:
    candidates = [
        os.environ.get("BIOKB_BROWSER_PATH", "").strip(),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        shutil.which("msedge") or "",
        shutil.which("chrome") or "",
    ]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    return None


def _start_flask(port: int, settings: Settings) -> None:
    from dataclasses import replace

    from waitress import serve

    from biokb.web.app import create_app

    application = create_app(replace(settings, host="127.0.0.1", port=port))
    print(f"[BioKB] http://127.0.0.1:{port}")
    serve(application, host="127.0.0.1", port=port, threads=4)


def _open_window(browser: str, url: str) -> subprocess.Popen:
    user_data = _RUNTIME / ".gui_browser_profile"
    user_data.mkdir(exist_ok=True)
    args = [
        browser,
        f"--app={url}",
        f"--window-size={_WINDOW_SIZE}",
        f"--user-data-dir={str(user_data)}",
        "--disable-extensions",
        "--no-first-run",
        "--disable-default-apps",
    ]
    print(f"[GUI] 打开窗口: {browser}")
    return subprocess.Popen(args)


def main() -> int:
    settings = Settings.from_env(_RUNTIME)
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"

    thread = threading.Thread(
        target=_start_flask, args=(port, settings), daemon=True, name="FlaskServer"
    )
    thread.start()

    if not _wait_ready(url):
        print("[ERROR] Flask 启动超时")
        if not getattr(sys, "frozen", False):
            input("按回车退出...")
        return 1

    print(f"[Ready] {url}")
    browser = _find_browser()
    if not browser:
        print("[WARN] 未找到 Edge/Chrome，请用浏览器打开上述地址")
        try:
            while thread.is_alive():
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        return 0

    proc = _open_window(browser, url)
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    print("[GUI] 窗口已关闭")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
