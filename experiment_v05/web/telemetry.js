// Numeric diagnostics only. No video payloads, page queries, or credentials.
export const RECORDER_VERSION='mobile-metrics-8.1';
const copy=value=>structuredClone(value);
const uid=()=>globalThis.crypto?.randomUUID?.()||`${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;

export class MeasurementLog {
  constructor({url,appVersion,environment={},timeOrigin=performance.timeOrigin,now=()=>performance.now(),limit=10000}) {
    const endpoint=new URL(url);
    this.now=now;this.limit=limit;this.sessionCount=0;this.active=null;
    this.run={id:uid(),opened_at_utc:new Date(timeOrigin).toISOString(),time_origin_unix_ms:timeOrigin,
      endpoint:{protocol:endpoint.protocol,hostname:endpoint.hostname,port:Number(endpoint.port||(endpoint.protocol==='https:'?443:80))},
      app_version:appVersion,recorder_version:RECORDER_VERSION,environment:copy(environment)};
    this.context={phone_network:'unknown',vpn_route:'unknown',note:'',evidence:'user-declared; not independently verified'};
    this.frames=[];this.samples=[];this.packets=[];this.events=[];this.clocks=[];this.sessions=[];
    this.discarded={frames:0,samples:0,packets:0,events:0,clocks:0,sessions:0};
  }
  push(kind,value) {
    const list=this[kind];list.push(value);
    if(list.length>this.limit){list.shift();this.discarded[kind]++;}
    return value;
  }
  event(type,details={},session=this.active) {
    return this.push('events',{...copy(details),at:this.now(),type,session_id:session?.id??null});
  }
  setContext(context) {
    this.context={...this.context,...copy(context)};
    this.event('conditions_changed',{conditions:this.context});
  }
  begin(config,reason) {
    this.end('superseded');
    this.active=this.push('sessions',{id:`${this.run.id}:${++this.sessionCount}`,started_at_ms:this.now(),ended_at_ms:null,
      start_reason:reason,end_reason:null,config:copy(config),conditions_at_start:copy(this.context),
      source:null,implementation:null,received_frames:0,presented_frames:0});
    this.event('session_start',{reason});return this.active;
  }
  end(reason,session=this.active) {
    if(!session||session.ended_at_ms!==null)return;
    this.event('session_end',{reason},session);session.ended_at_ms=this.now();session.end_reason=reason;
    if(this.active===session)this.active=null;
  }
  entry(kind,session,details) {
    return this.push(kind,{...details,session_id:session?.id??null,codec:session?.config.codec??null});
  }
  snapshot({controls,reconnects,state,environment}={}) {
    const at=this.now(),codecs=[...new Set(this.sessions.map(s=>s.config.codec))];
    return copy({schema:8,export_id:uid(),exported_at_utc:new Date().toISOString(),exported_at_ms:at,
      run:this.run,scope:'cumulative page run; repeated exports overlap; split by session_id and codec',
      measurement:{latency:'software publication to browser callback/canvas draw estimate, not optical glass-to-glass',
        time_domains:'at/received_at_ms/append_started_at_ms/append_completed_at_ms/ack_at_ms are browser performance milliseconds; captured_ms/server_sent_ms are server monotonic milliseconds',
        clock:'minimum-RTT offset estimate; symmetric path assumption; null means uncalibrated',
        frame_age:'current displayed frame age at sample time, includes time since last presentation',
        roi:'freshness is not verified clarity; JPEG base resolution can differ from output canvas',
        ack:'H264 SourceBuffer append completion; JPEG decode/draw completion; not proof of screen scanout',
        conditions:'manual conditions apply from conditions_changed event; browser effectiveType does not prove 4G or VPN route'},
      conditions:this.context,controls_at_export:controls,active_session_id:this.active?.id??null,
      state_at_export:state,environment_at_export:environment,reconnects,codecs,
      retention:{limit_per_array:this.limit,discarded:this.discarded},sessions:this.sessions,
      frames:this.frames,samples:this.samples,packets:this.packets,events:this.events,clocks:this.clocks});
  }
  filename(data) {
    const codec=data.codecs.length===1?data.codecs[0]:data.codecs.length?'mixed':'none';
    return `svt-${this.run.endpoint.port}-${codec}-${data.exported_at_utc.replace(/[:.]/g,'-')}-${data.export_id.slice(-8)}.json`;
  }
}

export function browserEnvironment() {
  const c=navigator.connection||navigator.mozConnection||navigator.webkitConnection;
  return {user_agent:navigator.userAgent,language:navigator.language,platform:navigator.platform,
    viewport:{width:innerWidth,height:innerHeight,dpr:devicePixelRatio},
    screen:{width:screen.width,height:screen.height},visibility:document.visibilityState,online:navigator.onLine,
    connection_estimate:c?{effective_type:c.effectiveType??null,rtt_ms:c.rtt??null,downlink_mbps:c.downlink??null,save_data:c.saveData??null}:null};
}
