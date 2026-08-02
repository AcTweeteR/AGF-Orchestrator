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
