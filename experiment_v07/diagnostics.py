"""Bounded numeric evidence only: no image bytes, addresses or credentials."""
from collections import deque
import logging
import math
import re
import time
import uuid

LOG = logging.getLogger(__name__)


class TraceBook:
    def __init__(self, limit=12000):
        self.boot_id = uuid.uuid4().hex[:16]
        self.events = deque(maxlen=limit)
        self.sessions = deque(maxlen=64)
        self.discarded = 0

    def start(self, client_id, codec):
        if client_id is not None and (not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9:_-]{1,96}", client_id)):
            raise ValueError("Invalid diagnostic session")
        session = {"server_session_id": uuid.uuid4().hex[:16], "client_session_id": client_id,
                   "codec": codec, "started_ms": time.perf_counter()*1000, "ended_ms": None, "reason": None,
                   "reports": 0}
        self.sessions.append(session)
        self.add(session, "session_start")
        return session

    def add(self, session, event, **details):
        if len(self.events) == self.events.maxlen:
            self.discarded += 1
        row = {"server_session_id": session["server_session_id"], "at_ms": time.perf_counter()*1000,
               "event": event, **details}
        self.events.append(row)
        return row

    def end(self, session, reason):
        session.update(ended_ms=time.perf_counter()*1000, reason=reason)
        self.add(session, "session_end", reason=reason)
        LOG.warning("delivery_session %s", {k: session[k] for k in ("server_session_id", "client_session_id", "codec", "reason")})

    def report(self, value):
        if not isinstance(value, dict):
            raise ValueError("Diagnostic object required")
        session = next((s for s in self.sessions if s["server_session_id"] == value.get("server_session_id")), None)
        if session is None or session["reports"] >= 64:
            raise ValueError("Unknown or exhausted diagnostic session")
        if value.get("event") not in ("integrity_rejected", "socket_close", "parse_error", "client_resync"):
            raise ValueError("Invalid diagnostic event")
        clean = {"client_event": value["event"]}
        for key in ("seq", "client_at_ms", "declared_bytes", "actual_bytes", "expected_crc32", "actual_crc32", "close_code"):
            v = value.get(key)
            if v is not None:
                if type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 2**53-1:
                    raise ValueError("Invalid numeric evidence")
                clean[key] = v
        session["reports"] += 1
        self.add(session, "client_report", **clean)

    def snapshot(self, compact=False):
        rows = list(self.events)
        if compact:
            keep = set()
            for session in self.sessions:
                positions = [i for i, e in enumerate(rows) if e["server_session_id"] == session["server_session_id"]]
                keep.update(positions[-8:])
                for n, i in enumerate(positions):
                    if rows[i]["event"] in ("abort", "media_reset", "client_report", "session_start", "session_end"):
                        keep.update(positions[max(0, n-12):n+5])
                        seq = rows[i].get("seq")
                        if seq is not None:
                            keep.update(j for j in positions if rows[j].get("seq") == seq)
            rows = [row for i, row in enumerate(rows) if i in keep]
        return {"schema": 1, "boot_id": self.boot_id, "captured_ms": time.perf_counter()*1000,
                "scope": "bounded numeric in-memory trace; server monotonic time; no media",
                "limit": self.events.maxlen, "discarded": self.discarded,
                "selection": "fault neighborhoods and session edges" if compact else "all retained events",
                "retained_events": len(self.events), "returned_events": len(rows),
                "sessions": list(self.sessions), "events": rows}
