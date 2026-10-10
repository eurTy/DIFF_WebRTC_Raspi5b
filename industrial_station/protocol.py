"""PDU zero-based addresses, unsigned 32-bit values in high-word-first order."""
from enum import IntEnum

VERSION = 2
DEVICE_ID = 1
STATUS_COUNT = 40
COMMAND_ADDRESS, COMMAND_COUNT = 100, 12
KEEPALIVE_ADDRESS, KEEPALIVE_COUNT = 120, 6
SIMULATED = 1


class State(IntEnum):
    IDLE = 1
    EXTENDING = 2
    EXTENDED = 3
    RETRACTING = 4
    FAULT = 5
    STOPPED = 6


class Mode(IntEnum):
    MANUAL = 1
    AUTO = 2


class Action(IntEnum):
    EXTEND = 1
    RETRACT = 2
    STOP = 3
    RESET = 4
    MODE = 5
    INJECT = 6
    CYCLE = 7
    ARM = 8


class Result(IntEnum):
    NONE = 0
    ACCEPTED = 1
    COMPLETED = 2
    REJECTED = 3
    FAILED = 4


class Reason(IntEnum):
    NONE = 0
    BOOT = 1
    SESSION = 2
    EXPIRED = 3
    SEQUENCE = 4
    BUSY = 5
    INTERLOCK = 6
    PARAMETER = 7
    ACTION_TIMEOUT = 8
    LIMIT_CONFLICT = 9
    COMM_TIMEOUT = 10
    STOPPED = 11
    FAULT_ACTIVE = 12


class Fault(IntEnum):
    NONE = 0
    TARGET_LIMIT_MISSING = 1
    BOTH_LIMITS = 2
    SLOW_MOTION = 3
    COMM_SILENT = 4
    FROZEN_HEARTBEAT = 5


def words(value):
    return [(int(value) >> 16) & 0xffff, int(value) & 0xffff]


def u32(registers, offset):
    return (registers[offset] << 16) | registers[offset + 1]


def command(action, sequence, parameter, boot, session, deadline_ms):
    return [int(action), *words(sequence), parameter, *words(boot),
            *words(session), *words(deadline_ms), 0, 0]


def decode_status(r):
    if len(r) != STATUS_COUNT or r[0] != VERSION or not r[11] & SIMULATED:
        raise ValueError("Not a compatible SIMULATED station; writes disabled")
    return {
        "protocol_version": r[0], "source": "SIMULATED", "state": State(r[1]).name,
        "mode": Mode(r[2]).name, "home_limit": bool(r[3] & 1),
        "extended_limit": bool(r[3] & 2), "local_permit": bool(r[3] & 4),
        "extend_output": bool(r[4] & 1), "retract_output": bool(r[4] & 2),
        "alarm": Reason(r[5]).name, "cycles": u32(r, 6), "last_action_ms": u32(r, 8),
        "current_mA": None, "quality_bits": r[11], "heartbeat": u32(r, 12),
        "status_sequence": u32(r, 14), "boot_id": u32(r, 16),
        "owner_session": u32(r, 18), "device_uptime_ms": u32(r, 20),
        "active_sequence": u32(r, 22), "ack_sequence": u32(r, 24),
        "ack_result": Result(r[26]).name, "ack_reason": Reason(r[27]).name,
        "model_position_permille": r[28], "injected_fault": Fault(r[29]).name,
        "motion_starts": u32(r, 30), "finished_sequence": u32(r, 32),
        "finished_result": Result(r[34]).name, "finished_reason": Reason(r[35]).name,
        "finished_duration_ms": u32(r, 36), "lease_remaining_ms": r[38],
    }
