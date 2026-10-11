'use strict';
const $ = id => document.getElementById(id);
const fmt = MonitorModel.format;
let status = null, receivedAt = null, video = null, videoAt = null, history = [], events = [], selected = null;
let failures = 0, stopped = false, pollCount = 0;
const alarmNames = { CAPTURE_STALE: '采集数据过期', HOST_HOT: '主机温度偏高', HOST_BUSY: '主机负载偏高', WIFI_WEAK: '无线信号偏弱' };
const icons = () => window.lucide?.createIcons();
const write = (id, value) => { $(id).textContent = value; };
const clock = value => new Date(value).toLocaleTimeString('zh-CN', { hour12: false });
const time = value => new Date(value).toLocaleString('zh-CN', { hour12: false });

async function get(path) {
  const response = await fetch('/observe/' + path, { cache: 'no-store', signal: AbortSignal.timeout(2500) });
  if (!response.ok) throw new Error('HTTP ' + response.status);
  return response.json();
}

function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const a = document.createElement('a'); a.href = url; a.download = name; a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function changeView(name) {
  for (const button of document.querySelectorAll('[data-view]')) {
    const active = button.dataset.view === name;
    button.classList.toggle('selected', active); button.setAttribute('aria-selected', String(active));
  }
  $('view-live').hidden = name !== 'live'; $('view-events').hidden = name !== 'events';
  if (name === 'live') drawTrend();
}

function renderStatus() {
  const current = MonitorModel.state(status, receivedAt, performance.now());
  const quality = failures > 0 ? 'STALE' : current.quality;
  $('connection').className = 'badge' + (quality === 'GOOD' ? '' : ' offline');
  write('connection', quality === 'GOOD' ? '状态在线' : '状态过期');
  $('offline').hidden = quality === 'GOOD';
  write('node-quality', quality);
  write('sample-age', current.age === null ? '状态未更新' : '状态年龄 ' + fmt(current.age) + ' ms');
  const sample = current.sample, sensors = sample?.sensors;
  if (sensors) {
    write('temperature', fmt(sensors.temperature_c, 1)); write('cpu', fmt(sensors.cpu_percent, 1));
    write('frequency', fmt(sensors.frequency_mhz) + ' MHz'); write('wifi', fmt(sensors.wifi_signal_dbm) + ' dBm');
    write('tx', fmt(sensors.wifi_tx_mbps, 2) + ' Mbps'); write('rx', fmt(sensors.wifi_rx_mbps, 2) + ' Mbps');
    write('memory', fmt(Number.isFinite(sensors.memory_available_bytes) ? sensors.memory_available_bytes / 1e9 : null, 2) + ' GB');
    write('memory-control', sensors.memory_cgroup ? '内核支持' : '未启用');
    write('sample-late', fmt(sample.sampling_late_ms, 1) + ' ms');
    const capture = sample.capture;
    const age = Number.isFinite(capture.age_ms) ? capture.age_ms : null;
    write('capture-age', fmt(age)); write('capture-quality', quality !== 'GOOD' ? '状态已过期' : age > 500 ? 'STALE' : capture.quality);
    $('capture-age').classList.toggle('alert', age > 500 || quality !== 'GOOD');
    const alarms = status.active_alarms;
    write('alarm-count', String(alarms.length)); $('alarm-count').classList.toggle('alert', alarms.length > 0);
    $('active-alarms').replaceChildren(...(alarms.length ? alarms : ['NONE']).map(code => {
      const li = document.createElement('li'); li.textContent = code === 'NONE' ? '无活动告警' : alarmNames[code] || code; return li;
    }));
    write('last-updated', '采样时间 ' + time(sample.utc));
  }
  const vq = MonitorModel.videoQuality(video, videoAt, performance.now());
  const age = Number.isFinite(video?.sample.ageMs) ? video.sample.ageMs + Math.max(0, performance.now() - videoAt) : null;
  write('video-age', fmt(age)); $('video-age').classList.toggle('alert', vq !== 'GOOD');
  write('video-quality', { GOOD: '画面新鲜', STALE: '画面过期', OFFLINE: '视频未更新', UNCALIBRATED: '时钟未校准' }[vq]);
  $('video-warning').hidden = vq === 'GOOD'; write('video-warning', $('video-quality').textContent);
  write('video-total', vq === 'OFFLINE' ? '--' : fmt(video?.stages?.total));
  write('video-clock', Number.isFinite(video?.sample.uncertaintyMs) ? '时钟估计余量 ' + fmt(video.sample.uncertaintyMs, 1) + ' ms' : '校准中');
  for (const key of ['capture', 'dispatch', 'network', 'decode']) write('stage-' + key, fmt(video?.stages?.[key], 1) + ' ms');
  if (video) {
    write('stream-status', vq === 'GOOD' ? 'WS · JPEG' : video.status);
    write('stream-rate', video.stats); write('frame-id', '帧 ' + (video.frameId ?? '--'));
    write('video-spec', video.resolution.join(' × ') + ' · MJPEG');
  }
}

function eventRow(event) {
  const row = document.createElement('button'); row.className = 'event-row' + (selected === event.id ? ' selected' : '');
  row.title = event.code + ' / ' + event.phase;
  const date = document.createElement('time'); date.textContent = time(event.utc); date.dateTime = event.utc;
  const phase = document.createElement('span'); phase.className = 'phase' + (event.phase === 'RECOVERED' ? ' recovered' : '');
  phase.textContent = event.phase === 'RECOVERED' ? '恢复' : '触发';
  const title = document.createElement('span'); title.textContent = event.title;
  const arrow = document.createElement('i'); arrow.dataset.lucide = 'chevron-right';
  row.append(date, phase, title, arrow); row.onclick = () => { changeView('events'); void showEvent(event.id); };
  return row;
}

function empty(text) { const p = document.createElement('p'); p.className = 'empty'; p.textContent = text; return p; }

function renderEvents() {
  write('event-count', String(events.length));
  const filtered = events.filter(e => $('event-filter').value === 'all' || e.phase === $('event-filter').value);
  $('event-list').replaceChildren(...(filtered.length ? filtered.map(eventRow) : [empty('暂无对应事件')]));
  $('recent-list').replaceChildren(...(events.length ? events.slice(0, 5).map(eventRow) : [empty('暂无告警事件')]));
  icons();
}

async function showEvent(id) {
  selected = id; renderEvents();
  const panel = $('event-detail'); panel.replaceChildren(empty('读取事件...'));
  try {
    const event = await get('events/' + id);
    if (selected !== id) return;
    const title = document.createElement('h3'); title.textContent = event.title;
    const dl = document.createElement('dl');
    const values = { '事件时间': time(event.utc), '规则 / 阶段': event.code + ' / ' + event.phase,
      '来源': event.source, '快照质量': event.frame.quality,
      '事件减帧发布时间': fmt(event.frame_delta_ms, 1) + ' ms',
      '前后状态样本': event.before.length + ' / ' + event.after.length,
      '后续上下文': event.context_complete ? '已完成' : '未完整', '关联时钟': 'Pi 单调时钟 / 非光学同步' };
    for (const [key, value] of Object.entries(values)) {
      const div = document.createElement('div'), dt = document.createElement('dt'), dd = document.createElement('dd');
      dt.textContent = key; dd.textContent = value; div.append(dt, dd); dl.append(div);
    }
    panel.replaceChildren(title, dl);
    if (event.image_available) {
      const img = document.createElement('img'); img.src = '/observe/snapshot/' + id; img.alt = '事件关联帧，' + event.frame.quality;
      img.onerror = () => img.replaceWith(empty('快照已不可用')); panel.append(img);
    } else panel.append(empty('无保留快照'));
    const button = document.createElement('button'); button.className = 'text-button';
    button.innerHTML = '<i data-lucide="download"></i>导出事件 JSON';
    button.onclick = () => download(event, 'incident-' + id + '.json'); panel.append(button);
    const details = document.createElement('details'), summary = document.createElement('summary'), pre = document.createElement('pre');
    summary.textContent = '原始测量记录'; pre.textContent = JSON.stringify(event.sample, null, 2); details.append(summary, pre); panel.append(details); icons();
  } catch { if (selected === id) panel.replaceChildren(empty('事件读取失败')); }
}

function drawTrend() {
  const canvas = $('trend'), width = canvas.clientWidth, height = canvas.clientHeight;
  if (width < 1) return;
  const dpr = window.devicePixelRatio || 1; canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
  const ctx = canvas.getContext('2d'); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, width, height);
  const points = MonitorModel.series(history, status?.run_id);
  const left = 34, right = width - 12, top = 12, bottom = height - 24;
  ctx.font = '10px Segoe UI'; ctx.fillStyle = '#7b8a82'; ctx.lineWidth = 1;
  for (let value = 0; value <= 100; value += 25) {
    const y = bottom - value / 100 * (bottom - top);
    ctx.strokeStyle = '#e0e7e1'; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(right, y); ctx.stroke(); ctx.fillText(String(value), 5, y + 3);
  }
  if (!points.length) { ctx.fillText('等待实测样本', left + 10, top + 30); return; }
  const end = points[points.length - 1].mono_ms, start = end - 300000;
  for (const [key, color] of [['cpu_percent', '#158375'], ['temperature_c', '#b95a4e']]) {
    ctx.strokeStyle = color; ctx.lineWidth = 1.6; ctx.beginPath(); let drawing = false, previous = null;
    for (const point of points) {
      const value = point.sensors[key];
      if (!Number.isFinite(value) || point.mono_ms < start) { drawing = false; continue; }
      const x = left + (point.mono_ms - start) / (end - start) * (right - left), y = bottom - Math.max(0, Math.min(100, value)) / 100 * (bottom - top);
      if (!drawing || previous !== null && point.mono_ms - previous > 2500) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      drawing = true; previous = point.mono_ms;
    }
    ctx.stroke();
  }
  ctx.fillStyle = '#7b8a82'; ctx.fillText('-5 min', left, height - 5); ctx.fillText('现在', right - 24, height - 5);
}

async function poll() {
  if (stopped) return;
  try {
    const next = await get('status'); receivedAt = performance.now(); status = next; failures = 0;
    if (!history.length || ++pollCount % 15 === 0) history = await get('history?limit=300');
    else if (next.sample && history.at(-1)?.mono_ms !== next.sample.mono_ms) history = [...history.slice(-299), next.sample];
    if (pollCount % 3 === 0 || !events.length) { events = await get('events'); renderEvents(); }
    drawTrend();
  } catch { failures++; }
  renderStatus(); setTimeout(poll, 1000);
}

window.addEventListener('message', event => {
  if (event.origin !== location.origin || event.source !== $('video').contentWindow || event.data?.type !== 'pi-video-status') return;
  video = event.data; videoAt = performance.now(); renderStatus();
});
for (const button of document.querySelectorAll('[data-view]')) button.onclick = () => changeView(button.dataset.view);
$('all-events').onclick = () => changeView('events');
$('event-filter').onchange = renderEvents;
$('refresh-events').onclick = async () => { try { events = await get('events'); renderEvents(); if (selected !== null) await showEvent(selected); } catch { $('event-list').replaceChildren(empty('事件服务不可用')); } };
$('reconnect').onclick = () => { $('video').src = '/?transport=ws&flow=' + $('flow').value + '&embed=1&clock=separate'; video = null; };
$('flow').onchange = $('reconnect').onclick;
$('export').onclick = () => download({ exported_utc: new Date().toISOString(), status, video, history, events,
  boundary: 'Linux/V4L2 measurements and timestamp estimates. No physical actuator or optical latency calibration.' }, 'pi-observation.json');
$('trend').onpointermove = event => {
  const points = MonitorModel.series(history, status?.run_id); if (!points.length) return;
  const rect = event.currentTarget.getBoundingClientRect(), fraction = Math.max(0, Math.min(1, (event.clientX - rect.left - 34) / (rect.width - 46)));
  const target = points.at(-1).mono_ms - (1 - fraction) * 300000;
  const point = points.reduce((a, b) => Math.abs(a.mono_ms - target) < Math.abs(b.mono_ms - target) ? a : b);
  write('trend-readout', clock(point.utc) + ' · CPU ' + fmt(point.sensors.cpu_percent, 1) + '% · 温度 ' + fmt(point.sensors.temperature_c, 1) + ' °C');
};
window.addEventListener('resize', drawTrend);
window.addEventListener('pagehide', () => { stopped = true; });
setInterval(renderStatus, 250);
icons(); void poll();
