"""Bounded local evidence; camera images stay on the Pi, outside Git."""
import hashlib
import json
import sqlite3


class EvidenceStore:
    def __init__(self, filename, sample_limit=86400, event_limit=500, image_limit_bytes=64 * 1024 * 1024):
        self.db = sqlite3.connect(filename)
        self.sample_limit, self.event_limit, self.image_limit = sample_limit, event_limit, image_limit_bytes
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS samples (id INTEGER PRIMARY KEY, mono_ms REAL, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, record TEXT NOT NULL, image BLOB);
        """)
        self.count = 0

    def sample(self, sample):
        self.db.execute("INSERT INTO samples(mono_ms,record) VALUES (?,?)", (sample["mono_ms"], json.dumps(sample)))
        self.count += 1
        if self.count % 60 == 0:
            self.db.execute("DELETE FROM samples WHERE id <= (SELECT max(id) FROM samples)-?", (self.sample_limit,))
            self.db.execute("PRAGMA incremental_vacuum(100)")
        self.db.commit()

    def event(self, record, image=None):
        record = {**record, "image_sha256": hashlib.sha256(image).hexdigest() if image else None}
        cursor = self.db.execute("INSERT INTO events(record,image) VALUES (?,?)", (json.dumps(record), image))
        event_id = cursor.lastrowid
        self.db.execute("DELETE FROM events WHERE id <= ?", (event_id - self.event_limit,))
        rows = self.db.execute("SELECT id,length(image) FROM events WHERE image IS NOT NULL ORDER BY id DESC").fetchall()
        size = 0
        for index, (identity, length) in enumerate(rows):
            size += length
            if size > self.image_limit or index >= 20:
                self.db.execute("UPDATE events SET image=NULL WHERE id=?", (identity,))
        self.db.commit()
        return event_id

    def detail(self, identity):
        row = self.db.execute("SELECT record,length(image) FROM events WHERE id=?", (identity,)).fetchone()
        return {"id": identity, **json.loads(row[0]), "image_available": row[1] is not None} if row else None

    def update_context(self, identity, after, complete):
        detail = self.detail(identity)
        if detail:
            detail.pop("id", None)
            detail.pop("image_available", None)
            detail["after"], detail["context_complete"] = after, complete
            self.db.execute("UPDATE events SET record=? WHERE id=?", (json.dumps(detail), identity))
            self.db.commit()

    def events(self, limit=100):
        rows = self.db.execute("SELECT id FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{k: v for k, v in self.detail(row[0]).items() if k not in ("before", "after", "sample")}
                for row in rows]

    def history(self, limit=600):
        rows = self.db.execute("SELECT record FROM samples ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [json.loads(row[0]) for row in reversed(rows)]

    def image(self, identity):
        row = self.db.execute("SELECT image FROM events WHERE id=?", (identity,)).fetchone()
        return row[0] if row else None

    def close(self):
        self.db.close()
