import agf_orchestrator.locking as locking
from agf_orchestrator.locking import FileLock, LockError, lock_status


def test_lock_blocks_second_owner_and_releases(tmp_path):
    path = tmp_path / "locks" / "x.lock"
    first = FileLock(path, "test")
    first.acquire()
    assert lock_status(path)["locked"] is True
    try:
        try:
            FileLock(path, "second").acquire()
        except LockError:
            pass
        else:
            raise AssertionError("second lock acquired")
    finally:
        first.release()
    assert lock_status(path)["locked"] is False


def test_unsupported_platform_lock_status_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(locking, "fcntl", None)
    result = locking.lock_status(tmp_path / "missing.lock")
    assert result["supported"] is False
    assert "unsupported" in result["diagnostic"]
