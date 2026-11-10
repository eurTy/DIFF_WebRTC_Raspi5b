"""Identify the served implementation without exposing deployment paths or Git state."""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from uuid import uuid4


def implementation(version):
    root = Path(__file__).resolve().parent.parent
    experiment = "experiment_v05" if version == "v0.5" else "experiment_v07"
    names = {f"{experiment}/server.py", "experiment_v05/codec.py", "experiment_v05/flow.py",
             "experiment_v05/telemetry.py", "web/protocol.js"}
    if experiment == "experiment_v07":
        names.update(str(path.relative_to(root)).replace("\\", "/") for path in (root / experiment).glob("*.py"))
    names.update(str(path.relative_to(root)).replace("\\", "/")
                 for directory in {root / experiment / "web", root / "experiment_v05/web"}
                 for path in directory.iterdir() if path.suffix in {".js", ".html", ".css"})
    hashes = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in sorted(names)}
    digest = hashlib.sha256("\n".join(f"{name}:{value}" for name, value in hashes.items()).encode()).hexdigest()
    return {"app_version": version, "source_sha256": digest, "source_files_sha256": hashes,
            "instance_id": uuid4().hex, "started_at_utc": datetime.now(timezone.utc).isoformat(),
            "scope": "listed application files, not container image or dependency lock"}
