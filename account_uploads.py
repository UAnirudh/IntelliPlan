"""Delete only the exact uploaded files belonging to an account."""

from pathlib import Path
from collections.abc import Iterable
from uuid import uuid4


def owned_upload_paths(user_id: int, notes_root: str, lessons_root: str,
                       note_names: Iterable[str | None],
                       lesson_names: Iterable[str | None]) -> list[Path]:
    roots = ((Path(notes_root).resolve() / f"user_{int(user_id)}", note_names),
             (Path(lessons_root).resolve(), lesson_names))
    paths: list[Path] = []
    for root, names in roots:
        # Resolve the configured roots first; never follow an account-folder
        # symlink out of the configured notes directory.
        if root.parent == Path(notes_root).resolve() and root.resolve().parent != root.parent:
            raise OSError("Account upload folder leaves configured storage")
        root = root.resolve()
        for name in names:
            if not name:
                continue
            if Path(name).name != name or "/" in name or "\\" in name:
                raise OSError("Invalid stored upload name")
            candidate = (root / name).resolve()
            if candidate.parent != root:
                raise OSError("Upload leaves configured storage")
            paths.append(candidate)
    return paths


def remove_uploads(paths: Iterable[Path]) -> None:
    for path in paths:
        path.unlink(missing_ok=True)


def quarantine_uploads(paths: Iterable[Path]) -> list[tuple[Path, Path]]:
    """Stage exact files beside their originals; undo a partial staging failure."""
    staged: list[tuple[Path, Path]] = []
    try:
        for original in paths:
            if not original.exists():
                continue
            quarantine = original.with_name(f".delete-{uuid4().hex}-{original.name}")
            original.rename(quarantine)
            staged.append((original, quarantine))
    except OSError:
        restore_uploads(staged)
        raise
    return staged


def restore_uploads(staged: Iterable[tuple[Path, Path]]) -> None:
    for original, quarantine in staged:
        if quarantine.exists():
            quarantine.rename(original)
