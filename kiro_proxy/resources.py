import sys
from pathlib import Path


def get_resource_path(relative_path: str) -> Path:
    base_path = Path(sys._MEIPASS) if hasattr(sys, "_MEIPASS") else Path(__file__).parent.parent
    return base_path / relative_path
