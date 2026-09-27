#!/usr/bin/env python3
"""One-way mirror sync of guidance assets from ``.claude/`` to ``.qoder/``.

Decision record (single source of truth for dual-platform assets):
  - Canonical source : ``.claude/agents``, ``.claude/skills`` (all edits happen here)
  - Managed mirror   : ``.qoder/agents``, ``.qoder/skills`` (generated, never hand-edited)
  - ``.claude/rules`` and ``.claude/workflows`` are intentionally NOT mirrored; Qoder
    reaches them through the path references in ``CLAUDE.md`` / ``AGENTS.md``.
    Mirroring them too would create a second copy free to drift.

The mirror is produced by this script only, and ``.githooks/pre-commit`` runs it
with ``--auto-stage`` before every commit.

Usage:
    uv run python scripts/sync_agent_assets.py              # sync + final verify
    uv run python scripts/sync_agent_assets.py --verify     # read-only, exit 1 on drift
    uv run python scripts/sync_agent_assets.py --auto-stage # sync then git add the mirror
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# (category, canonical dir, mirror dir, mirror granularity)
MIRROR_PAIRS: tuple[tuple[str, Path, Path, str], ...] = (
    ("agents", ROOT / ".claude" / "agents", ROOT / ".qoder" / "agents", "file"),
    ("skills", ROOT / ".claude" / "skills", ROOT / ".qoder" / "skills", "dir"),
)

# Staged mirror paths, relative to ROOT, used by --auto-stage.
STAGED_PATHS: tuple[str, ...] = (".qoder/agents", ".qoder/skills")


@dataclass(frozen=True)
class Change:
    """A single divergence between the canonical source and its mirror."""

    kind: str  # mirror-missing | missing | drift | orphan | extra
    label: str
    src: Path | None
    dst: Path


def _normalized(path: Path) -> str:
    """Read text with line endings normalized, so CRLF checkout noise is not drift.

    ``.gitattributes`` pins ``eol=lf`` only for hooks and shell scripts; Markdown
    assets are still materialized with ``core.autocrlf=true``, so the same blob can
    appear as LF in CI and CRLF on Windows. Byte comparison would report permanent
    drift and re-copy on every run.

    ``errors="replace"`` is deliberate: this is a gate, so it must always report a
    verdict instead of dying on a stray byte. Byte-identical files stay equal after
    replacement, so a legitimately synced pair is never flagged as drift.
    """
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").strip()


def _same(src: Path, dst: Path) -> bool:
    """Report whether the mirror copy already matches the canonical file."""
    if not dst.is_file():
        return False
    return _normalized(src) == _normalized(dst)


def _rel(path: Path, base: Path) -> str:
    """Render a path relative to ``base`` with forward slashes for stable output."""
    return path.relative_to(base).as_posix()


def _collect_file_level(category: str, canon: Path, mirror: Path) -> Iterator[Change]:
    """Diff a flat directory of ``*.md`` files (the ``agents`` granularity).

    A missing mirror container is reported but does **not** stop the walk: the file-level
    changes emitted right after it are applied in order, so one pass converges from an
    empty mirror. Short-circuiting here would require running the sync twice.
    """
    if not mirror.is_dir():
        yield Change("mirror-missing", f"{category}/", None, mirror)

    canon_files = {p.name: p for p in canon.glob("*.md")}
    mirror_files = list(mirror.glob("*.md")) if mirror.is_dir() else []

    for orphan in sorted(mirror_files):
        if orphan.name not in canon_files:
            yield Change("orphan", f"{category}/{orphan.name}", None, orphan)

    for name in sorted(canon_files):
        src = canon_files[name]
        dst = mirror / name
        if _same(src, dst):
            continue
        yield Change("missing" if not dst.is_file() else "drift", f"{category}/{name}", src, dst)


def _collect_dir_level(category: str, canon: Path, mirror: Path) -> Iterator[Change]:
    """Diff a directory of skill directories, recursively (the ``skills`` granularity).

    Like the file-level walker this keeps going after a missing container, and it also
    emits the individual files of a not-yet-existing skill directory -- otherwise the
    directory would be created empty and only filled on a second run.
    """
    if not mirror.is_dir():
        yield Change("mirror-missing", f"{category}/", None, mirror)

    canon_dirs = {p.name: p for p in canon.iterdir() if p.is_dir()}
    mirror_dirs = [p for p in mirror.iterdir() if p.is_dir()] if mirror.is_dir() else []

    for orphan in sorted(mirror_dirs):
        if orphan.name not in canon_dirs:
            yield Change("orphan", f"{category}/{orphan.name}/", None, orphan)

    for name in sorted(canon_dirs):
        src_dir = canon_dirs[name]
        dst_dir = mirror / name
        dst_exists = dst_dir.is_dir()
        src_files = sorted((p for p in src_dir.rglob("*") if p.is_file()), key=lambda p: _rel(p, src_dir))
        src_rels = {_rel(p, src_dir) for p in src_files}

        if not dst_exists:
            yield Change("missing", f"{category}/{name}/", None, dst_dir)

        if dst_exists:
            for extra in sorted(p for p in dst_dir.rglob("*") if p.is_file()):
                if _rel(extra, dst_dir) not in src_rels:
                    yield Change("extra", f"{category}/{name}/{_rel(extra, dst_dir)}", None, extra)

        for src in src_files:
            dst = dst_dir / _rel(src, src_dir)
            if _same(src, dst):
                continue
            yield Change(
                "missing" if not dst.is_file() else "drift",
                f"{category}/{name}/{_rel(src, src_dir)}",
                src,
                dst,
            )


def collect_changes() -> list[Change]:
    """Gather every divergence across all mirror pairs."""
    changes: list[Change] = []
    for category, canon, mirror, mode in MIRROR_PAIRS:
        if not canon.is_dir():
            raise SystemExit(f"[ERROR] canonical source missing (repo incomplete): {canon}")
        walker = _collect_file_level if mode == "file" else _collect_dir_level
        changes.extend(walker(category, canon, mirror))
    return changes


def _apply(change: Change) -> None:
    """Materialize one change on disk."""
    if change.kind == "orphan":
        if change.dst.is_dir():
            shutil.rmtree(change.dst)
        else:
            change.dst.unlink()
        return

    if change.kind == "extra":
        change.dst.unlink()
        return

    if change.src is None:  # container directory to (re)create
        change.dst.mkdir(parents=True, exist_ok=True)
        return

    change.dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(change.src, change.dst)


def _report(changes: list[Change], verify: bool) -> None:
    """Print one line per change, tagged with the pass that produced it."""
    tag = "VERIFY" if verify else "SYNC"
    for change in changes:
        if change.kind in {"orphan", "extra"}:
            print(f"[{tag}] mirror leftover: {change.label}")
        elif change.kind == "mirror-missing":
            print(f"[{tag}] mirror directory missing: {change.label}")
        elif change.kind == "missing":
            print(f"[{tag}] mirror entry missing: {change.label}")
        else:
            print(f"[{tag}] content drift: {change.label}")


def _git_add_mirror() -> None:
    """Stage the mirror paths so the pending commit carries the regenerated copy."""
    cmd = ["git", "-C", str(ROOT), "add", "--", *STAGED_PATHS]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        print(f"[SYNC] git add failed: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(1)
    print(f"[SYNC] staged mirror paths: {' '.join(STAGED_PATHS)}")


def main(argv: list[str] | None = None) -> int:
    """Entry point: sync (default) or verify the ``.claude`` -> ``.qoder`` mirror."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify", action="store_true", help="read-only check; exit 1 on drift")
    parser.add_argument(
        "--auto-stage",
        action="store_true",
        help="git add the mirror paths after syncing (used by pre-commit)",
    )
    args = parser.parse_args(argv)

    changes = collect_changes()

    if args.verify:
        if changes:
            _report(changes, verify=True)
            print(
                f"[VERIFY] {len(changes)} divergence(s) found; "
                f"fix with: uv run python scripts/sync_agent_assets.py",
                file=sys.stderr,
            )
            return 1
        print("[VERIFY] mirror is consistent (.claude is the only canonical source)")
        return 0

    if not changes:
        print("mirror already up to date (.claude is the only canonical source)")
        # Stage anyway: the mirror can be byte-consistent on disk and still untracked.
        # Returning here without staging would commit .claude/ without .qoder/, leaving
        # the repository inconsistent and failing check_assets.py's mirror checks in CI.
        if args.auto_stage:
            _git_add_mirror()
        return 0

    _report(changes, verify=False)
    for change in changes:
        _apply(change)
    print(f"\n[SYNC] applied {len(changes)} change(s) to .qoder, running final verify...")

    # Re-scan instead of recursing: a failed terminal check must exit non-zero.
    leftover = collect_changes()
    if leftover:
        _report(leftover, verify=True)
        print(f"[VERIFY] {len(leftover)} divergence(s) remain after sync", file=sys.stderr)
        return 1
    print("[VERIFY] mirror is consistent (.claude is the only canonical source)")

    if args.auto_stage:
        _git_add_mirror()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
