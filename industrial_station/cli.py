"""Local client for the simulated actuator gateway."""
import argparse
import asyncio
import json
import os
import secrets
import time
import uuid
from pathlib import Path

import aiohttp

from .protocol import Action


class Client:
    def __init__(self, session, url, token):
        self.session, self.url = session, url.rstrip("/")
        self.headers = {"Authorization": "Bearer " + token}

    async def request(self, method, path, payload=None):
        async with self.session.request(method, self.url + path, headers=self.headers, json=payload) as response:
            text = await response.text()
            if response.status >= 400:
                raise RuntimeError(f"HTTP {response.status}: {text[:300]}")
            return json.loads(text)

    async def command(self, action, parameter=0, command_id=None, wait=True):
        command_id = command_id or str(uuid.uuid4())
        record = await self.request("POST", "/api/commands", {
            "command_id": command_id, "action": action, "parameter": parameter})
        deadline = time.monotonic() + 6
        while wait and record["status"] in ("CREATED", "SENT", "ACCEPTED"):
            if time.monotonic() > deadline:
                raise RuntimeError("Result wait expired; query the same command_id, do not resend a new movement")
            await asyncio.sleep(.05)
            record = await self.request("GET", "/api/commands/" + command_id)
        return record


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access", default="industrial_station/runtime/access.json")
    parser.add_argument("--url", help="Override configured gateway URL")
    parser.add_argument("--role", choices=["operator", "observer"], default="operator")
    sub = parser.add_subparsers(dest="task", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    cmd = sub.add_parser("command")
    cmd.add_argument("action", choices=[a.name for a in Action])
    cmd.add_argument("--parameter", type=int, default=0)
    cmd.add_argument("--id")
    cmd.add_argument("--no-wait", action="store_true")
    report = sub.add_parser("report")
    report.add_argument("--prefix", default="")
    report.add_argument("--output", default="industrial_station/runtime/report.json")
    demo = sub.add_parser("demo")
    demo.add_argument("--cycles", type=int, default=100)
    demo.add_argument("--output", default="industrial_station/runtime/demo.json")
    args = parser.parse_args()
    path = Path(args.access)
    if args.task == "init":
        path.parent.mkdir(parents=True, exist_ok=True)
        config = {"url": args.url or "http://127.0.0.1:8091",
                  "tokens": {role: secrets.token_urlsafe(32) for role in ("operator", "observer")}}
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as output:
            json.dump(config, output, indent=2)
        print(json.dumps({"access_file": str(path), "created": True}))
        return
    config = json.loads(path.read_text(encoding="utf-8"))
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
        client = Client(session, args.url or config["url"], config["tokens"][args.role])
        if args.task == "status":
            print(json.dumps(await client.request("GET", "/api/status"), indent=2))
        elif args.task == "command":
            print(json.dumps(await client.command(args.action, args.parameter, args.id, not args.no_wait), indent=2))
        else:
            from urllib.parse import urlencode
            prefix = args.prefix if args.task == "report" else "demo-" + str(uuid.uuid4()) + "-"
            if args.task == "demo":
                if not 1 <= args.cycles <= 1000:
                    raise ValueError("cycles must be in 1..1000")
                for index, (action, parameter) in enumerate([("ARM", 0), ("INJECT", 0), ("RESET", 0), ("MODE", 2)]):
                    result = await client.command(action, parameter, prefix + "setup-" + str(index))
                    if result["status"] != "COMPLETED":
                        raise RuntimeError("Setup rejected: " + json.dumps(result))
                for index in range(args.cycles):
                    result = await client.command("CYCLE", command_id=prefix + str(index))
                    print(json.dumps({"cycle": index + 1, "status": result["status"],
                                      "duration_ms": result.get("device_duration_ms")}), flush=True)
                    if result["status"] != "COMPLETED":
                        break
            result = await client.request("GET", "/api/report?" + urlencode({"prefix": prefix}))
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(result, indent=2), encoding="utf-8")
            print(json.dumps({"report": str(output), "cycles": result["cycle_results"],
                              "duration_ms": result["cycle_duration_ms"]}), flush=True)
            if args.task == "demo" and result["cycle_results"]["COMPLETED"] != args.cycles:
                raise RuntimeError("Demo failed; report retains failed/unknown results")


if __name__ == "__main__":
    asyncio.run(main())
