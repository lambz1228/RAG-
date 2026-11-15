"""无需安装包即可从源码运行完整 CLI。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from biokb.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
