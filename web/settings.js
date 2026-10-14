export const SETTINGS_KEY='semantic-video-settings-v1';

export function validSettings(value) {
  if(!value||value.version!==1||!value.config)return false;
  const c=value.config;
  if(c.type!=='start'||!['semantic','static-roi','uniform','full'].includes(c.mode)||!['single','median','realtime'].includes(c.feedback))return false;
  if(!Number.isInteger(c.fps)||c.fps<2||c.fps>20||!Number.isInteger(c.kbps)||c.kbps<128||c.kbps>12000||![6,7,8].includes(c.seed))return false;
  if(!Array.isArray(c.regions)||c.regions.length!==2)return false;
  return c.regions.every((r,i)=>r&&r.name===['process-A','process-B'][i]&&['x','y','w','h','weight'].every(k=>Number.isFinite(r[k]))&&r.x>=0&&r.y>=0&&r.w>=.02&&r.h>=.02&&r.x+r.w<=1&&r.y+r.h<=1&&r.weight>=1&&r.weight<=10)
    &&c.regions.reduce((n,r)=>n+r.w*r.h,0)<=.5;
}

export function loadSettings(storage) {
  try {const value=JSON.parse(storage.getItem(SETTINGS_KEY));return validSettings(value)?value.config:null;} catch {return null;}
}

export function saveSettings(storage,config) {
  const value={version:1,config};
  if(!validSettings(value))return false;
  try {storage.setItem(SETTINGS_KEY,JSON.stringify(value));return true;} catch {return false;}
}
