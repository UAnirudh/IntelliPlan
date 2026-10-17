import base64
import hashlib
import json
import os

import pytest

from scripts.mongodb_connector_batches import prepare
from scripts.migrate_to_mongodb import canonical, safe_file


def test_oversized_incompressible_document_is_rejected(tmp_path):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    value = {"_id": "a", "data": base64.b64encode(os.urandom(10000)).decode()}
    content = canonical(value) + "\n"
    (snapshot / safe_file("records")).write_text(content, encoding="utf-8", newline="\n")
    (snapshot / "manifest.json").write_text(json.dumps({"format": 1, "status": "complete",
        "created_at": "test", "tables": [{"name": "records", "rows": 1, "file": safe_file("records"),
        "sha256": hashlib.sha256(content.encode()).hexdigest()}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="payload limit"):
        prepare(snapshot, tmp_path / "batches", byte_limit=1000)
