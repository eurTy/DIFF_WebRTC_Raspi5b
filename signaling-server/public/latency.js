(function (root) {
  'use strict';

  class LatencyTracker {
    constructor(now = () => performance.now()) {
      this.now = now;
      this.reset();
    }

    reset() {
      this.requests = new Map();
      this.clocks = [];
      this.frames = new Map();
      this.latest = null;
      this.nextRequest = 0;
      this.lastReply = null;
    }

    prune(at = this.now()) {
      for (const [id, sent] of this.requests) {
        if (at - sent > 10000) this.requests.delete(id);
      }
      this.clocks = this.clocks.filter(clock => at - clock.at <= 30000).slice(-64);
      for (const [id, frame] of this.frames) {
        if (at - frame.created > 30000 && frame !== this.latest) this.frames.delete(id);
      }
      while (this.frames.size > 512) {
        const id = [...this.frames.keys()].find(key => this.frames.get(key) !== this.latest);
        this.frames.delete(id);
      }
    }

    request() {
      this.prune();
      const id = ++this.nextRequest;
      this.requests.set(id, this.now());
      return { type: 'latency_clock_request', request_id: id };
    }

    cancelRequest(id) {
      this.requests.delete(id);
    }

    acceptClock(message) {
      const at = this.now();
      const sent = this.requests.get(message.request_id);
      this.requests.delete(message.request_id);
      const received = message.server_receive_ms;
      const replied = message.server_send_ms;
      if (!Number.isFinite(sent) || at - sent > 10000 ||
          !Number.isFinite(received) || !Number.isFinite(replied) || replied < received) return false;
      const rtt = at - sent - (replied - received);
      if (rtt < 0) return false;
      this.clocks.push({ at, rtt, offset: ((received - sent) + (replied - at)) / 2 });
      this.lastReply = at;
      this.prune(at);
      return true;
    }

    frame(id) {
      this.prune();
      if (!this.frames.has(id)) this.frames.set(id, { id, created: this.now() });
      return this.frames.get(id);
    }

    noteMetadata(id, published) {
      if (Number.isFinite(published) && published > 0) this.frame(id).published = published;
    }

    noteDecoded(id, decoded = this.now()) {
      const frame = this.frame(id);
      frame.decoded = decoded;
      if (!this.latest || decoded >= this.latest.decoded) this.latest = frame;
    }

    noteTiming(id, timing, received = this.now()) {
      if (timing) Object.assign(this.frame(id), { timing, received });
    }

    stages() {
      const frame = this.latest, timing = frame?.timing;
      if (!timing || this.clocks.length < 3 || this.lastReply === null || this.now() - this.lastReply > 10000) return null;
      const best = this.clocks.reduce((a, b) => a.rtt < b.rtt ? a : b);
      const driverValid = (timing.flags & 0xe000) === 0x2000 && timing.driver > 0 && timing.driver <= timing.dequeued;
      const positive = value => Number.isFinite(value) && value >= 0 ? value : null;
      return {
        source: driverValid ? ((timing.flags & 0x70000) === 0x10000 ? 'V4L2 SOE' : 'V4L2 EOF') : 'V4L2 unknown',
        capture: driverValid ? positive(timing.published - timing.driver) : null,
        dispatch: positive(timing.sent - timing.published),
        network: positive(frame.received + best.offset - timing.sent),
        decode: positive(frame.decoded - frame.received),
        total: driverValid ? positive(frame.decoded + best.offset - timing.driver) : null,
      };
    }

    snapshot() {
      const at = this.now();
      this.prune(at);
      const blank = { state: 'calibrating', delayMs: null, ageMs: null, meanMs: null, p95Ms: null, uncertaintyMs: null, count: 0 };
      if (this.lastReply !== null && at - this.lastReply > 10000) return { ...blank, state: 'stale' };
      if (this.clocks.length < 3) return blank;
      // Minimum RTT limits queueing bias; expire it so clock drift is not accumulated indefinitely.
      const best = this.clocks.reduce((a, b) => a.rtt < b.rtt ? a : b);
      const uncertainty = best.rtt / 2 + 1;
      const values = [];
      for (const frame of this.frames.values()) {
        if (Number.isFinite(frame.published) && Number.isFinite(frame.decoded) && at - frame.decoded <= 10000) {
          const delay = frame.decoded + best.offset - frame.published;
          if (delay >= 0) values.push(delay);
        }
      }
      values.sort((a, b) => a - b);
      const result = {
        ...blank, state: 'waiting', uncertaintyMs: uncertainty, count: values.length,
        meanMs: values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null,
        p95Ms: values.length ? values[Math.ceil(values.length * 0.95) - 1] : null,
      };
      if (!this.latest || !Number.isFinite(this.latest.published)) return result;
      const delay = this.latest.decoded + best.offset - this.latest.published;
      if (delay < 0) return { ...result, state: 'invalid' };
      return { ...result, state: 'ready', delayMs: delay, ageMs: at + best.offset - this.latest.published };
    }
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { LatencyTracker };
  else root.VideoLatency = { LatencyTracker };
})(typeof globalThis !== 'undefined' ? globalThis : this);
