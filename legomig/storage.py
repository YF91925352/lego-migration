"""Everything that touches Parquet lives here."""
import shutil
import uuid
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq

from .schema import TABLES

ARROW = {"int16": pa.int16(), "int32": pa.int32(), "string": pa.string(), "bool": pa.bool_()}


def arrow_schema(table: str) -> pa.Schema:
    return pa.schema([pa.field(c, ARROW[t]) for c, t in TABLES[table]["columns"].items()])


def write_table(df: pd.DataFrame, table: str, warehouse: Path) -> Path:
    """Write atomically: build in a temp dir, then swap it into place.
    A crash mid-write never leaves a half-written table behind."""
    spec = TABLES[table]
    cols = list(spec["columns"])
    data = pa.Table.from_pandas(df[cols].sort_values(spec["pk"]), schema=arrow_schema(table), preserve_index=False)

    tmp = warehouse / ".tmp" / f"{table}-{uuid.uuid4().hex}"
    tmp.mkdir(parents=True)
    if spec["partition"]:
        ds.write_dataset(
            data, tmp, format="parquet",
            partitioning=ds.partitioning(pa.schema([data.schema.field(spec["partition"])]), flavor="hive"),
            basename_template="part-{i}.parquet",
            existing_data_behavior="overwrite_or_ignore",
        )
    else:
        pq.write_table(data, tmp / "part-0.parquet")

    final = warehouse / table
    old = None
    if final.exists():
        old = warehouse / ".tmp" / f"{table}-old-{uuid.uuid4().hex}"
        final.rename(old)
    tmp.rename(final)
    if old:
        shutil.rmtree(old)
    return final


def read_table(table: str, warehouse: Path) -> pd.DataFrame:
    spec = TABLES[table]
    path = warehouse / table
    kwargs = {"partitioning": "hive"} if spec["partition"] else {}
    t = ds.dataset(path, format="parquet", **kwargs).to_table()
    t = t.select(list(spec["columns"])).cast(arrow_schema(table))
    return t.to_pandas(types_mapper=pd.ArrowDtype).sort_values(spec["pk"]).reset_index(drop=True)
