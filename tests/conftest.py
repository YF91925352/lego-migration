import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE_RAW = ROOT / "tests" / "fixtures" / "raw"
MAPPING = ROOT / "config" / "mapping.json"


@pytest.fixture
def workdir(tmp_path):
    """A fresh copy of the (invented) fixture CSVs in a temp folder."""
    raw = tmp_path / "raw"
    shutil.copytree(FIXTURE_RAW, raw)
    return tmp_path


def export_and_migrate(workdir: Path, defects: int = 0, crash_after=None):
    from legomig.legacy_export import export
    from legomig.migrate import run

    legacy, out = workdir / "out" / "legacy", workdir / "out"
    if not (legacy / "sets.jsonl").exists():
        export(workdir / "raw", legacy, defects_per_type=defects, seed=42)
    return run(legacy, out, MAPPING, crash_after=crash_after)
