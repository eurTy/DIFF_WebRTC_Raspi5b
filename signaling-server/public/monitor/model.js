(function (root) {
  'use strict';
  const format = (value, digits = 0) => Number.isFinite(value) ? value.toFixed(digits) : '--';
  function state(status, receivedAt, now) {
    if (!status || !Number.isFinite(receivedAt)) return { quality: 'OFFLINE', age: null, sample: null };
    const age = Number.isFinite(status.sample_age_ms) ? status.sample_age_ms + Math.max(0, now - receivedAt) : null;
    const quality = status.quality !== 'GOOD' || age === null || age > 2500 ? 'STALE' : 'GOOD';
    return { quality, age, sample: status.sample };
  }
  function videoQuality(value, receivedAt, now) {
    if (!value || !Number.isFinite(receivedAt) || now - receivedAt > 2000) return 'OFFLINE';
    if (value.sample.state !== 'ready') return 'UNCALIBRATED';
    const extra = Math.max(0, now - receivedAt);
    return value.sample.ageMs + extra > 500 || value.receivedAgoMs === null || value.receivedAgoMs + extra > 500 ? 'STALE' : 'GOOD';
  }
  function series(history, runId) {
    return history.filter(s => s.run_id === runId && Number.isFinite(s.mono_ms)).slice(-300);
  }
  const api = { format, state, videoQuality, series };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MonitorModel = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
