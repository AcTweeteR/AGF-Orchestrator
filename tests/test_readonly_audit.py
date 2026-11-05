"""End-to-end tests for the distinct, zero-execution audit session outcome."""
import json
import subprocess
from pathlib import Path

import pytest

from agf_orchestrator.project_models import ProjectPolicy
from agf_orchestrator.project_registry import ProjectRegistry
from agf_orchestrator.readonly_audit import ReadOnlyAuditError
from agf_orchestrator.session_manager import SessionManager, SessionManagerError
from agf_orchestrator.session_models import SessionStatus
from agf_orchestrator.session_store import SessionStore


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


@pytest.fixture
def site(tmp_path, monkeypatch):
    state = tmp_path / "state"
    root = tmp_path / "checkout"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Audit fixture")
    git(root, "config", "user.email", "fixture@example.invalid")
    git(root, "remote", "add", "origin", "https://example.invalid/fixture.git")
    (root / "README.md").write_text("# Kodi fixture\ntrakt trailer and touch tests\n")
    (root / "source").mkdir()
    (root / "source" / "control.py").write_text(
        "import subprocess\n\n"
        "def unsafe():\n"
        "    subprocess.run('echo hello', shell=True)\n"
    )
    (root / "secrets").mkdir()
    (root / "secrets" / "token.py").write_text(
        "x = 'PRIVATE_FIXTURE_VALUE'\n"
    )
    (root / "repo").mkdir()
    (root / "repo" / "addons.xml").write_text(
        '<addons><addon id="service.actweeter" version="0.4.99"/></addons>\n'
    )
    (root / "repo" / "service.actweeter").mkdir()
    import zipfile
    zip_path = root / "repo" / "service.actweeter" / "service.actweeter-0.4.99.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "service.actweeter/addon.xml",
            '<addon id="service.actweeter" version="0.4.99" />',
        )
        archive.writestr(
            "service.actweeter/lib/safety.py",
            "import requests\nrequests.get('https://example.invalid', verify=False)\n",
        )
    import hashlib
    (root / "repo" / "addons.xml.md5").write_text(
        hashlib.md5(
            (root / "repo" / "addons.xml").read_bytes(), usedforsecurity=False,
        ).hexdigest()
    )
    git(root, "add", ".")
    git(root, "commit", "-m", "auditable fixture")
    monkeypatch.setenv("AGF_STATE_DIR", str(state))
    registry = ProjectRegistry(state)
    project = registry.add("fixture-audit", str(root))
    manager = SessionManager(registry=registry, store=SessionStore(state))
    session = manager.start(
        project.project_id,
        "AUDITORÍA PROFUNDA SOLO LECTURA de repositorio Kodi; sin modificar Stable.",
    )
    assert session.status is SessionStatus.READY
    return root, registry, manager, project, session


def test_readonly_audit_produces_true_evidence_and_closes_special_session(site):
    root, registry, manager, project, session = site
    baseline = git(root, "status", "--porcelain=v1", "-z")
    response = manager.audit_readonly(session.session_id)
    assert response["session"]["status"] == "AUDITED"
    report = response["report"]
    assert report["audit_kind"] == "READ_ONLY_STATIC"
    assert report["outcome"] == "STATIC_AUDIT_COMPLETED"
    assert report["base_sha"] == project.current_head_sha
    assert report["scope"]["scanned_text_files"] >= 3
    assert report["scope"]["excluded_sensitive_or_unsafe"] >= 1
    assert report["safety"]["repo_writes"] is False
    assert report["safety"]["network_provider"] is False
    assert "PRIVATE_FIXTURE_VALUE" not in json.dumps(report)
    assert any(
        x["rule"] == "subprocess_shell_enabled"
        and x["file"] == "source/control.py" and x["line"] == 4
        for x in report["findings"]
    )
    assert not any(
        x["rule"] == "missing_indexed_zip" for x in report["findings"]
    )
    assert git(root, "status", "--porcelain=v1", "-z") == baseline == ""
    assert git(root, "rev-parse", "HEAD") == report["base_sha"]
    assert response["replayed"] is False
    assert manager.get(session.session_id).status is SessionStatus.AUDITED
    assert manager.get(session.session_id).events[-1].actor == "REVIEWER"
    replay = manager.audit_readonly(session.session_id)
    assert replay["replayed"] is True
    assert replay["report"] == report
    with pytest.raises(SessionManagerError, match="terminal|completed"):
        manager.assess(session.session_id)


def test_blocks_live_execution_project_before_any_audit(site):
    root, registry, manager, project, session = site
    from dataclasses import replace

    unsafe = replace(project, policy=ProjectPolicy(allow_live_execution=True))
    with pytest.raises(SessionManagerError, match="execution- and delivery-disabled"):
        manager.audit_readonly(session.session_id) if registry.get(
            project.project_id
        ).policy.allow_live_execution else _unsafe_audit(unsafe, session)
    assert manager.get(session.session_id).status is SessionStatus.READY


def _unsafe_audit(project, session):
    from agf_orchestrator.readonly_audit import build_readonly_audit

    try:
        build_readonly_audit(project, session)
    except ReadOnlyAuditError as exc:
        raise SessionManagerError(str(exc)) from exc


def test_dirty_or_changed_source_refused_without_terminal_audit(site):
    root, registry, manager, project, session = site
    (root / "README.md").write_text("changed\n")
    with pytest.raises(SessionManagerError, match="clean"):
        manager.audit_readonly(session.session_id)
    assert manager.get(session.session_id).status is SessionStatus.READY
    git(root, "restore", "README.md")
    (root / "new.txt").write_text("untracked\n")
    with pytest.raises(SessionManagerError, match="clean"):
        manager.audit_readonly(session.session_id)


def test_blocked_architect_audit_recoverable_only_as_static_review(site):
    root, registry, manager, project, session = site
    manager.transition(session.session_id, SessionStatus.BLOCKED, summary="No signed architect")
    assert manager.get(session.session_id).status is SessionStatus.BLOCKED
    response = manager.audit_readonly(session.session_id)
    assert response["session"]["status"] == "AUDITED"
    assert response["report"]["safety"]["signature_or_approval"] is False


def test_cannot_manually_transition_to_audited_without_report(site):
    root, registry, manager, project, session = site
    with pytest.raises(SessionManagerError, match="read-only evidence"):
        manager.transition(session.session_id, SessionStatus.AUDITED)
    assert manager.get(session.session_id).status is SessionStatus.READY


def test_tampered_artifact_fails_closed_on_replay(site):
    root, registry, manager, project, session = site
    result = manager.audit_readonly(session.session_id)
    path = Path(result["report_path"])
    path.write_text("{}")
    with pytest.raises(SessionManagerError, match="tampered"):
        manager.audit_readonly(session.session_id)


def test_non_audit_goal_refused(site):
    root, registry, manager, project, session = site
    session.goal = "Implement feature and deploy it"
    manager.store.save(session)
    with pytest.raises(SessionManagerError, match="explicitly stated audit"):
        manager.audit_readonly(session.session_id)


def test_invalid_xml_is_evidence_not_runtime_claim(site):
    root, registry, manager, project, session = site
    # This is a static error and is admissible only if tracked at source SHA.
    (root / "source" / "broken.xml").write_text("<broken>\n")
    git(root, "add", "source/broken.xml")
    git(root, "commit", "-m", "add invalid XML")
    project = registry.verify(project.project_id)
    from dataclasses import replace
    session = replace(session, base_sha=project.current_head_sha)
    manager.store.save(session)
    outcome = manager.audit_readonly(session.session_id)
    assert any(
        item["rule"] == "xml_parse_error"
        and item["confidence"] == "CONFIRMED_STATIC"
        for item in outcome["report"]["findings"]
    )



def test_scans_current_published_stable_zip_in_memory_not_just_historical_files(site):
    root, registry, manager, project, session = site
    result = manager.audit_readonly(session.session_id)
    report = result["report"]
    assert report["published_package_coverage"]["stable_packages"] == 1
    assert report["published_package_coverage"]["stable_python_entries"] == 1
    assert any(
        f["rule"] == "stable_tls_verification_disabled"
        and f["file"] == "repo/service.actweeter/service.actweeter-0.4.99.zip"
        and f["package_member"] == "service.actweeter/lib/safety.py"
        and f["line"] == 2
        for f in report["findings"]
    )
    assert git(root, "status", "--porcelain=v1", "-z") == ""


def test_stable_zip_identity_mismatch_is_evidenced_without_extracting(site):
    import zipfile
    from dataclasses import replace

    root, registry, manager, project, session = site
    zip_path = root / "repo/service.actweeter/service.actweeter-0.4.99.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "service.actweeter/addon.xml",
            '<addon id="service.actweeter" version="0.0.1" />',
        )
    git(root, "add", str(zip_path.relative_to(root)))
    git(root, "commit", "-m", "tampered packaged metadata")
    verified = registry.verify(project.project_id)
    manager.store.save(replace(session, base_sha=verified.current_head_sha))
    result = manager.audit_readonly(session.session_id)
    assert any(
        f["rule"] == "zip_index_metadata_mismatch"
        and f["confidence"] == "CONFIRMED_STATIC"
        for f in result["report"]["findings"]
    )
    assert result["session"]["status"] == "AUDITED"


def test_zip_traversal_is_reported_as_confirmed_static_indicator(site):
    import zipfile
    from dataclasses import replace

    root, registry, manager, project, session = site
    zip_path = root / "repo/service.actweeter/service.actweeter-0.4.99.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        archive.writestr(
            "service.actweeter/addon.xml",
            '<addon id="service.actweeter" version="0.4.99" />',
        )
        archive.writestr("../untrusted.py", "print('not run')\n")
    git(root, "add", str(zip_path.relative_to(root)))
    git(root, "commit", "-m", "unsafe package entry")
    verified = registry.verify(project.project_id)
    manager.store.save(replace(session, base_sha=verified.current_head_sha))
    result = manager.audit_readonly(session.session_id)
    assert any(f["rule"] == "unsafe_zip_path" for f in result["report"]["findings"])
    assert not (root.parent / "untrusted.py").exists()



def test_real_cli_writes_only_off_repo_audit_artifact(site, capsys):
    from agf_orchestrator.cli import main

    root, registry, manager, project, session = site
    result = main([
        "session", "audit-readonly", "--session", session.session_id, "--json",
    ])
    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["session"]["status"] == "AUDITED"
    assert output["report_path"].startswith(str(manager.store.state_dir))
    assert output["report"]["safety"]["network_provider"] is False
    assert main([
        "session", "audit-readonly", "--session", session.session_id, "--json",
    ]) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["replayed"] is True
    assert git(root, "status", "--porcelain=v1", "-z") == ""
