"""The AI mapping helper: prompt contents and the human-review comparison (no API call)."""
import importlib.util
import json

from conftest import MAPPING, ROOT

spec = importlib.util.spec_from_file_location("suggest_mapping", ROOT / "scripts" / "suggest_mapping.py")
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)


def test_prompt_lists_target_columns_but_not_derived_ones(workdir):
    from legomig.legacy_export import export
    export(workdir / "raw", workdir / "legacy")
    prompt = sm.build_prompt(sm.legacy_samples(workdir / "legacy"))
    assert '"PieceCount"' in prompt and '"num_parts"' in prompt
    assert '"decade"' not in prompt


def test_comparison_flags_disagreements_for_review():
    confirmed = json.loads(MAPPING.read_text())
    suggested = {"sets": {"fields": {
        "SetNo": {"to": "set_num", "type": "str", "confidence": 0.99},
        "Yr": {"to": "year", "type": "str", "confidence": 0.6, "note": "looks like text"},
    }}}
    lines = "\n".join(sm.compare(suggested, {"sets": confirmed["sets"]}))
    assert "agree    sets.SetNo -> set_num" in lines
    assert "DIFFERS  sets.Yr" in lines
    assert "MISSING  sets.ThemeRef" in lines
