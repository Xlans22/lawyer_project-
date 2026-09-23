"""
Dossier storage: maps each client to a structured folder tree on disk.

    data/clients/<id>_<safe_name>/
        contracts/       عقود
        ids/             بطاقات
        rulings/         أحكام
        correspondence/  مراسلات
        scans/           مستندات

Filenames are sanitised and de-duplicated so an incoming mobile photo never
silently overwrites an existing document.
"""

from __future__ import annotations

import re
import shutil
import unicodedata
from datetime import datetime
from pathlib import Path

from core import config


def safe_name(name: str, fallback: str = "client") -> str:
    """Turn arbitrary text into a filesystem-safe component.

    Arabic letters are preserved (NTFS handles them fine); only characters that
    are illegal on Windows or dangerous in a path are replaced.
    """
    name = unicodedata.normalize("NFC", name or "").strip()
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = name.strip("._")
    return name[:80] or fallback


def client_folder_name(client_id: int, name: str) -> str:
    return f"{client_id:04d}_{safe_name(name)}"


def client_root(client_id: int, name: str) -> Path:
    return config.CLIENTS_DIR / client_folder_name(client_id, name)


def provision_client(client_id: int, name: str) -> Path:
    """Create the full dossier tree for a client and return its root."""
    root = client_root(client_id, name)
    for category in config.CATEGORIES:
        (root / category).mkdir(parents=True, exist_ok=True)
    return root


def category_dir(client_id: int, name: str, category: str) -> Path:
    category = category if category in config.CATEGORIES else config.DEFAULT_CATEGORY
    path = client_root(client_id, name) / category
    path.mkdir(parents=True, exist_ok=True)
    return path


def unique_path(directory: Path, filename: str) -> Path:
    """Return a non-colliding path inside `directory` for `filename`."""
    directory.mkdir(parents=True, exist_ok=True)
    stem = Path(safe_name(Path(filename).stem, "document")).name
    suffix = Path(filename).suffix.lower() or ".bin"

    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def store_bytes(
    client_id: int,
    client_name: str,
    category: str,
    filename: str,
    payload: bytes,
) -> Path:
    """Persist raw bytes into a client's dossier and return the final path."""
    dest = unique_path(category_dir(client_id, client_name, category), filename)
    dest.write_bytes(payload)
    return dest


def timestamped_name(prefix: str, suffix: str = ".jpg") -> str:
    """Filename for an incoming mobile capture."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{stamp}{suffix}"


def relocate(client_id: int, old_name: str, new_name: str) -> None:
    """Rename a client's dossier folder after the client record is renamed."""
    old = client_root(client_id, old_name)
    new = client_root(client_id, new_name)
    if old.exists() and old != new:
        if new.exists():
            # Merge rather than clobber — never destroy client material.
            for item in old.iterdir():
                target = new / item.name
                if item.is_dir() and target.exists():
                    for sub in item.iterdir():
                        shutil.move(str(sub), str(unique_path(target, sub.name)))
                    item.rmdir()
                elif not target.exists():
                    shutil.move(str(item), str(target))
            old.rmdir()
        else:
            old.rename(new)


def dossier_tree(client_id: int, name: str) -> dict[str, list[str]]:
    """Category -> list of filenames, for populating the document panel."""
    tree: dict[str, list[str]] = {}
    root = client_root(client_id, name)
    for category in config.CATEGORIES:
        folder = root / category
        files = sorted(p.name for p in folder.iterdir() if p.is_file()) if folder.is_dir() else []
        tree[category] = files
    return tree
