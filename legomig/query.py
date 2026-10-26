"""Run every SQL file in queries/ with DuckDB directly on the Parquet output."""
from pathlib import Path

from .report import connect


NO_SEPARATOR = {"year", "decade"}  # 1990, not 1,990


def _md_table(cols, rows) -> str:
    def fmt(col, v):
        if v is None:
            return ""
        if isinstance(v, int) and not isinstance(v, bool) and col not in NO_SEPARATOR:
            return f"{v:,}"
        return str(v)
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(fmt(c, v) for c, v in zip(cols, r)) + " |" for r in rows]
    return "\n".join(out)


def run_queries(out_dir: Path, queries_dir: Path) -> str:
    con = connect(out_dir)
    parts = ["# Query results", "", "Produced by `python -m legomig query`: DuckDB reading the migrated Parquet files directly.", ""]
    for f in sorted(queries_dir.glob("*.sql")):
        sql = f.read_text(encoding="utf-8")
        cur = con.execute(sql)
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        print(f"\n== {f.name} ==")
        print(con.sql(sql))  # DuckDB's own pretty table in the terminal
        parts += [f"## {f.stem}", "", "```sql", sql.strip(), "```", "", _md_table(cols, rows), ""]
    return "\n".join(parts)
