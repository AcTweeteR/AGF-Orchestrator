from tools import audit_public_history as audit


def test_history_findings_do_not_print_matched_bytes_or_sensitive_paths(monkeypatch, capsys):
    sensitive_path = "confidential-customer-name.key"
    detected = b"-----BEGIN " + b"PRIVATE KEY-----"
    oid = "a" * 40
    monkeypatch.setattr(audit, "all_objects", lambda: [(oid, sensitive_path)])
    monkeypatch.setattr(audit, "object_type", lambda _oid: "blob")
    monkeypatch.setattr(audit, "object_size", lambda _oid: len(detected))

    def git(*args, **_kwargs):
        if args == ("git", "rev-parse", "--is-shallow-repository"):
            return b"false"
        if args == ("git", "rev-list", "--all", "--count"):
            return b"1"
        assert args == ("git", "cat-file", "blob", oid)
        return detected

    monkeypatch.setattr(audit, "run", git)
    assert audit.main() == 1
    output = capsys.readouterr().out
    assert "PUBLIC_HISTORY_AUDIT=FAIL" in output
    assert "private-key " + oid in output
    assert "sensitive-filename " + oid in output
    assert sensitive_path not in output
    assert detected.decode() not in output


def test_oversized_blob_diagnostic_omits_filename(monkeypatch):
    monkeypatch.setattr(audit, "object_size", lambda _oid: 11 * 1024 * 1024)
    assert audit.scan_blob("b" * 40, "confidential-path") == [
        "oversized-blob " + "b" * 40 + " size=11534336",
    ]
