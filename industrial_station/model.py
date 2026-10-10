"""Deterministic actuator model; all positions and limit inputs are simulated."""
import secrets
import time
from collections import OrderedDict

from .protocol import Action, Fault, Mode, Reason, Result, State, VERSION, u32, words


class Station:
    def __init__(self, clock=time.monotonic, boot_id=None, move_ms=400, timeout_ms=1200,
                 lease_ms=1000, fault_ms=2000):
        if not 0 < move_ms < timeout_ms or lease_ms <= 0 or fault_ms <= 0:
            raise ValueError("Invalid simulation timing")
        self.clock, self.started = clock, clock()
        self.boot = boot_id or secrets.randbelow(0xffffffff) + 1
        self.move_ms, self.timeout_ms, self.lease_ms = move_ms, timeout_ms, lease_ms
        self.fault_ms = fault_ms
        self.state, self.mode, self.alarm = State.IDLE, Mode.MANUAL, Reason.NONE
        self.position, self.cycles, self.motion_starts = 0.0, 0, 0
        self.owner = self.last_keepalive = self.keepalive_counter = 0
        self.sequence = self.highwater = self.heartbeat = 0
        self.ack = (0, Result.NONE, Reason.NONE)
        self.finished = (0, Result.NONE, Reason.NONE, 0)
        self.active = None
        self.visited_extended = False
        self.fault, self.fault_until = Fault.NONE, 0
        self.last_action_ms = 0
        self.cache = OrderedDict()
        self.retired = {}

    def now_ms(self):
        return int(round((self.clock() - self.started) * 1000))

    def limits(self):
        if self.fault == Fault.BOTH_LIMITS:
            return True, True
        home, extended = self.position <= 0, self.position >= 1
        if self.fault == Fault.TARGET_LIMIT_MISSING and self.active:
            if self.state == State.EXTENDING:
                extended = False
            else:
                home = False
        return home, extended

    def _cache(self, seq, fingerprint, result, reason):
        self.cache[seq] = (fingerprint, result, reason)
        while len(self.cache) > 128:
            victim = next(key for key in self.cache if not self.active or key != self.active["seq"])
            del self.cache[victim]
        self.ack = (seq, result, reason)

    def _finish(self, result, reason):
        if not self.active:
            return
        seq = self.active["seq"]
        self.last_action_ms = max(0, self.now_ms() - self.active["began"])
        self.finished = (seq, result, reason, self.last_action_ms)
        fingerprint = self.cache[seq][0]
        self._cache(seq, fingerprint, result, reason)
        self.active = None

    def _trip(self, reason):
        self._finish(Result.FAILED, reason)
        self.state, self.alarm = State.FAULT, reason

    def tick(self):
        now = self.now_ms()
        self.sequence += 1
        self.retired = {key: until for key, until in self.retired.items() if until > now}
        if self.fault in (Fault.COMM_SILENT, Fault.FROZEN_HEARTBEAT) and now >= self.fault_until:
            self.fault = Fault.NONE
        if self.fault != Fault.FROZEN_HEARTBEAT:
            self.heartbeat = now
        if self.owner and now - self.last_keepalive >= self.lease_ms:
            self.retired[self.owner] = now + 5000
            if self.active:
                self._trip(Reason.COMM_TIMEOUT)
            self.owner = 0
        if self.fault == Fault.BOTH_LIMITS:
            self._trip(Reason.LIMIT_CONFLICT)
        if not self.active:
            return
        elapsed = now - self.active["leg_began"]
        duration = self.move_ms * (10 if self.fault == Fault.SLOW_MOTION else 1)
        start, target = self.active["start"], self.active["target"]
        self.position = start + (target - start) * min(1, elapsed / duration)
        home, extended = self.limits()
        reached = extended if target else home
        if reached:
            if target:
                self.visited_extended = True
                if self.active["cycle"]:
                    self._leg(0, now)
                    return
                self.state = State.EXTENDED
            else:
                self.state = State.IDLE
                if self.visited_extended:
                    self.cycles += 1
                    self.visited_extended = False
            self._finish(Result.COMPLETED, Reason.NONE)
        elif elapsed >= self.timeout_ms:
            self._trip(Reason.ACTION_TIMEOUT)

    def _leg(self, target, now):
        self.active.update(target=target, start=self.position, leg_began=now)
        self.state = State.EXTENDING if target else State.RETRACTING

    def keepalive(self, values):
        self.tick()
        boot, owner, counter = u32(values, 0), u32(values, 2), u32(values, 4)
        if boot != self.boot or not owner or owner != self.owner or counter <= self.keepalive_counter:
            return False
        self.keepalive_counter, self.last_keepalive = counter, self.now_ms()
        return True

    def execute(self, values):
        self.tick()
        action_value, seq, param = values[0], u32(values, 1), values[3]
        boot, session, deadline = u32(values, 4), u32(values, 6), u32(values, 8)
        fingerprint = tuple(values)
        def reject(reason, remember=False):
            self.ack = (seq, Result.REJECTED, reason)
            if remember:
                self._cache(seq, fingerprint, Result.REJECTED, reason)
            return self.ack
        if boot != self.boot:
            return reject(Reason.BOOT)
        if not session or not seq or values[10:] != [0, 0]:
            return reject(Reason.PARAMETER)
        try:
            action = Action(action_value)
        except ValueError:
            return reject(Reason.PARAMETER)
        if action != Action.ARM and session != self.owner:
            return reject(Reason.SESSION)
        if action == Action.ARM and (self.active or self.owner not in (0, session)):
            return reject(Reason.BUSY)
        if action == Action.ARM and session in self.retired:
            return reject(Reason.SESSION)
        # The sequence floor survives bounded-cache eviction within an owner session.
        if session == self.owner and seq in self.cache:
            previous, result, reason = self.cache[seq]
            if previous != fingerprint:
                return reject(Reason.SEQUENCE)
            self.ack = (seq, result, reason)
            return self.ack
        if deadline <= self.now_ms() or deadline > self.now_ms() + 5000:
            return reject(Reason.EXPIRED)
        if action == Action.ARM and self.owner != session:
            self.cache.clear()
            self.highwater = self.keepalive_counter = 0
            # Finished sequence numbers belong to the previous owner's namespace.
            self.finished = (0, Result.NONE, Reason.NONE, 0)
        if seq <= self.highwater:
            return reject(Reason.SEQUENCE)
        self.highwater = seq
        if param and action not in (Action.MODE, Action.INJECT):
            return reject(Reason.PARAMETER, True)
        if self.active and action != Action.STOP:
            return reject(Reason.BUSY, True)
        if action == Action.ARM:
            self.owner, self.last_keepalive = session, self.now_ms()
        elif action == Action.STOP:
            self._finish(Result.FAILED, Reason.STOPPED)
            if self.state != State.FAULT:
                self.state = State.IDLE if self.position <= 0 else State.EXTENDED if self.position >= 1 else State.STOPPED
        elif action == Action.INJECT:
            try:
                self.fault = Fault(param)
            except ValueError:
                return reject(Reason.PARAMETER, True)
            self.fault_until = self.now_ms() + self.fault_ms
        elif action == Action.RESET:
            if self.fault != Fault.NONE:
                return reject(Reason.FAULT_ACTIVE, True)
            self.alarm = Reason.NONE
            self.state = State.IDLE if self.position <= 0 else State.EXTENDED if self.position >= 1 else State.STOPPED
        elif action == Action.MODE:
            try:
                self.mode = Mode(param)
            except ValueError:
                return reject(Reason.PARAMETER, True)
        else:
            if self.alarm != Reason.NONE:
                return reject(Reason.FAULT_ACTIVE, True)
            manual = self.mode == Mode.MANUAL
            permitted = ((action == Action.EXTEND and manual and self.state in (State.IDLE, State.STOPPED)) or
                         (action == Action.RETRACT and manual and self.state in (State.EXTENDED, State.STOPPED)) or
                         (action == Action.CYCLE and not manual and self.state == State.IDLE))
            if not permitted:
                return reject(Reason.INTERLOCK, True)
            self.active = {"seq": seq, "began": self.now_ms(), "cycle": action == Action.CYCLE}
            self.motion_starts += 1
            self._leg(0 if action == Action.RETRACT else 1, self.now_ms())
            self._cache(seq, fingerprint, Result.ACCEPTED, Reason.NONE)
            return self.ack
        self._cache(seq, fingerprint, Result.COMPLETED, Reason.NONE)
        self.tick()
        return self.ack

    def registers(self):
        self.tick()
        home, extended = self.limits()
        outputs = (1 if self.state == State.EXTENDING else 2 if self.state == State.RETRACTING else 0)
        seq, result, reason = self.ack
        finished_seq, finished_result, finished_reason, duration = self.finished
        lease = max(0, self.lease_ms - (self.now_ms() - self.last_keepalive)) if self.owner else 0
        return [VERSION, self.state, self.mode, int(home) | int(extended) << 1 | 4,
                outputs, self.alarm, *words(self.cycles), *words(self.last_action_ms),
                0, 3, *words(self.heartbeat), *words(self.sequence), *words(self.boot),
                *words(self.owner), *words(self.now_ms()), *words(self.active["seq"] if self.active else 0),
                *words(seq), result, reason, round(self.position * 1000), self.fault,
                *words(self.motion_starts), *words(finished_seq), finished_result, finished_reason,
                *words(duration), lease, 0]
