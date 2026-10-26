"""Command line: python -m legomig {download,export,migrate,verify,query,all}"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="legomig", description=__doc__)
    ap.add_argument("command", choices=["download", "export", "migrate", "verify", "query", "all"])
    ap.add_argument("--raw", type=Path, default=ROOT / "data" / "raw", help="Rebrickable CSV folder")
    ap.add_argument("--out", type=Path, default=ROOT / "out", help="legacy export, warehouse, rejects, state")
    ap.add_argument("--reports", type=Path, default=ROOT / "reports", help="where the Markdown reports go")
    ap.add_argument("--mapping", type=Path, default=ROOT / "config" / "mapping.json")
    ap.add_argument("--defects", type=int, default=3, help="injected defects per type (0 = none)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--crash-after", help="simulate a crash after this table (to demo resuming)")
    a = ap.parse_args(argv)
    legacy = a.out / "legacy"
    steps = ["download", "export", "migrate", "verify", "query"] if a.command == "all" else [a.command]

    for step in steps:
        print(f"\n▶ {step}")
        if step == "download":
            from .download import download
            download(a.raw)
        elif step == "export":
            from .legacy_export import export
            res = export(a.raw, legacy, a.defects, a.seed)
            print(f"  legacy export written to {legacy} ({res['defects']} defects injected)")
        elif step == "migrate":
            from .migrate import run
            run(legacy, a.out, a.mapping, a.crash_after)
        elif step == "verify":
            from .report import to_markdown, verify
            r = verify(legacy, a.out)
            a.reports.mkdir(parents=True, exist_ok=True)
            (a.reports / "migration_report.md").write_text(to_markdown(r), encoding="utf-8")
            (a.reports / "migration_report.json").write_text(json.dumps(r, indent=1, default=str), encoding="utf-8")
            if a.reports == ROOT / "reports":
                _update_readme(to_markdown(r))
            for c in r["checks"]:
                print(f"  {'✓' if c['ok'] else '✗'} {c['check']}: {c['detail']}")
            print(f"  report: {a.reports / 'migration_report.md'}")
            if not r["ok"]:
                print("  VERIFICATION FAILED")
                return 1
        elif step == "query":
            from .query import run_queries
            md = run_queries(a.out, ROOT / "queries")
            a.reports.mkdir(parents=True, exist_ok=True)
            (a.reports / "query_results.md").write_text(md, encoding="utf-8")
    return 0


def _update_readme(report_md: str) -> None:
    """Put the latest report between the markers in README.md, so the numbers there are never typed by hand."""
    readme = ROOT / "README.md"
    start, end = "<!-- REPORT:START -->", "<!-- REPORT:END -->"
    text = readme.read_text(encoding="utf-8") if readme.exists() else ""
    if start not in text or end not in text:
        return
    body = report_md.split("\n", 1)[1].replace("\n## ", "\n#### ")  # drop the title, demote headings
    head, rest = text.split(start, 1)
    readme.write_text(f"{head}{start}\n{body.strip()}\n{end}{rest.split(end, 1)[1]}", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
