"""Regression tests for DLT-2026-141: non-blocking opportunistic caches, strict freshness, and lock-wait diagnostics."""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from eidos.cli.main import app
from eidos.core.index.graph_index import ArchitecturalIndex
from eidos.core.index.storage import (
    _advisory_db_lock,
    _db_lock_path,
    _format_lock_wait_message,
    _read_lock_holder,
    load_spec_validation_cache,
    load_test_binding_cache,
    open_cache_connection,
    save_spec_validation_cache,
    save_test_binding_cache,
)
from eidos.core.spec.boundary_validator import evaluate_import_boundaries
from eidos.core.spec.validator import SpecValidator

runner = CliRunner()


def _hold_os_lock_in_subprocess(lock_path: Path) -> subprocess.Popen[str]:
    """Spawn a child process holding an exclusive OS advisory lock on ``lock_path`` until stdin closes."""
    helper_code = (
        "import os, sys, time\n"
        "from pathlib import Path\n"
        "lp = Path(sys.argv[1])\n"
        "lp.parent.mkdir(parents=True, exist_ok=True)\n"
        "fd = os.open(str(lp), os.O_CREAT | os.O_RDWR, 0o666)\n"
        "if os.name == 'nt':\n"
        "    import msvcrt\n"
        "    if os.fstat(fd).st_size == 0:\n"
        "        os.write(fd, b'0')\n"
        "        os.lseek(fd, 0, os.SEEK_SET)\n"
        "    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)\n"
        "else:\n"
        "    import fcntl\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX)\n"
        "    os.ftruncate(fd, 0)\n"
        "    os.write(fd, f'pid={os.getpid()} started={time.time():.3f}\\n'.encode('ascii'))\n"
        "print(f'LOCKED:{os.getpid()}', flush=True)\n"
        "sys.stdin.read()\n"
        "if os.name == 'nt':\n"
        "    import msvcrt\n"
        "    os.lseek(fd, 0, os.SEEK_SET)\n"
        "    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)\n"
        "else:\n"
        "    import fcntl\n"
        "    fcntl.flock(fd, fcntl.LOCK_UN)\n"
        "os.close(fd)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", helper_code, str(lock_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout is not None
    ready = proc.stdout.readline().strip()
    assert ready.startswith("LOCKED:"), f"Child failed to acquire lock: {ready!r}"
    return proc


def _release_os_lock_subprocess(proc: subprocess.Popen[str]) -> None:
    if proc.stdin is not None:
        proc.stdin.close()
    proc.wait(timeout=5)


def _init_minimal_repo(tmp_path: Path, *, with_import_boundaries: bool = False) -> tuple[Path, Path]:
    """Create a minimal valid Eidos repo (optionally with ARCH-*.import_boundaries)."""
    repo = tmp_path
    eidos_dir = repo / "eidos"
    (eidos_dir / "architecture" / "containers").mkdir(parents=True)
    (repo / "src" / "alpha").mkdir(parents=True)
    (repo / "src" / "beta").mkdir(parents=True)
    (repo / "src" / "alpha" / "mod.py").write_text("def hello() -> str:\n    return 'ok'\n", encoding="utf-8")
    (repo / "src" / "beta" / "mod.py").write_text("def helper() -> str:\n    return 'beta'\n", encoding="utf-8")

    (eidos_dir / "system.adoc").write_text(
        "---\ntype: product/system\nid: SYS-DEMO\ntitle: Demo\nstatus: active\narchetypes:\n  - library\n---\n= Demo\n",
        encoding="utf-8",
    )
    ib_block = (
        "\nimport_boundaries:\n  tier_rules:\n    core:\n      deny:\n        - presentation\n"
        if with_import_boundaries
        else ""
    )
    (eidos_dir / "architecture" / "system-architecture.adoc").write_text(
        "---\n"
        "type: tech/architecture\n"
        "id: ARCH-DEMO\n"
        "title: Demo Architecture\n"
        "status: active\n"
        "c4_context:\n"
        "  actors: []\n"
        "  external_systems: []\n"
        f"{ib_block}"
        "---\n= Architecture\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "alpha.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-ALPHA\n"
        "title: Alpha\n"
        "status: active\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "source_path: src/alpha\n"
        "tier: core\n"
        "---\n= Alpha\n",
        encoding="utf-8",
    )
    (eidos_dir / "architecture" / "containers" / "beta.adoc").write_text(
        "---\n"
        "type: tech/container\n"
        "id: CNT-BETA\n"
        "title: Beta\n"
        "status: active\n"
        "container_type: library\n"
        "runtime: Python 3.12\n"
        "source_path: src/beta\n"
        "tier: presentation\n"
        "---\n= Beta\n",
        encoding="utf-8",
    )
    return repo, eidos_dir


def test_nonblocking_caches_strict_freshness_and_lock_wait_diagnostics(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify INV-INDEX-FRESHNESS-AND-LOCK-DIAGNOSTICS across non-blocking caches, strict freshness, and stderr lock-wait diagnostics."""
    repo, eidos_dir = _init_minimal_repo(tmp_path, with_import_boundaries=True)
    db_path = eidos_dir / ".cache" / "index.db"
    lock_path = _db_lock_path(db_path)

    # 1. Cold DB: opportunistic cache loads return {} immediately without creating/locking index.db
    assert load_spec_validation_cache(db_path) == {}
    assert load_test_binding_cache(db_path) == {}
    assert not db_path.exists()

    # Initialize and sync the DB cleanly first
    idx = ArchitecturalIndex(db_path=db_path, repo_root=repo, eidos_dir=eidos_dir)
    idx.sync(auto_semantic=False)
    idx.close()

    # Hold OS lock from a separate process
    holder = _hold_os_lock_in_subprocess(lock_path)
    try:
        holder_meta = _read_lock_holder(lock_path)
        assert f"pid={holder.pid}" in holder_meta

        # 2. Opportunistic cache writes skip immediately under contention (never stall)
        t0 = time.monotonic()
        save_spec_validation_cache(db_path, [("eidos/system.adoc", 1, "sha", "sh", "{}")], [])
        save_test_binding_cache(db_path, [("tests/test_a.py", 1, "sha", "{}", "[]")], [])
        assert (time.monotonic() - t0) < 1.0

        # 3. Clean validate (no dirty files) and index status complete promptly without blocking on lock
        t1 = time.monotonic()
        rep_clean = SpecValidator().validate_directory(eidos_dir)
        assert rep_clean.is_clean
        assert (time.monotonic() - t1) < 2.0

        status_res = runner.invoke(app, ["index", "status", "--path", str(eidos_dir), "--json"])
        assert status_res.exit_code == 0
        assert json.loads(status_res.stdout)["is_dirty"] is False

        # 4. Introduce a forbidden cross-tier import (core -> presentation) while lock is held,
        #    then release the lock after 0.2s: evaluate_import_boundaries MUST wait cleanly,
        #    emit a stderr notice naming index.db.lock + holder PID, re-sync fresh state,
        #    and detect the new forbidden import violation (zero stale WAL snapshot!).
        (repo / "src" / "alpha" / "mod.py").write_text(
            "from beta.mod import helper\n\ndef hello() -> str:\n    return helper()\n",
            encoding="utf-8",
        )

        def _delayed_release() -> None:
            time.sleep(0.2)
            _release_os_lock_subprocess(holder)

        releaser = threading.Thread(target=_delayed_release, daemon=True)
        releaser.start()
        capsys.readouterr()
        violations, _ = evaluate_import_boundaries(eidos_dir, repo_root=repo)
        releaser.join(timeout=5)
        wait_stderr = capsys.readouterr().err
        assert "Waiting for Eidos index lock" in wait_stderr
        assert "index.db.lock" in wait_stderr
        assert f"pid={holder.pid}" in wait_stderr
        assert len(violations) == 1
        assert violations[0]["source"] == "CNT-ALPHA"
        assert violations[0]["target"] == "CNT-BETA"
    finally:
        if holder.poll() is None:
            _release_os_lock_subprocess(holder)


def test_advisory_lock_helpers_allow_ddl_false_and_thread_wait(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Cover _read_lock_holder, _format_lock_wait_message, allow_ddl=False, and intra-process thread wait notification."""
    missing_lock = tmp_path / "missing.lock"
    assert _read_lock_holder(missing_lock) == ""
    assert "Waiting for Eidos index lock" in _format_lock_wait_message(missing_lock)
    assert "held by" not in _format_lock_wait_message(missing_lock)

    # Uninitialized empty SQLite file with allow_ddl=False fails fast without DDL lock
    raw_db = tmp_path / "empty.db"
    sqlite3.connect(str(raw_db)).close()
    with pytest.raises(sqlite3.OperationalError):
        open_cache_connection(raw_db, allow_ddl=False, sqlite_timeout=0.1)
    assert load_spec_validation_cache(raw_db) == {}
    assert load_test_binding_cache(raw_db) == {}

    # Intra-process thread contention: notify_wait emits stderr notice and waits cleanly until released
    db_path = tmp_path / "thread.db"
    entered = threading.Event()

    def _holder_thread() -> None:
        with _advisory_db_lock(db_path, non_blocking=False):
            entered.set()
            time.sleep(0.15)

    t = threading.Thread(target=_holder_thread, daemon=True)
    t.start()
    assert entered.wait(timeout=2.0)
    with _advisory_db_lock(db_path, non_blocking=True) as got:
        assert got is False
    capsys.readouterr()
    with _advisory_db_lock(db_path, non_blocking=False, notify_wait=True) as got_blocking:
        assert got_blocking is True
    assert "Waiting for Eidos index lock" in capsys.readouterr().err
    t.join(timeout=3.0)
