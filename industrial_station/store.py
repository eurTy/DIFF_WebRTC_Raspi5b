"""SQLite audit records, independent of the video process."""
import json
import math
import sqlite3
import time
from datetime import datetime, timezone


def utc():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, filename):
        self.db = sqlite3.connect(filename)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS commands (id TEXT PRIMARY KEY, status TEXT NOT NULL, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, utc TEXT, mono_ms REAL, kind TEXT, record TEXT);
        """)
        for record in self.pending():
            self.update(record["command_id"], "UNKNOWN", "GATEWAY_RESTART")

    def get(self, command_id):
        row = self.db.execute("SELECT record FROM commands WHERE id=?", (command_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, record):
        self.db.execute("INSERT INTO commands VALUES (?, ?, ?)",
                        (record["command_id"], record["status"], json.dumps(record)))
        self.event("command_created", record)

    def update(self, command_id, status, reason, duration_ms=None):
        record = self.get(command_id)
        if record["status"] == status and record["reason"] == reason:
            return record
        record.update(status=status, reason=reason, updated_utc=utc())
        if status in ("ACCEPTED", "COMPLETED", "REJECTED", "FAILED") and "confirmation_ms" not in record:
            record["confirmation_ms"] = time.monotonic() * 1000 - record["created_mono_ms"]
        if duration_ms is not None:
            record["device_duration_ms"] = duration_ms
        self.db.execute("UPDATE commands SET status=?, record=? WHERE id=?",
                        (status, json.dumps(record), command_id))
        self.event("command_result", record)
        return record

    def pending(self):
        return [json.loads(row[0]) for row in self.db.execute(
            "SELECT record FROM commands WHERE status IN ('CREATED','SENT','ACCEPTED')")]

    def event(self, kind, record):
        self.db.execute("INSERT INTO events (utc,mono_ms,kind,record) VALUES (?,?,?,?)",
                        (utc(), time.monotonic() * 1000, kind, json.dumps(record)))
        self.db.commit()

    def events(self, after=0, limit=100):
        return [dict(id=row[0], utc=row[1], mono_ms=row[2], kind=row[3], data=json.loads(row[4]))
                for row in self.db.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (after, limit))]

    def report(self, prefix=""):
        rows = self.db.execute("SELECT record FROM commands WHERE substr(id,1,?)=? ORDER BY rowid LIMIT 5001",
                               (len(prefix), prefix)).fetchall()
        commands = [json.loads(row[0]) for row in rows[:5000]]
        cycles = [c for c in commands if c["action"] == "CYCLE"]
        durations = sorted(c["device_duration_ms"] for c in cycles
                           if c["status"] == "COMPLETED" and "device_duration_ms" in c)
        counts = {status: sum(c["status"] == status for c in cycles) for status in
                  ("CREATED", "SENT", "ACCEPTED", "COMPLETED", "FAILED", "REJECTED", "UNKNOWN")}
        return {"source": "SIMULATED", "generated_utc": utc(), "prefix": prefix,
                "truncated": len(rows) > 5000, "commands": commands, "cycle_results": counts,
                "cycle_duration_ms": {"count": len(durations),
                    "mean": sum(durations) / len(durations) if durations else None,
                    "p95": durations[math.ceil(len(durations) * .95) - 1] if durations else None},
                "measurement_boundary": "Simulated device command start to simulated home-limit completion"}

    def close(self):
        self.db.close()
