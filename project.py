"""One read-only entry point for the frozen observer's four canonical modules."""
import argparse
import ast
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parent


def beneath(root, relative):
    value = PurePosixPath(relative)
    if value.is_absolute() or ".." in value.parts or "\\" in relative:
        raise ValueError("Expected a repository-relative path")
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("Path escapes root")
    return result


def digest(path):
    # Text normalization distinguishes real source changes from checkout line endings.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def inventory(root=ROOT):
    config = json.loads((root / "integration/project.json").read_text(encoding="utf-8"))
    records = []
    for module in config["modules"]:
        source = beneath(root, module["source"])
        paths = set()
        for pattern in module["patterns"]:
            found = {p for p in source.glob(pattern) if p.is_file()}
            if not found:
                raise ValueError(f"No source for {module['name']}: {pattern}")
            paths.update(found)
        for path in sorted(paths):
            relative = path.relative_to(source).as_posix()
            records.append({"module": module["name"], "source": path.relative_to(root).as_posix(),
                            "runtime": module["runtime"] + "/" + relative,
                            "sha256_lf": digest(path)})
    for relative in config["compose_files"] + config["host_units"]:
        if not beneath(root, relative).is_file():
            raise ValueError(f"Missing deployment input: {relative}")
    return {"project": config["project"], "baseline_commit": config["baseline_commit"],
            "scope": "Canonical application and test sources; vendored libraries remain in Git release",
            "normalization": "CRLF to LF", "files": records}


def compare(home, record):
    differences = []
    for item in record["files"]:
        path = beneath(home, item["runtime"])
        if not path.is_file():
            differences.append({"path": item["runtime"], "state": "MISSING"})
        elif digest(path) != item["sha256_lf"]:
            differences.append({"path": item["runtime"], "state": "DIFFERENT"})
    return {"checked": len(record["files"]), "differences": differences, "control": "READ_ONLY"}


def remote_status(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Expected an HTTP(S) base URL without credentials")
    endpoint = url.rstrip("/") + "/observe/status"
    request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=4) as response:
        payload = response.read(256 * 1024 + 1)
        if len(payload) > 256 * 1024:
            raise ValueError("Unexpectedly large status response")
        raw = json.loads(payload)
    return {"source": raw.get("source"), "quality": raw.get("quality"),
            "sample_age_ms": raw.get("sample_age_ms"),
            "capture": raw.get("sample", {}).get("capture", {}).get("quality"),
            "active_alarms": len(raw.get("active_alarms", [])), "control": raw.get("control")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Validate canonical files and Python syntax without importing applications")
    inv = sub.add_parser("inventory", help="Print a normalized source checksum manifest")
    inv.add_argument("--output", type=Path)
    cmp = sub.add_parser("compare-runtime", help="Read deployed sources on the Pi; never synchronize or delete")
    cmp.add_argument("--home", type=Path, default=Path("/home/eur"))
    status = sub.add_parser("status", help="Read the existing aggregate status API")
    status.add_argument("--url", default="http://192.168.3.19:8080")
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = remote_status(args.url)
            code = 0 if result["quality"] == "GOOD" and result["capture"] == "GOOD" else 1
        else:
            result = inventory()
            code = 0
            if args.command == "check":
                for item in result["files"]:
                    if item["source"].endswith(".py"):
                        ast.parse(beneath(ROOT, item["source"]).read_text(encoding="utf-8"))
                result = {"project": result["project"], "canonical_files": len(result["files"]),
                          "source_check": "PASS", "services_changed": False}
            elif args.command == "compare-runtime":
                result = compare(args.home, result)
                code = bool(result["differences"])
            elif args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return code
    except (OSError, ValueError, SyntaxError, KeyError) as error:
        print(json.dumps({"error": str(error), "services_changed": False}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
