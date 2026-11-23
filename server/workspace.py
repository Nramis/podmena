from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from time import monotonic
from typing import Optional

from fastapi import HTTPException

from excel import open_excel, worksheet_info
from .db import new_id

TIMEOUT = 3_600
CAPACITY = 20
PREVIEW_TTL = TIMEOUT


@dataclass
class Preview:
    id: str
    result: Path
    request: object
    summary: dict
    touched: float = field(default_factory=monotonic)


@dataclass
class Book:
    id: str
    owner: str
    session: str
    name: str
    source: Path
    sheets: list
    touched: float = field(default_factory=monotonic)
    lock: Lock = field(default_factory=Lock)
    preview: Optional[Preview] = None


class Workspace:
    def __init__(self):
        self.directory = TemporaryDirectory(prefix="podmena-")
        self.root = Path(self.directory.name)
        self.entries = {}
        self.lock = Lock()

    def put(self, owner, session, name, data):
        with closing(open_excel(data)) as book:
            sheets = worksheet_info(book)
        entry_id = new_id()
        path = self.root / f"{entry_id}.xlsx"
        with self.lock:
            previous = [entry for entry in self.entries.values() if entry.session == session]
            if len(self.entries) >= CAPACITY and not previous:
                raise HTTPException(503, "Слишком много открытых книг. Попробуйте позже.")
            path.write_bytes(data)
            entry = Book(entry_id, owner, session, name, path, sheets)
            self.entries[entry_id] = entry
            for old in previous:
                self._remove(old)
        return {"id": entry_id, "name": name, "sheets": sheets}, [old.id for old in previous]

    def get(self, entry_id, owner, session):
        with self.lock:
            entry = self.entries.get(entry_id)
            if (entry is None or entry.owner != owner or entry.session != session
                    or monotonic() - entry.touched >= TIMEOUT):
                raise HTTPException(404, "Книга недоступна или сеанс файла истёк. Загрузите книгу снова.")
            entry.touched = monotonic()
            return entry

    def _remove(self, entry):
        with entry.lock:
            self.entries.pop(entry.id, None)
            entry.source.unlink(missing_ok=True)
            if entry.preview:
                entry.preview.result.unlink(missing_ok=True)

    def remove(self, entry):
        with self.lock:
            self._remove(entry)

    def close_session(self, session):
        with self.lock:
            entries = [entry for entry in self.entries.values() if entry.session == session]
            for entry in entries:
                self._remove(entry)
            return [entry.id for entry in entries]

    def clean(self):
        with self.lock:
            now = monotonic()
            for entry in list(self.entries.values()):
                if now - entry.touched >= TIMEOUT:
                    self._remove(entry)
                elif entry.preview and now - entry.preview.touched >= PREVIEW_TTL:
                    with entry.lock:
                        if entry.preview and now - entry.preview.touched >= PREVIEW_TTL:
                            entry.preview.result.unlink(missing_ok=True)
                            entry.preview = None

    def close(self):
        self.directory.cleanup()


def source_book(entry):
    if not entry.source.is_file():
        raise HTTPException(404, "Книга закрыта. Загрузите её снова.")
    return open_excel(entry.source.read_bytes())
