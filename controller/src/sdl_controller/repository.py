"""Sole writer of applied configuration. Revision files precede the durable pointer."""
import hashlib
from pathlib import Path
from typing import Any

from sdl_core.errors import SdlError
from sdl_core.jsonutil import atomic_write, digest, dumps, read_json
from sdl_core.model import empty_configuration, require_valid


class ConfigurationRepository:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.revisions = directory / "revisions"
        self.pointer = directory / "current.json"
        self.document = empty_configuration()
        self.revision = 0
        self.operations: list[dict] = []
        self.approved: set[str] = set()
        self.recovery: str | None = None
        self.highwater = max([0, *[int(p.stem) for p in self.revisions.glob("*.json") if p.stem.isdecimal()]])
        if self.pointer.exists():
            try:
                pointer = read_json(self.pointer, 2 * 1024 * 1024)
                revision = pointer["revision"]
                record = self.get(revision)
                if record["sha256"] != pointer["sha256"]:
                    raise ValueError("pointer digest mismatch")
                require_valid(record["document"])
                self.document = record["document"]
                self.revision = revision
                self.operations = pointer["operations"][-100:]
                self.approved = set(pointer["approvedActions"])
            except (SdlError, OSError, ValueError, KeyError, TypeError):
                self.document = empty_configuration()
                self.revision = 0
                self.operations = []
                self.approved = set()
                self.recovery = "Current revision is damaged. Files were preserved; inspect configuration.history and explicitly restore a valid revision. Running an empty safe configuration."

    def get(self, revision: int | None = None) -> dict:
        if revision is None or revision == 0:
            return {"document": self.document, "revision": self.revision, "sha256": digest(self.document)}
        if type(revision) is not int or revision < 1:
            raise SdlError("INVALID_PARAMS", "Revision must be a positive integer.")
        try:
            record = read_json(self.revisions / f"{revision:010d}.json", 9 * 1024 * 1024)
            if record["revision"] != revision or digest(record["document"]) != record["sha256"]:
                raise ValueError("revision digest mismatch")
            return record
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SdlError("REVISION_UNAVAILABLE", "Revision is missing or corrupt.") from exc

    def lookup(self, operation_id: str, payload_hash: str) -> dict | None:
        for item in self.operations:
            if item["operationId"] == operation_id:
                if item["payloadHash"] != payload_hash:
                    raise SdlError("OPERATION_CONFLICT", "Operation ID was reused with a different payload.")
                return item["result"]
        return None

    def commit(self, document: dict, expected: int, operation_id: str, payload_hash: str,
               approved: set[str], warnings: list[dict]) -> dict:
        existing = self.lookup(operation_id, payload_hash)
        if existing is not None:
            return existing
        if expected != self.revision:
            raise SdlError("REVISION_CONFLICT", "The applied revision changed. Refresh before applying.", details={"currentRevision": self.revision})
        new_revision = max(self.highwater, self.revision) + 1
        record = {"revision": new_revision, "sha256": digest(document), "document": document}
        result = {"revision": new_revision, "configurationApplied": True, "deviceSynchronized": False,
                  "warnings": warnings, "operationId": operation_id}
        operations = [*self.operations, {"operationId": operation_id, "payloadHash": payload_hash, "result": result}][-100:]
        pointer = {"revision": new_revision, "sha256": record["sha256"], "operations": operations,
                   "approvedActions": sorted(approved)}
        # If the second write fails, the first is only an unreferenced revision, not an applied one.
        atomic_write(self.revisions / f"{new_revision:010d}.json", dumps(record))
        atomic_write(self.pointer, dumps(pointer))
        self.document, self.revision = document, new_revision
        self.highwater = new_revision
        self.operations, self.approved, self.recovery = operations, approved, None
        # 100 retained revision documents cover the 100 retained idempotency outcomes.
        for old in sorted(self.revisions.glob("*.json"))[:-100]:
            try:
                old.unlink()
            except OSError:
                pass
        return result

    def history(self, cursor: int = 0, limit: int = 20) -> dict:
        names = sorted(self.revisions.glob("*.json"), reverse=True)
        result = []
        for path in names[cursor:cursor + limit]:
            if not path.stem.isdecimal():
                continue
            revision = int(path.stem)
            try:
                record = self.get(revision)
                require_valid(record["document"])
                result.append({"revision": revision, "name": record["document"]["name"], "valid": True})
            except (SdlError, OSError):
                result.append({"revision": revision, "valid": False})
        return {"revisions": result, "nextCursor": cursor + limit if cursor + limit < len(names) else None}


class ExecutionRepository:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.records: list[dict] = []
        self.recovery: str | None = None
        if path.exists():
            try:
                data = read_json(path, 40 * 1024 * 1024)
                if not isinstance(data, list) or len(data) > 200 or any(not isinstance(r, dict) or "runId" not in r or "state" not in r for r in data):
                    raise ValueError()
                self.records = data
                for item in self.records:
                    if item["state"] in ("starting", "running"):
                        item["state"] = "unknownAfterRestart"
                self.save()
            except (SdlError, OSError, ValueError, TypeError):
                # Never overwrite damaged history, because it can contain uncertain execution intents.
                self.recovery = "Execution history damaged. Execution is disabled until the file is reviewed and moved aside while the Controller is stopped."

    def save(self) -> None:
        if self.recovery:
            raise SdlError("EXECUTION_RECOVERY_REQUIRED", self.recovery)
        atomic_write(self.path, dumps([{k: v for k, v in r.items() if k not in ("stdout", "stderr")} for r in self.records]))

    def add(self, record: dict) -> None:
        if len(self.records) >= 200:
            removable = next((i for i, r in enumerate(self.records) if r["state"] not in ("starting", "running")), None)
            if removable is None:
                raise SdlError("LIMIT_EXCEEDED", "Execution history is full of active jobs.")
            self.records.pop(removable)
        self.records.append(record)
        self.save()

    def find_operation(self, operation_id: str, payload_hash: str) -> dict | None:
        for record in self.records:
            if record.get("operationId") == operation_id:
                if record.get("payloadHash") != payload_hash:
                    raise SdlError("OPERATION_CONFLICT", "Execution operation ID has a different payload.")
                return record
        return None

    def summaries(self, cursor: int = 0, limit: int = 20, include_output: bool = False) -> dict:
        records = list(reversed(self.records))
        chosen = records[cursor:cursor + limit]
        if not include_output:
            chosen = [{k: v for k, v in r.items() if k not in ("stdout", "stderr")} for r in chosen]
        return {"executions": chosen, "nextCursor": cursor + limit if cursor + limit < len(records) else None}
