"""Offline, authorization-gated security master snapshot import."""
import sys

sys.dont_write_bytecode = True
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.phase_c_safety import main


if __name__ == "__main__":
    raise SystemExit(main("015-04"))
