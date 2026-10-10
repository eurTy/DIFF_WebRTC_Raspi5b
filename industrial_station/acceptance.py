"""Explicit live acceptance run against the SIMULATED gateway, never real I/O."""
import argparse
import asyncio
import json
import time
import uuid
from pathlib import Path

import aiohttp

from .cli import Client


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--access", default="industrial_station/runtime/access.json")
    parser.add_argument("--output", default="industrial_station/runtime/live-smoke.json")
    args = parser.parse_args()
    config = json.loads(Path(args.access).read_text(encoding="utf-8"))
    prefix = "smoke-" + str(uuid.uuid4()) + "-"
    evidence = {"source": "SIMULATED", "prefix": prefix, "checks": [], "passed": False}
    def check(name, condition, **details):
        evidence["checks"].append({"name": name, "passed": bool(condition), **details})
        if not condition:
            raise AssertionError(name)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            client = Client(session, config["url"], config["tokens"]["operator"])
            async def status():
                return await client.request("GET", "/api/status")
            async def command(name, action, parameter=0, expected="COMPLETED"):
                result = await client.command(action, parameter, prefix + name)
                check(name, result["status"] == expected, status=result["status"], reason=result["reason"])
                return result
            initial = await status()
            check("simulation_only", initial["source"] == "SIMULATED" and
                  initial["sample"]["source"] == "SIMULATED" and initial["quality"] == "GOOD")
            check("initial_home", initial["sample"]["state"] == "IDLE")
            async with session.get(config["url"] + "/api/status") as response:
                check("anonymous_denied", response.status == 401, http_status=response.status)
            observer = {"Authorization": "Bearer " + config["tokens"]["observer"]}
            async with session.post(config["url"] + "/api/commands", headers=observer,
                                    json={"command_id": prefix + "forbidden", "action": "ARM"}) as response:
                check("observer_cannot_write", response.status == 403, http_status=response.status)
            await command("arm", "ARM")
            await command("clear", "INJECT")
            await command("reset", "RESET")
            await command("manual", "MODE", 1)
            before = (await status())["sample"]["motion_starts"]
            await command("extend", "EXTEND")
            for _ in range(9):
                result = await client.command("EXTEND", command_id=prefix + "extend")
                if result["status"] != "COMPLETED":
                    raise AssertionError("Duplicate result changed")
            after = (await status())["sample"]["motion_starts"]
            check("ten_identical_requests_one_motion", after - before == 1, motion_delta=after - before)
            await command("return", "RETRACT")
            await command("missing_limit", "INJECT", 1)
            result = await command("timeout", "EXTEND", expected="FAILED")
            check("timeout_reason", result["reason"] == "ACTION_TIMEOUT", duration_ms=result.get("device_duration_ms"))
            await command("clear_timeout", "INJECT")
            await command("reset_timeout", "RESET")
            await command("return_after_timeout", "RETRACT")
            await command("both_limits", "INJECT", 2)
            sample = (await status())["sample"]
            check("both_limits_alarm", sample["alarm"] == "LIMIT_CONFLICT" and
                  not sample["extend_output"] and not sample["retract_output"], alarm=sample["alarm"])
            await command("clear_limits", "INJECT")
            await command("reset_limits", "RESET")
            await command("freeze_heartbeat", "INJECT", 5)
            qualities = []
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                state = await status()
                qualities.append(state["quality"])
                if any(q != "GOOD" for q in qualities) and state["quality"] == "GOOD":
                    break
                await asyncio.sleep(.05)
            check("frozen_heartbeat_detected", any(q != "GOOD" for q in qualities), qualities=sorted(set(qualities)))
            check("recovery_requires_rearm", state["quality"] == "GOOD" and not state["armed"])
            await command("rearm", "ARM")
            await command("final_reset", "RESET")
            final = await status()
            check("final_idle_no_alarm", final["sample"]["state"] == "IDLE" and final["sample"]["alarm"] == "NONE")
            evidence["commands"] = (await client.request("GET", "/api/report?prefix=" + prefix))["commands"]
            evidence["passed"] = True
    except Exception as error:
        evidence["error"] = str(error)
        raise
    finally:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(json.dumps({"output": str(output), "passed": evidence["passed"], "checks": evidence["checks"]}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
