"""Real local IPC tests; fixture signatures are not production owner authority."""

import multiprocessing
import socket

import pytest
from provider_test_support import owner_endpoint, verify_envelope

from agf_orchestrator.documentation import DocumentationError, _request_owner_attestation


@pytest.mark.parametrize("response", [
    b"", b"\x00\x00", (65537).to_bytes(4, "big"), (0).to_bytes(4, "big"),
    (10).to_bytes(4, "big") + b"{}", (1).to_bytes(4, "big") + b"{",
    (1).to_bytes(4, "big") + b"\xff", (2).to_bytes(4, "big") + b"[]",
])
def test_owner_transport_rejects_truncated_oversized_and_malformed_responses(response):
    with owner_endpoint(response_bytes=response) as endpoint:
        with pytest.raises(DocumentationError):
            _request_owner_attestation({"request": "fixture"}, endpoint)


def test_owner_transport_bounds_request_before_connecting():
    with pytest.raises(DocumentationError, match="size limit"):
        _request_owner_attestation({"payload": "x" * 65536}, "/missing/owner.sock")


def test_owner_transport_unsupported_platform_is_typed(monkeypatch):
    monkeypatch.delattr(socket, "AF_UNIX")
    with pytest.raises(DocumentationError, match="unavailable"):
        _request_owner_attestation({}, "/missing/owner.sock")


def test_owner_transport_has_one_total_response_deadline(monkeypatch):
    import agf_orchestrator.documentation as documentation

    with owner_endpoint() as endpoint:
        ticks = iter([0.0, 1.0, 11.0])
        monkeypatch.setattr(documentation.time, "monotonic", lambda: next(ticks))
        with pytest.raises(TimeoutError):
            _request_owner_attestation({}, endpoint)
        monkeypatch.undo()


def test_daemonic_runtime_can_use_preexisting_external_owner_endpoint():
    subject = {"request": "daemon-fixture"}
    with owner_endpoint() as endpoint:
        parent, child = multiprocessing.Pipe(duplex=False)

        def resolve():
            envelope = _request_owner_attestation(subject, endpoint)
            verify_envelope(subject, envelope)
            child.send(True)
            child.close()

        process = multiprocessing.get_context("fork").Process(target=resolve, daemon=True)
        process.start()
        child.close()
        try:
            assert parent.poll(5)
            assert parent.recv() is True
            process.join(timeout=5)
            assert process.exitcode == 0
        finally:
            parent.close()
            if process.is_alive():
                process.terminate()
                process.join(timeout=2)
