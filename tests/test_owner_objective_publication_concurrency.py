"""External owner publication races, using inherited ephemeral test authority only."""

import json
import multiprocessing

import pytest
from test_owner_objective_publication import setup

import agf_orchestrator.authority_generation as generations
from agf_orchestrator.authority_generation import AuthorityGenerationStore
from agf_orchestrator.project_models import ProjectStatus
from agf_orchestrator.project_registry import ProjectRegistry
from tools.owner_objective_publication import publish


def spawn(context, function):
    receiver, sender = context.Pipe(duplex=False)

    def child():
        try:
            sender.send(("OK", function()))
        except BaseException as exc:
            sender.send((type(exc).__name__, str(exc)))
        finally:
            sender.close()

    process = context.Process(target=child)
    process.start()
    sender.close()
    return process, receiver


def result(process, receiver):
    try:
        process.join(20)
        assert not process.is_alive(), "owner fixture worker exceeded bounded wait"
        assert process.exitcode == 0
        assert receiver.poll(1)
        return receiver.recv()
    finally:
        if process.is_alive():
            process.kill()
            process.join(5)
        receiver.close()


def test_concurrent_prepare_cannot_overwrite_another_operation(tmp_path, monkeypatch):
    _, project, _, store, proposal, previous = setup(tmp_path, monkeypatch)
    context = multiprocessing.get_context("fork")
    start = context.Event()

    def prepare(operation):
        assert start.wait(10)
        return publish(project.project_id, operation, proposal)

    first = spawn(context, lambda: prepare("operation-first"))
    second = spawn(context, lambda: prepare("operation-second"))
    start.set()
    outcomes = [result(*first), result(*second)]
    winners = [payload for status, payload in outcomes if status == "OK"]
    assert len(winners) == 1, outcomes
    assert winners[0]["status"] == "PREPARED_NOT_ACTIVE"
    loser = "operation-second" if winners[0]["operation_id"] == "operation-first" else (
        "operation-first"
    )
    assert all(status in {"OK", "LockError", "RuntimeError"} for status, _ in outcomes)
    path = store._generation_path(project.project_id, "generation-3")
    before = path.read_bytes()
    assert store.load(project.project_id, "generation-3").operation_id == winners[0]["operation_id"]
    with pytest.raises(RuntimeError, match="differs"):
        publish(project.project_id, loser, proposal)
    assert path.read_bytes() == before
    assert store.active(project.project_id) == previous


def test_registry_writer_is_excluded_through_final_activation_commit(tmp_path, monkeypatch):
    manager, project, _, store, proposal, _ = setup(tmp_path, monkeypatch)
    prepared = publish(project.project_id, "operation-objective", proposal)
    context = multiprocessing.get_context("fork")
    original = AuthorityGenerationStore._activate_owner_controlled_locked
    observations = []

    def activate(self, *args, **kwargs):
        def writer():
            ProjectRegistry(manager.store.state_dir).set_status(project.project_id,
                                                               ProjectStatus.DISABLED)
            return "WRITTEN"

        outcome = result(*spawn(context, writer))
        observations.append(outcome[0])
        assert outcome[0] == "LockError", outcome
        return original(self, *args, **kwargs)

    monkeypatch.setattr(AuthorityGenerationStore, "_activate_owner_controlled_locked", activate)
    activated = publish(project.project_id, "operation-objective", proposal, action="activate",
                        expected_manifest=prepared["manifest_hash"])
    assert activated["status"] == "ACTIVATED"
    assert observations == ["LockError"]
    assert manager.registry.get(project.project_id).status is ProjectStatus.ACTIVE
    assert store.active(project.project_id).generation_id == "generation-3"


@pytest.mark.parametrize("interrupted_file", ["generation-3.json", "active.json",
                                              "generation-floor.json"])
def test_crash_during_metadata_commit_recovers_exact_generation_once(
    tmp_path, monkeypatch, interrupted_file,
):
    _, project, _, store, proposal, _ = setup(tmp_path, monkeypatch)
    prepared = publish(project.project_id, "operation-objective", proposal)
    original = generations._atomic_write

    def interrupted(path, payload):
        if path.name == interrupted_file:
            raise OSError("process interrupted during metadata commit")
        return original(path, payload)

    monkeypatch.setattr(generations, "_atomic_write", interrupted)
    with pytest.raises(OSError, match="during metadata commit"):
        publish(project.project_id, "operation-objective", proposal, action="activate",
                expected_manifest=prepared["manifest_hash"])
    transition = store._metadata_transition_path(project.project_id)
    journal = json.loads(transition.read_text())
    expected = journal["generation"]["payload"]
    assert expected["generation_id"] == "generation-3"
    assert expected["status"] == "ACTIVE"
    monkeypatch.setattr(generations, "_atomic_write", original)
    restarted = AuthorityGenerationStore(store.root)
    recovered = restarted.active(project.project_id)
    assert recovered.to_dict() == expected
    assert not transition.exists()
    assert restarted._floor(project.project_id) == 3
    assert publish(project.project_id, "operation-objective", proposal, action="activate",
                   expected_manifest=prepared["manifest_hash"])["status"] == "ALREADY_ACTIVE"
    assert restarted.active(project.project_id) == recovered
