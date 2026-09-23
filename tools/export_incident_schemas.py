"""Export additive schemas without rewriting historical contracts or their digests."""
from hashlib import sha256
import json
from pathlib import Path

from fireviewer_contracts.backend.incident_state_schemas import (
    IncidentStateRevision, IncidentUpdateRequest, IncidentUpdateResult, TemporalEvidence,
)


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    destination = root / "src/fireviewer_contracts/schemas"
    lock_file = root / "schemas.lock.json"
    lock = json.loads(lock_file.read_text(encoding="utf-8"))
    for model in (TemporalEvidence, IncidentStateRevision, IncidentUpdateRequest, IncidentUpdateResult):
        schema = model.model_json_schema(by_alias=True)
        name = schema["properties"]["schema"]["const"]
        schema["$id"] = f"urn:{name}"
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        relative = f"incident-revisions/v1/{name.split('.')[1]}.schema.json"
        path = destination / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (json.dumps(schema, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        path.write_bytes(content)
        entry = {"source": "backend/incident_state_schemas.py", "path": relative,
                 "sha256": sha256(content).hexdigest()}
        lock["resources"] = [item for item in lock["resources"] if item["path"] != relative]
        lock["resources"].append(entry)
    lock_file.write_text(json.dumps(lock, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
