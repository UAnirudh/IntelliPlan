"""Prepare bounded Extended JSON batches for the authenticated Atlas connector.

No credentials are needed. The snapshot and output must remain in a private
directory outside the repository. This command prepares data; it does not
send data or execute text contained in documents.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from scripts.migrate_to_mongodb import canonical, validate_snapshot, pack_document, unpack_document
except ModuleNotFoundError:
    from migrate_to_mongodb import canonical, validate_snapshot, pack_document, unpack_document

from bson import json_util


def prepare(snapshot: Path, destination: Path, byte_limit=48000, row_limit=100):
    manifest = validate_snapshot(snapshot)
    destination.mkdir(mode=0o700, exist_ok=False)
    plan = {"tables": [{"name": t["name"], "rows": t["rows"]} for t in manifest["tables"]],
            "batches": [], "snapshot_at": manifest["created_at"]}

    def save(name, lines):
        identifier = len(plan["batches"])
        payload = '{"collection":' + json.dumps(name) + ',"documents":[' + ",".join(lines) + "]}"
        if len(payload.encode("ascii")) > byte_limit:
            raise ValueError("A document exceeds the connector payload limit; use the native driver transfer")
        (destination / f"{identifier:05d}.json").write_text(payload, encoding="ascii")
        plan["batches"].append({"id": identifier, "collection": name, "rows": len(lines), "bytes": len(payload)})

    for table in manifest["tables"]:
        batch = []
        size = 0
        with (snapshot / table["file"]).open(encoding="utf-8") as source:
            for line in source:
                original = json_util.loads(line)
                packed = pack_document(original)
                if canonical(unpack_document(packed)) != canonical(original):
                    raise ValueError("Storage codec did not preserve the source document")
                line = canonical(packed)
                overhead = len(json.dumps(table["name"])) + 40
                if batch and (size + len(line) + overhead > byte_limit or len(batch) >= row_limit):
                    save(table["name"], batch)
                    batch = []
                    size = 0
                batch.append(line)
                size += len(line) + 1
        if batch:
            save(table["name"], batch)
    (destination / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    return plan


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.snapshot, args.output)
    print(json.dumps({"tables": len(result["tables"]), "rows": sum(t["rows"] for t in result["tables"]),
                      "batches": len(result["batches"])}))
