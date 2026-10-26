"""Ask an LLM to *suggest* a legacy-field -> target-column mapping, for a human to review.

The model sees only field names and a few sample values from the legacy export, plus
the target schema. Its answer is written to config/mapping.suggested.json and compared
field by field with the human-confirmed config/mapping.json. The pipeline never reads
the suggestion: a person merges what they agree with.

    python scripts/suggest_mapping.py --dry-run     # print the prompt, no API call
    ANTHROPIC_API_KEY=... python scripts/suggest_mapping.py
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from legomig.schema import TABLES  # noqa: E402

TYPES = {
    "str": "non-empty string", "int": "integer", "int_optional": "integer or empty",
    "yn_bool": "'Y'/'N' -> boolean", "hex_rgb": "'#RRGGBB' -> 'RRGGBB'",
}
DERIVED = {"decade"}  # computed by the pipeline, not mapped


def legacy_samples(legacy_dir: Path, n: int = 3) -> dict:
    catalog = json.loads((legacy_dir / "catalog.json").read_text(encoding="utf-8"))
    out = {f"catalog.json#{k}": v[:n] for k, v in catalog.items() if isinstance(v, list)}
    with open(legacy_dir / "sets.jsonl", encoding="utf-8") as f:
        sets = [json.loads(next(f)) for _ in range(n)]
    out["sets.jsonl"] = [{k: v for k, v in s.items() if k != "Inventory"} for s in sets]
    out["sets.jsonl#Inventory (each line also inherits SetNo from its set)"] = sets[0]["Inventory"][:n]
    return out


def build_prompt(samples: dict) -> str:
    target = {t: {c: ty for c, ty in s["columns"].items() if c not in DERIVED} for t, s in TABLES.items()}
    return f"""You are helping migrate data from a legacy system to a new schema.

Legacy export (field names with sample records):
{json.dumps(samples, indent=1, ensure_ascii=False)}

Target tables and columns:
{json.dumps(target, indent=1)}

Allowed conversion types: {json.dumps(TYPES)}

Return ONLY a JSON object with this shape, one entry per target table:
{{"<table>": {{"source": "<legacy section>", "fields": {{"<LegacyField>": {{"to": "<target column>", "type": "<conversion type>", "confidence": 0.0-1.0, "note": "<why>"}}}}}}}}
Map every target column exactly once. If you are unsure, say so in the note and lower the confidence."""


def call_claude(prompt: str, model: str) -> dict:
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps({"model": model, "max_tokens": 4000,
                         "messages": [{"role": "user", "content": prompt}]}).encode(),
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        text = json.loads(resp.read())["content"][0]["text"]
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def compare(suggested: dict, confirmed: dict) -> list[str]:
    lines = []
    for table, spec in confirmed.items():
        if table.startswith("_"):
            continue
        sug = suggested.get(table, {}).get("fields", {})
        for src, f in spec["fields"].items():
            s = sug.get(src)
            if not s:
                lines.append(f"  MISSING  {table}.{src}: model did not map it (human: -> {f['to']} as {f['type']})")
            elif (s.get("to"), s.get("type")) == (f["to"], f["type"]):
                lines.append(f"  agree    {table}.{src} -> {f['to']} ({f['type']}, confidence {s.get('confidence')})")
            else:
                lines.append(f"  DIFFERS  {table}.{src}: model -> {s.get('to')} ({s.get('type')}), "
                             f"human -> {f['to']} ({f['type']}). Note: {s.get('note', '')}")
        for src in set(sug) - set(spec["fields"]):
            lines.append(f"  EXTRA    {table}.{src}: model mapped a field the human mapping does not use")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--legacy", type=Path, default=ROOT / "out" / "legacy")
    ap.add_argument("--model", default=os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"))
    ap.add_argument("--dry-run", action="store_true", help="print the prompt and stop")
    a = ap.parse_args()

    prompt = build_prompt(legacy_samples(a.legacy))
    if a.dry_run or "ANTHROPIC_API_KEY" not in os.environ:
        print(prompt)
        if not a.dry_run:
            print("\n(set ANTHROPIC_API_KEY to send this prompt to the model)")
        return
    suggested = call_claude(prompt, a.model)
    out = ROOT / "config" / "mapping.suggested.json"
    out.write_text(json.dumps(suggested, indent=1, ensure_ascii=False), encoding="utf-8")
    confirmed = json.loads((ROOT / "config" / "mapping.json").read_text(encoding="utf-8"))
    print(f"Suggestion written to {out}\nComparison with the confirmed mapping:")
    print("\n".join(compare(suggested, confirmed)))


if __name__ == "__main__":
    main()
