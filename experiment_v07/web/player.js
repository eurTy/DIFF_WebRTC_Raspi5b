import '/app.js';

const probe=window.__svtProbe;
const fallback=new URL(location.href);fallback.hostname=location.hostname==='100.91.218.90'?'100.80.123.16':location.hostname;fallback.port='8090';fallback.pathname='/';fallback.search='';fallback.hash='';
const baseline=document.getElementById('baseline');baseline.href=fallback.href;baseline.textContent='保留版 8090';
window.__svtProbe=()=>({...probe(),schema:8,transport:'WebSocket/H264-fMP4-or-JPEG/TCP'});
