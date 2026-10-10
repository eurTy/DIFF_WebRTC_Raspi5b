(function (root) {
  'use strict';
  class LatestRenderer {
    constructor({ render, release, discard, active, error }) {
      Object.assign(this, { render, release, discard, active, error });
      this.pending = null;
      this.busy = false;
    }
    enqueue(frame) {
      if (!this.active()) return;
      if (this.pending) { this.discard(this.pending); this.release(this.pending); }
      this.pending = frame;
      void this.drain();
    }
    async drain() {
      if (this.busy) return;
      this.busy = true;
      try {
        while (this.pending && this.active()) {
          const frame = this.pending;
          this.pending = null;
          await this.render(frame);
          if (this.active()) this.release(frame);
        }
      } catch (error) { this.error(error); }
      finally { this.busy = false; if (!this.active()) this.pending = null; }
    }
  }
  if (typeof module !== 'undefined' && module.exports) module.exports = { LatestRenderer };
  else root.LatestRenderer = LatestRenderer;
})(typeof globalThis !== 'undefined' ? globalThis : this);
