"""Plain JSON state with serialized writers and recoverable multi-file commits."""

import fcntl
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path


class PerlaError(Exception):
    def __init__(self, code, message, remediation="Inspect the input and retry."):
        super().__init__(message)
        self.code, self.message, self.remediation = code, message, remediation


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path):
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            parse_constant=lambda value: reject_constant(value),
        )
    except (ValueError, UnicodeError) as exc:
        raise PerlaError("E_INVALID_JSON", f"Invalid JSON in {path}: {exc}") from exc


def reject_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def atomic_json(path, value):
    content = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
    atomic_text(path, content + "\n")


def atomic_text(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.state = self.root / ".perla"
        self.pending = None

    @classmethod
    def discover(cls, start):
        start = Path(start).resolve()
        for root in (start, *start.parents):
            if (root / ".perla" / "project.json").is_file():
                return cls(root)
        raise PerlaError(
            "E_NO_PROJECT",
            "No .perla project found.",
            "Run perla init --input game.json in the project directory.",
        )

    def path(self, relative):
        path = self.state / relative
        if not path.resolve().is_relative_to(self.state.resolve()):
            raise PerlaError("E_INVALID_PATH", "State path escapes .perla.")
        return path

    def read(self, relative, default=None):
        if self.pending is not None and relative in self.pending:
            return self.pending[relative]
        path = self.path(relative)
        return read_json(path) if path.exists() else default

    def write(self, relative, value):
        if self.pending is None:
            raise RuntimeError("State writes require a transaction")
        self.path(relative)
        canonical(value)
        self.pending[relative] = value

    def records(self, directory):
        names = {str(p.relative_to(self.state)) for p in self.path(directory).glob("*.json")}
        names.update(k for k in (self.pending or {}) if str(Path(k).parent) == directory)
        return [self.read(k) for k in sorted(names)]

    @contextmanager
    def transaction(self):
        """A journal is replayed after interrupted commits, before any CLI state read."""
        with self.path(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            journal = self.path(".transaction.json")
            if journal.exists():
                for name, value in read_json(journal).items():
                    atomic_json(self.path(name), value)
                journal.unlink()
            self.pending = {}
            try:
                yield self
                if self.pending:
                    atomic_json(journal, self.pending)
                    for name, value in self.pending.items():
                        atomic_json(self.path(name), value)
                    journal.unlink()
            finally:
                self.pending = None
                fcntl.flock(lock, fcntl.LOCK_UN)
