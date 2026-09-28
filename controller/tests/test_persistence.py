from uuid import uuid4

import pytest

from sdl_cli.main import demo_configuration
from sdl_core.errors import SdlError
from sdl_core.jsonutil import digest
from sdl_controller.repository import ConfigurationRepository, ExecutionRepository


def commit(repository, document=None, operation=None):
    document = document or demo_configuration()
    return repository.commit(document, repository.revision, operation or str(uuid4()), digest(document), set(), [])


def test_revision_and_idempotency(paths):
    repo = ConfigurationRepository(paths.config)
    doc = demo_configuration()
    operation = str(uuid4())
    result = commit(repo, doc, operation)
    assert result["revision"] == 1
    assert repo.lookup(operation, digest(doc)) == result
    with pytest.raises(SdlError, match="different payload"):
        repo.lookup(operation, "other")
    fresh = ConfigurationRepository(paths.config)
    assert fresh.document == doc
    assert fresh.lookup(operation, digest(doc)) == result
    with pytest.raises(SdlError, match="changed"):
        repo.commit(doc, 0, str(uuid4()), digest(doc), set(), [])


def test_failure_before_pointer_preserves_previous(paths, monkeypatch):
    repo = ConfigurationRepository(paths.config)
    old = demo_configuration()
    commit(repo, old)
    import sdl_controller.repository as module
    real = module.atomic_write
    def failing(path, data):
        if path.name == "current.json":
            raise OSError("simulated disk failure")
        real(path, data)
    monkeypatch.setattr(module, "atomic_write", failing)
    with pytest.raises(OSError):
        commit(repo)
    assert repo.document == old
    assert ConfigurationRepository(paths.config).document == old


def test_corrupt_pointer_safe_recovery(paths):
    repo = ConfigurationRepository(paths.config)
    commit(repo)
    repo.pointer.write_bytes(b"damaged")
    recovered = ConfigurationRepository(paths.config)
    assert recovered.recovery
    assert recovered.revision == 0
    assert recovered.document["pages"][0]["buttons"] == []
    assert repo.pointer.read_bytes() == b"damaged"
    assert recovered.history()["revisions"][0]["valid"]


def test_uncertain_execution_is_not_retried(paths):
    path = paths.state / "executions.json"
    repo = ExecutionRepository(path)
    repo.add({"runId": "one", "state": "running", "operationId": "op", "payloadHash": "hash", "stdout": "private output"})
    assert "private output" not in path.read_text()
    recovered = ExecutionRepository(path)
    assert recovered.find_operation("op", "hash")["state"] == "unknownAfterRestart"
