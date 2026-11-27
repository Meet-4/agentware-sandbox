"""Trace: tamper-evident, hash-chained JSONL audit log.

Every pipeline step appends one event; each entry's `prev_hash` chains to
the previous entry's hash. Written even on failure or kill. `verify()` walks
the chain and reports the first tampered line index.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def _digest(obj: dict) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


@dataclass
class Trace:
    run_id: str
    out_dir: Path
    started: float = field(default_factory=time.time)
    prev_hash: str = "GENESIS"
    events: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        (self.out_dir / "traces").mkdir(parents=True, exist_ok=True)

    def event(self, kind: str, **payload: Any) -> dict:
        entry = {
            "ts": round(time.time(), 3),
            "seq": len(self.events),
            "event": kind,
            **payload,
        }
        entry["prev_hash"] = self.prev_hash
        entry["entry_hash"] = _digest(entry)
        self.prev_hash = entry["entry_hash"]
        self.events.append(entry)
        path = self.out_dir / "traces" / f"{self.run_id}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
        return entry

    def summary(self, text: str) -> None:
        """Append a human-readable run summary (traces/<run>.md)."""
        p = self.out_dir / "traces" / f"{self.run_id}.md"
        with p.open("a", encoding="utf-8") as fh:
            fh.write(text.rstrip() + "\n\n")

    def cost(self) -> float:
        return round(sum(e.get("cost_usd", 0.0) for e in self.events), 6)

    @staticmethod
    def verify(path: Path) -> tuple[bool, int | None]:
        """Returns (ok, first_bad_line_index)."""
        prev = "GENESIS"
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                return False, i
            if entry.get("prev_hash") != prev:
                return False, i
            stored = entry.pop("entry_hash", None)
            if stored != _digest(entry):
                return False, i
            prev = stored
        return True, None
