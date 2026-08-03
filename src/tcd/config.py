"""Global configuration and path constants."""

from __future__ import annotations

from pathlib import Path

TCD_HOME = Path.home() / ".tcd"
JOBS_DIR = TCD_HOME / "jobs"
LOCKS_DIR = TCD_HOME / "locks"
LOG_FILE = TCD_HOME / "tcd.log"

DEFAULT_TIMEOUT_MINUTES = 60
TMUX_SESSION_PREFIX = "tcd"


def ensure_dirs() -> None:
    """Create required directories if they don't exist."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    LOCKS_DIR.mkdir(parents=True, exist_ok=True)


def repo_lock_path(repo_path: str | Path) -> Path:
    """Lock file guarding one repo's stash stack against concurrent jobs.

    Keyed on the git *common* directory, not the caller's path: a main checkout
    and a linked worktree of the same repository share one stash stack but
    resolve to different paths, so hashing the path handed them different locks
    — meaning the repo-level lock was not repo-level exactly where worktree
    jobs, the reason it exists, are running.
    """
    import hashlib
    import subprocess

    key = str(Path(repo_path).resolve())
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
        # Tolerate anything that does not behave like CompletedProcess: failing
        # to take a lock is far worse than taking a slightly narrower one.
        if getattr(result, "returncode", 1) == 0 and getattr(result, "stdout", "").strip():
            key = str(Path(result.stdout.strip()).resolve())
    except Exception:
        pass  # fall back to the path; a private lock beats no lock

    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return LOCKS_DIR / f"repo-{digest}.lock"


def job_json_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def job_log_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.log"


def job_prompt_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.prompt"


def job_signal_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.turn-complete"


def job_events_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.events.jsonl"
