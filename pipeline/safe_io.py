"""safe_io.py — crash-safe JSON state read/write.

The pipeline's shared state (keyword queue, done-list, drip state, taxonomy,
post logs) is read-modify-written by several GitHub Actions runs (drip every
30 min, refresh, newsletter). A plain ``path.write_text(json.dumps(...))`` can
truncate the file if the process dies mid-write, and a half-written file then
crashes the next reader.

``write_json`` writes to a temp file in the same directory and ``os.replace``s
it into place — an atomic rename on the same filesystem, so a reader always sees
either the old complete file or the new complete file, never a partial one.
``load_json`` tolerates a missing/corrupt file by returning a default instead of
raising.
"""

import json
import os
import tempfile
from pathlib import Path


def write_json(path, data, indent: int = 2) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=indent, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)        # atomic on the same filesystem
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return default
