"""Table previews exercise real CSV/Parquet files in isolated API processes."""
from __future__ import annotations
import csv
from decimal import Decimal
import io
import os
from pathlib import Path
import subprocess
import sys

import pytest

from test_api import create, ok

ROOT = Path(__file__).resolve().parents[1]


def upload(client, project, name, content):
    return ok(client.post(f"/api/projects/{project['id']}/upload", files={"file": (name, content)}))["path"]


def preview(client, project, path, **params):
    return client.get(f"/api/projects/{project['id']}/file/preview", params={"path": path, **params})


def case_csv_pagination_and_json_values(client):
    project = create(client)
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(["id", "value", "note"])
    for index in range(4105):
        value = float("nan") if index == 7 else float("inf") if index == 8 else index / 10
        note = "line one\nline two" if index == 1 else "x" * 10000 if index == 9 else f"sample {index}"
        writer.writerow([index, value, note])
    path = upload(client, project, "observations.csv", content.getvalue().encode())
    first = ok(preview(client, project, path, limit=10))
    assert first["columns"] == ["id", "value", "note"]
    assert first["total"] == 4105 and first["returned"] == 10 and first["has_more"]
    assert first["rows"][1]["note"] == "line one\nline two"
    assert first["rows"][7]["value"] is None and first["rows"][8]["value"] is None
    assert len(first["rows"][9]["note"]) <= 513 and first["truncated_cells"] == 1
    middle = ok(preview(client, project, path, offset=2039, limit=100))
    assert middle["returned"] == 100 and middle["total"] == 4105
    assert middle["rows"][0]["id"] == 2039 and middle["rows"][-1]["id"] == 2138
    end = ok(preview(client, project, path, offset=4103, limit=100))
    assert [r["id"] for r in end["rows"]] == [4103, 4104] and not end["has_more"]
    empty = ok(preview(client, project, path, offset=9000))
    assert empty["total"] == 4105 and empty["rows"] == [] and empty["columns"] == first["columns"]
    for query in ({"offset": -1}, {"offset": 10_000_001}, {"limit": 0}, {"limit": 501}):
        assert preview(client, project, path, **query).status_code == 422
    tsv = upload(client, project, "small.tsv", b"id\tname\n1\talice\n")
    assert ok(preview(client, project, tsv))["rows"] == [{"id": 1, "name": "alice"}]


def case_parquet_batches_datetimes_and_missing_values(client):
    import pandas as pd
    project = create(client)
    frame = pd.DataFrame({"id": range(75), "score": [float("nan") if i == 12 else float("inf") if i == 13 else i / 10 for i in range(75)],
                          "when": pd.date_range("2024-01-01", periods=75), "labels": [["measured", str(i)] for i in range(75)],
                          "decimal": [Decimal("1.23")] * 75})
    frame.loc[13, "when"] = pd.NaT
    output = io.BytesIO()
    frame.to_parquet(output, engine="pyarrow", row_group_size=13, index=False)
    path = upload(client, project, "measurements.parquet", output.getvalue())
    page = ok(preview(client, project, path, offset=12, limit=20))
    assert page["format"] == "parquet" and page["total"] == 75 and page["returned"] == 20
    assert page["columns"] == list(frame.columns)
    assert page["rows"][0]["id"] == 12 and page["rows"][-1]["id"] == 31
    assert page["rows"][0]["score"] is None and page["rows"][1]["score"] is None
    assert page["rows"][0]["when"].startswith("2024-01-13") and page["rows"][1]["when"] is None
    assert page["rows"][0]["labels"] == ["measured", "12"] and page["rows"][0]["decimal"] == "1.23"
    end = ok(preview(client, project, path, offset=72, limit=10))
    assert end["returned"] == 3 and not end["has_more"]
    missing = ok(preview(client, project, path, offset=1000))
    assert missing["rows"] == [] and missing["total"] == 75


def case_malformed_and_oversized_tables_are_actionable(client):
    project = create(client)
    for name, data in (("bad.csv", b'a,b\n1,2\n3,4,5\n'), ("quoted.csv", b'a,b\n1,"unfinished\n'),
                       ("empty.csv", b""), ("invalid.csv", b"a,b\n\xff,2\n"), ("bad.parquet", b"PAR1brokenPAR1")):
        path = upload(client, project, name, data)
        response = preview(client, project, path)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "TABLE_PARSE_FAILED"
    headers = upload(client, project, "headers.csv", b"id,value\n")
    assert ok(preview(client, project, headers))["rows"] == []
    wide = upload(client, project, "wide.csv", (",".join(f"column{i}" for i in range(105)) + "\n" + ",".join(str(i) for i in range(105)) + "\n").encode())
    page = ok(preview(client, project, wide))
    assert page["truncated_columns"] and page["column_count"] == 105 and len(page["columns"]) == 100
    too_wide = upload(client, project, "too-wide.csv", (",".join(f"c{i}" for i in range(1001)) + "\n").encode())
    assert preview(client, project, too_wide).status_code == 413
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]
    with (root / "too-large.csv").open("wb") as file:
        file.truncate(128 * 1024 * 1024 + 1)
    response = preview(client, project, "too-large.csv")
    assert response.status_code == 413 and response.json()["detail"]["code"] == "TABLE_TOO_LARGE"


def case_path_traversal_and_symlink_escape_remain_denied(client):
    project = create(client)
    root = Path(os.environ["FOREST_DATA_DIR"]) / "projects" / project["id"]
    outside = root.parent / "outside.csv"
    outside.write_text("secret\nprivate\n")
    (root / "escape.csv").symlink_to(outside)
    for path in ("../outside.csv", str(outside), "escape.csv"):
        response = preview(client, project, path)
        assert response.status_code == 403 and response.json()["detail"]["code"] == "PATH_ESCAPE"
    assert preview(client, project, "missing.csv").status_code == 404
    ordinary = upload(client, project, "readme.md", b"hello")
    unsupported = preview(client, project, ordinary)
    assert unsupported.status_code == 415 and unsupported.json()["detail"]["code"] == "UNSUPPORTED_TABLE_FORMAT"


def case_zero_padded_identifiers_survive_preview(client):
    project = create(client)
    content = (b"postal_code,subject_id,serial,score\n"
               b"02108,000123,0000,9.5\n"
               b"10001,000124,0007,10.5\n")
    path = upload(client, project, "identifiers.csv", content)
    page = ok(preview(client, project, path))
    assert page["columns"] == ["postal_code", "subject_id", "serial", "score"]
    assert page["rows"][0] == {"postal_code": "02108", "subject_id": "000123", "serial": "0000", "score": 9.5}, page
    assert page["rows"][1]["postal_code"] == "10001", page
    tsv = upload(client, project, "identifiers.tsv", content.replace(b",", b"\t"))
    page = ok(preview(client, project, tsv))
    assert page["rows"][0]["subject_id"] == "000123", page


CASES = [name.removeprefix("case_") for name in list(globals()) if name.startswith("case_")]


@pytest.mark.parametrize("case", CASES)
def test_isolated_file_preview(tmp_path, case):
    env = {**os.environ, "FOREST_DATA_DIR": str(tmp_path / "data"), "FOREST_DATABASE_URL": "sqlite:///" + str(tmp_path / "preview.sqlite"),
           "FOREST_OWNER_TOKEN": "preview-test-owner", "FOREST_MODEL": "", "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--case", case], cwd=ROOT, env=env, capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr


if __name__ == "__main__":
    from fastapi.testclient import TestClient
    from services.api.main import app
    selected = sys.argv[2]
    assert selected in CASES
    with TestClient(app) as client:
        globals()["case_" + selected](client)
