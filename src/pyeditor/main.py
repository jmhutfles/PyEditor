from __future__ import annotations

import sys
from pathlib import Path

if __package__ in {None, ""}:
    workspace_root = Path(__file__).resolve().parents[2]
    workspace_root_text = str(workspace_root)
    if workspace_root_text not in sys.path:
        sys.path.insert(0, workspace_root_text)
    from src.pyeditor.main_window import MainWindow
else:
    from .main_window import MainWindow



def main() -> int:
    window = MainWindow()
    window.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
