"""用当前解释器验证安装包、源码入口，所有服务使用临时数据。"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def verify(output: Path) -> dict:
    report = {"passed": False, "checks": []}
    environment = {key: value for key, value in os.environ.items() if not key.startswith("BIOKB_")}
    environment.update(
        BIOKB_HOST="127.0.0.1",
        BIOKB_LLM_BACKEND="disabled",
        BIOKB_EMBEDDING_BACKEND="hash",
        BIOKB_EMBEDDING_MODEL="local-hash-v2",
        BIOKB_RETRIEVAL="hybrid",
        BIOKB_RERANKER_BACKEND="disabled",
        BIOKB_EMBEDDING_API_KEY="",
        BIOKB_EMBEDDING_API_KEY_ENV="",
        BIOKB_LLM_API_KEY="",
        BIOKB_LLM_API_KEY_ENV="",
        BIOKB_RERANKER_API_KEY="",
        BIOKB_RERANKER_API_KEY_ENV="",
        PYTHONIOENCODING="utf-8",
    )
    with tempfile.TemporaryDirectory(prefix="biokb-runtime-") as temporary:
        working = Path(temporary)
        smoke = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                "import json,sys; from pathlib import Path; import biokb; from biokb.config import Settings; s=Settings.from_env(); assert Path(biokb.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); assert s.data_dir == Path.cwd().resolve()/'data'/'biokb'; print(json.dumps({'module':biokb.__file__,'data_root_correct':True}))",
            ],
            cwd=working,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            check=True,
        )
        report["installed_package"] = json.loads(smoke.stdout)
        report["checks"].append("隔离解释器从 site-packages 加载 wheel，数据根目录为工作目录")
        for label in ("wheel_cli", "source_app"):
            port = free_port()
            environment.update(BIOKB_PORT=str(port), BIOKB_DATA_DIR=str(working / label))
            if label == "wheel_cli":
                command = [sys.executable, "-I", "-m", "biokb", "serve"]
            else:
                command = [sys.executable, str(ROOT / "app.py")]
            process = subprocess.Popen(
                command,
                cwd=working,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            try:
                url = f"http://127.0.0.1:{port}"
                deadline = time.monotonic() + 20
                while True:
                    try:
                        with urllib.request.urlopen(url + "/api/health", timeout=1) as response:
                            assert json.load(response)["status"] == "ok"
                        break
                    except (urllib.error.URLError, TimeoutError):
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise AssertionError(f"启动失败：{label}")
                        time.sleep(0.1)
                for path in (
                    "/",
                    "/static/app.css",
                    "/static/main.js",
                    "/static/shell.js",
                    "/static/i18n.js",
                    "/static/messages.js",
                    "/static/papers.js",
                    "/static/literature.js",
                    "/api/literature/topics",
                    "/api/dashboard",
                    "/api/config",
                    "/api/papers",
                ):
                    with urllib.request.urlopen(url + path, timeout=5) as response:
                        assert response.status == 200
                        if path == "/api/dashboard":
                            assert json.load(response)["stats"]["documents"] == 0
                        elif path == "/api/papers":
                            assert json.load(response)["summary"]["papers"] == 0
                        elif path.startswith("/static/"):
                            assert (
                                response.read() == (ROOT / "src/biokb/web" / path[1:]).read_bytes()
                            )
                report["checks"].append(label + "：Waitress、页面、静态资源与 API 可用")
            finally:
                process.terminate()
                try:
                    process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)
    report["passed"] = True
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/refactor/runtime_report.json")
    print(json.dumps(verify(parser.parse_args().output), ensure_ascii=False, indent=2))
