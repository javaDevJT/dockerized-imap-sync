#!/usr/bin/env bun
// App-scoped operator tool. Secret bytes use fd 3 internally, never stdout/argv.
import { spawn } from 'node:child_process';
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { request } from 'node:https';
import { randomBytes, createHash } from 'node:crypto';
import { isDeepStrictEqual } from 'node:util';
import { resolve } from 'node:path';

export const APP = 'imap-sync';
export const ORIGIN = 'https://192.168.0.2:8443';
export const DIRECTORY = '/mnt/Lake/imap-sync-openwoof';
export const HOST_KEY = `${DIRECTORY}/openwoof-ingest-key`;
export const CONTAINER_KEY = '/run/secrets/openwoof-ingest-key';
const ROOT = resolve(import.meta.dir, '..');
const RECEIPT = resolve(ROOT, 'artifacts/.private-openwoof/preparation.json');
const HELPER = resolve(ROOT,'artifacts/.private-openwoof/imap-openwoof-keychain');
const CA = readFileSync(resolve(import.meta.dir, 'truenas-ca.pem'));
const TLS = {ca:CA, serverName:'localhost', rejectUnauthorized:true};
const TELEMETRY_ENV={OTEL_EXPORTER_OTLP_ENDPOINT:'https://openwoof.javadevjt.tech',OTEL_EXPORTER_OTLP_PROTOCOL:'http/protobuf',
  OTEL_SERVICE_NAME:APP,OTEL_RESOURCE_ATTRIBUTES:'service.namespace=mail,deployment.environment.name=production',OPENWOOF_INGEST_KEY_FILE:CONTAINER_KEY};
export class SafeFailure extends Error {
  constructor(phase, jobId, errno) { super(phase); this.phase=phase; this.jobId=jobId; this.errno=errno; }
}
const fail = (phase, jobId) => { throw new SafeFailure(phase, jobId); };

export async function privateSecret(mode, executable=HELPER) {
  const child=spawn(executable,[mode],{stdio:['ignore','pipe','pipe','pipe']});
  const chunks=[]; let total=0,stdout='',stderrBytes=0,stderr='';
  const data=new Promise((resolvePromise,reject)=>{
    child.stdio[3].on('data',b=>{total+=b.length;if(total>16384){child.kill();reject(new SafeFailure('private-pipe-size'));}else chunks.push(b);});
    child.stdio[3].on('end',resolvePromise); child.stdio[3].on('error',()=>reject(new SafeFailure('private-pipe')));
  });
  child.stdout.on('data',b=>{if(stdout.length<4096)stdout+=b.toString();else child.kill();});
  child.stderr.on('data',b=>{stderrBytes+=b.length;if(stderr.length<256)stderr+=b.toString();});
  const code=await new Promise((resolvePromise,reject)=>{child.on('exit',resolvePromise);child.on('error',()=>reject(new SafeFailure('keychain-helper-launch')));});
  await data;
  const bytes=Buffer.concat(chunks); chunks.forEach(b=>b.fill(0));
  if(code!==0||stderrBytes||!bytes.length){bytes.fill(0);const safe=stderr.match(/^openwoof-keychain: ([a-z-]+)\n$/)?.[1];fail(safe?`keychain-helper-${safe}`:'keychain-helper');}
  try {const status=JSON.parse(stdout);if(status.ok!==true||typeof status.mode!=='string')throw Error();}catch{bytes.fill(0);fail('keychain-helper-status');}
  return bytes;
}

export async function connect(onNotification) {
  const key=await privateSecret('read-nas-to-fd');
  const ws=new WebSocket('wss://192.168.0.2:8443/api/current',{tls:TLS});
  const pending=new Map();let next=0;
  const call=(method,params=[])=>new Promise((resolvePromise,reject)=>{
    const id=++next,timer=setTimeout(()=>{pending.delete(id);reject(new SafeFailure('rpc-timeout'));},30000);
    pending.set(id,{resolve:resolvePromise,reject,timer,method});ws.send(JSON.stringify({jsonrpc:'2.0',id,method,params}));
  });
  ws.onmessage=e=>{let m;try{m=JSON.parse(String(e.data));}catch{return;}
    const p=pending.get(m.id);if(!p){if(m.method)try{onNotification?.(m);}catch{}return;}pending.delete(m.id);clearTimeout(p.timer);
    m.error?p.reject(new SafeFailure(`rpc-${p.method}`,undefined,m.error.data?.errno??m.error.data?.error)):p.resolve(m.result);
  };
  try {
    await new Promise((resolvePromise,reject)=>{ws.onopen=resolvePromise;ws.onerror=()=>reject(new SafeFailure('nas-tls'));});
    if(await call('auth.login_with_api_key',[key.toString('utf8')])!==true)fail('nas-auth');
  }catch(e){ws.close();throw e;}finally{key.fill(0);}
  return {call,close:()=>ws.close()};
}

async function appState(client) {
  const [app]=await client.call('app.query',[[['id','=',APP]],{extra:{retrieve_config:true}}]);
  if(!app||app.id!==APP||app.custom_app||app.metadata?.name!=='ix-app'||app.state!=='RUNNING')fail('app-preflight');
  const c=app.config;
  if(c.image?.repository!=='ghcr.io/javadevjt/dockerized-imap-sync'||c.run_as_custom_user||c.command?.length||c.entrypoint?.length)fail('app-config-preflight');
  return app;
}
export function makeValues(config,digest) {
  if(!/^sha256:[a-f0-9]{64}$/.test(digest))fail('image-reference');
  const envs=(config.envs??[]).filter(x=>!x.name.startsWith('OTEL_EXPORTER_OTLP')&&!['OTEL_SERVICE_NAME','OTEL_RESOURCE_ATTRIBUTES','OPENWOOF_INGEST_KEY','OPENWOOF_INGEST_KEY_FILE'].includes(x.name));
  envs.push(...Object.entries(TELEMETRY_ENV).map(([name,value])=>({name,value})));
  const storage=structuredClone(config.storage??[]);
  if(storage.some(x=>x.mount_path===CONTAINER_KEY))fail('existing-secret-mount');
  storage.push({type:'host_path',read_only:true,mount_path:CONTAINER_KEY,host_path_config:{acl_enable:false,path:HOST_KEY}});
  return {image:{...config.image,tag:`latest@${digest}`},envs,storage,privileged:false};
}
export function secureFile(stat,acl,empty=false) {
  return stat?.realpath===HOST_KEY&&stat.type==='FILE'&&stat.uid===0&&stat.gid===0&&(stat.mode&0o777)===0o600
    &&acl?.path===HOST_KEY&&acl.trivial===true&&(empty?stat.size===0:stat.size>0);
}
async function fileState(client) {
  return {stat:await client.call('filesystem.stat',[HOST_KEY]),acl:await client.call('filesystem.getacl',[HOST_KEY])};
}
async function job(client,id) {
  if(!Number.isSafeInteger(id))fail('job-id');
  for(let n=0;n<120;n++){
    const j=await client.call('core.get_jobs',[[['id','=',id]],{get:true}]);
    if(j.state==='SUCCESS')return {id,state:j.state};
    if(['FAILED','ABORTED'].includes(j.state))fail('job-failed-inspect-before-retry',id);
    await Bun.sleep(5000);
  }fail('job-timeout-inspect-before-retry',id);
}
export function multipart(bytes,boundary=randomBytes(18).toString('hex')) {
  const metadata=JSON.stringify({method:'filesystem.put',params:[HOST_KEY,{mode:null,append:false}]});
  const body=Buffer.concat([Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="data"\r\nContent-Type: application/json\r\n\r\n${metadata}\r\n`),
    Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="file"; filename="openwoof-ingest-key"\r\nContent-Type: application/octet-stream\r\n\r\n`),bytes,Buffer.from(`\r\n--${boundary}--\r\n`)]);
  return {body,boundary};
}
async function upload(client,bytes) {
  const token=await client.call('auth.generate_token',[120,{},true,true]);
  const url=new URL('/_upload/',ORIGIN);url.searchParams.set('auth_token',token);
  const {body,boundary}=multipart(bytes);
  try {
    const result=await new Promise((resolvePromise,reject)=>{
      const req=request(url,{method:'POST',ca:CA,servername:'localhost',rejectUnauthorized:true,agent:false,
        headers:{'content-type':`multipart/form-data; boundary=${boundary}`,'content-length':body.length}},res=>{
        let text='',size=0;if(res.statusCode<200||res.statusCode>=300){res.resume();reject(new SafeFailure('upload-http'));return;}
        res.on('data',b=>{size+=b.length;if(size>65536){res.destroy();reject(new SafeFailure('upload-response-size'));}else text+=b.toString();});
        res.on('end',()=>{try{resolvePromise(JSON.parse(text));}catch{reject(new SafeFailure('upload-response'));}});
        res.on('error',()=>reject(new SafeFailure('upload-response')));
      });req.setTimeout(30000,()=>req.destroy());req.on('error',()=>reject(new SafeFailure('upload-transport')));req.end(body);
    });
    return await job(client,result.job_id??result.jobId??result);
  }finally{body.fill(0);}
}
function saveReceipt(value) {
  mkdirSync(resolve(ROOT,'artifacts/.private-openwoof'),{recursive:true,mode:0o700});
  writeFileSync(RECEIPT,JSON.stringify(value,null,2)+'\n',{mode:0o600});
}
function receipt() {
  if(!existsSync(RECEIPT))fail('missing-preparation-receipt');
  const r=JSON.parse(readFileSync(RECEIPT,'utf8'));
  if(r.app!==APP||r.path!==HOST_KEY)fail('preparation-receipt');return r;
}
export function configHash(config) {
  const canonical=value=>Array.isArray(value)?value.map(canonical):value&&typeof value==='object'?Object.fromEntries(Object.keys(value).sort().map(k=>[k,canonical(value[k])])):value;
  const stable=Object.fromEntries(Object.entries(config).filter(([k])=>!['ix_context','ix_volumes'].includes(k)));
  return createHash('sha256').update(JSON.stringify(canonical(stable))).digest('hex');
}
async function settledApp(client) {
  for(let n=0;n<30;n++){
    try{return await appState(client);}catch(e){if(!(e instanceof SafeFailure)||e.phase!=='app-preflight')throw e;}
    await Bun.sleep(2000);
  }fail('app-not-running-after-job');
}
async function prepare(client) {
  await appState(client);
  const parent=await client.call('filesystem.stat',['/mnt/Lake']);
  const parentAcl=await client.call('filesystem.getacl',['/mnt/Lake']);
  if(parent.uid!==0||parent.type!=='DIRECTORY'||(parent.mode&0o022)!==0||parentAcl.trivial!==true)fail('secret-parent-security');
  if(existsSync(RECEIPT))fail('preparation-already-recorded-inspect-before-retry');
  try {await client.call('filesystem.stat',[DIRECTORY]);fail('existing-secret-directory');}
  catch(e){if(!(e instanceof SafeFailure)||e.errno!==2)throw e;}
  await client.call('filesystem.mkdir',[{path:DIRECTORY,options:{mode:'700'}}]);
  await job(client,await client.call('filesystem.setperm',[{path:DIRECTORY,uid:0,gid:0,mode:'700',options:{recursive:false,traverse:false,stripacl:true}}]));
  await upload(client,Buffer.alloc(0));
  await job(client,await client.call('filesystem.setperm',[{path:HOST_KEY,uid:0,gid:0,mode:'600',options:{recursive:false,traverse:false,stripacl:true}}]));
  const f=await fileState(client);if(!secureFile(f.stat,f.acl,true))fail('empty-file-security');
  const r={app:APP,path:HOST_KEY,phase:'prepared-empty',inode:f.stat.inode,device:f.stat.dev,preparedAt:new Date().toISOString()};
  saveReceipt(r);return r;
}
async function deliver(client) {
  await appState(client);const r=receipt(),f=await fileState(client);
  if(r.phase!=='prepared-empty'||!secureFile(f.stat,f.acl,true)||r.inode!==f.stat.inode)fail('prepared-file-drift');
  const key=await privateSecret('prompt-ingest-to-fd');
  try {await upload(client,key);}finally{key.fill(0);}
  const after=await fileState(client);if(!secureFile(after.stat,after.acl))fail('delivered-file-security');
  const next={...r,phase:'delivered',deliveredAt:new Date().toISOString()};saveReceipt(next);return next;
}
async function deploy(client,digest) {
  const r=receipt(),f=await fileState(client);if(r.phase!=='delivered'||!secureFile(f.stat,f.acl))fail('secret-not-ready');
  const app=await appState(client),before=app.config,values=makeValues(before,digest);
  const second=await appState(client);if(!isDeepStrictEqual(second.config,before))fail('app-config-drift');
  // Persist the attempt before mutation: a lost reply or failed postcondition
  // must never leave the operation eligible for a blind repeat.
  const attempt={...r,phase:'deploy-submitted',digest,expectedConfigHash:configHash({...before,...values}),attemptedAt:new Date().toISOString()};
  saveReceipt(attempt);
  const jobId=await client.call('app.update',[APP,{values}]);
  saveReceipt({...attempt,jobId});
  const result=await job(client,jobId);
  const after=await settledApp(client);
  for(const [k,v]of Object.entries(before))if(!['image','storage','envs','privileged','ix_context','ix_volumes'].includes(k)&&!isDeepStrictEqual(v,after.config[k]))fail('unrelated-config-drift',result.id);
  if(after.config.privileged!==false)fail('privileged-mode',result.id);
  if(!isDeepStrictEqual(values.image,after.config.image)||!isDeepStrictEqual(values.envs,after.config.envs))fail('telemetry-config-verification',result.id);
  const stored=after.config.storage??[];
  const previous=before.storage??[];
  if(stored.length!==previous.length+1||!isDeepStrictEqual(stored.slice(0,previous.length),previous))fail('storage-preservation',result.id);
  const secretMount=stored.find(x=>x.mount_path===CONTAINER_KEY);
  if(!secretMount||secretMount.type!=='host_path'||secretMount.read_only!==true||secretMount.host_path_config?.path!==HOST_KEY||secretMount.host_path_config?.acl_enable!==false)fail('secret-mount-verification',result.id);
  const containers=after.active_workloads?.container_details??[];
  const expected=`${values.image.repository}:${values.image.tag}`;
  if(containers.length!==1||containers[0].state!=='running'||containers[0].image!==expected)fail('runtime-image-verification',result.id);
  const mounts=after.active_workloads?.container_details?.flatMap(x=>x.volume_mounts??[])??[];
  saveReceipt({...r,phase:'deployed',digest,jobId:result.id,containerId:containers[0].id,deployedAt:new Date().toISOString()});
  return {app:APP,state:after.state,job:result,image:after.config.image,secretPath:HOST_KEY,mounts,privileged:after.config.privileged,configurationPreservedExceptTelemetryImageAndPrivilegeRemoval:true};
}
async function reconcileDeploy(client) {
  const r=receipt();if(r.phase!=='deploy-submitted'||!Number.isSafeInteger(r.jobId))fail('no-submitted-job');
  const result=await job(client,r.jobId),app=await settledApp(client),c=app.config;
  if(c.image?.tag!==`latest@${r.digest}`||c.privileged!==false)fail('reconcile-image-privilege');
  for(const [name,value] of Object.entries(TELEMETRY_ENV))if(c.envs.filter(x=>x.name===name&&x.value===value).length!==1)fail('reconcile-telemetry-env');
  const f=await fileState(client);if(!secureFile(f.stat,f.acl))fail('reconcile-secret-file');
  const mounts=c.storage??[];
  if(mounts.length!==1||mounts[0].read_only!==true||mounts[0].mount_path!==CONTAINER_KEY||mounts[0].host_path_config?.path!==HOST_KEY)fail('reconcile-storage');
  if(r.expectedConfigHash&&configHash(c)!==r.expectedConfigHash)fail('reconcile-config-fingerprint');
  const containers=app.active_workloads?.container_details??[];
  if(containers.length!==1||containers[0].state!=='running'||containers[0].image!==`ghcr.io/javadevjt/dockerized-imap-sync:latest@${r.digest}`)fail('reconcile-runtime-image');
  saveReceipt({...r,phase:'deployed',containerId:containers[0].id,reconciledAt:new Date().toISOString()});
  return {app:APP,state:app.state,job:result,digest:r.digest,containerId:containers[0].id,privileged:false,secretReadOnlyConfigured:true,
    completeConfigFingerprintVerified:!!r.expectedConfigHash,reconciledWithoutMutation:true};
}
async function deployImage(client,digest) {
  if(!/^sha256:[a-f0-9]{64}$/.test(digest))fail('image-reference');
  const r=receipt();if(r.phase!=='deployed')fail('previous-deployment-not-verified');
  const app=await appState(client),before=app.config;
  if(before.image.tag!==`latest@${r.digest}`)fail('previous-image-drift');
  const f=await fileState(client);if(!secureFile(f.stat,f.acl))fail('secret-not-ready');
  const image={...before.image,tag:`latest@${digest}`};
  const second=await appState(client);if(!isDeepStrictEqual(second.config,before))fail('app-config-drift');
  const {validation,validatedAt,jobId:previousJobId,...previous}=r;
  const attempt={...previous,previousJobId,previousDigest:r.digest,previousValidation:validation,previousValidatedAt:validatedAt,
    phase:'deploy-submitted',digest,expectedConfigHash:configHash({...before,image}),attemptedAt:new Date().toISOString()};
  saveReceipt(attempt);
  const jobId=await client.call('app.update',[APP,{values:{image}}]);saveReceipt({...attempt,jobId});
  await job(client,jobId);
  return await reconcileDeploy(client);
}

export function parseRuntimeOutput(output) {
  const records=[];
  for(const line of output.replace(/\x1b\[[0-9;?]*[A-Za-z]/g,'').split(/[\r\n]+/)) {
    const start=line.indexOf('{');if(start<0)continue;
    try {const value=JSON.parse(line.slice(start));if(['openwoof.runtime_check','openwoof.validation_receipt'].includes(value.event))records.push(value);}catch{}
  }
  const runtime=records.find(x=>x.event==='openwoof.runtime_check');
  const validation=records.find(x=>x.event==='openwoof.validation_receipt');
  const exit=output.match(/OPENWOOF_VALIDATE_EXIT=(\d+);RUNTIME_EXIT=(\d+)/);
  if(!runtime||!validation||!exit)fail('runtime-receipt-incomplete');
  const exporterNotices=[...new Set([...output.matchAll(/telemetry exporter reported an issue signal=(traces|metrics|logs)/g)].map(x=>x[1]))];
  return {runtime,validation,validationExit:Number(exit[1]),runtimeExit:Number(exit[2]),exporterNotices};
}
async function validateRuntime(client) {
  const r=receipt();if(r.phase!=='deployed')fail('deployment-not-recorded');
  const app=await appState(client),containers=app.active_workloads?.container_details??[];
  if(containers.length!==1||containers[0].id!==r.containerId||containers[0].image!==`ghcr.io/javadevjt/dockerized-imap-sync:latest@${r.digest}`)fail('runtime-validation-image-drift');
  const token=await client.call('auth.generate_token',[120,{},true,true]);
  // Official version-matched TrueNAS app console, never a host shell or Docker socket.
  const script=readFileSync(resolve(import.meta.dir,'runtime-check.py'),'utf8');
  const command=`stty -echo\npython3 - <<'OPENWOOF_RUNTIME_CHECK'\n${script}\nOPENWOOF_RUNTIME_CHECK\nruntime_status=$?\npython3 /opt/telemetry/runner.py --validate\nvalidation_status=$?\nprintf '\\nOPENWOOF_VALIDATE_EXIT=%s;RUNTIME_EXIT=%s\\n' "$validation_status" "$runtime_status"\nexit\n`;
  const ws=new WebSocket('wss://192.168.0.2:8443/websocket/shell',{tls:TLS});
  ws.binaryType='arraybuffer';
  const output=await new Promise((resolvePromise,reject)=>{
    let text='',finished=false,authenticated=false;
    const finish=(error)=>{if(finished)return;finished=true;clearTimeout(timer);try{ws.close();}catch{}error?reject(new SafeFailure(error)):resolvePromise(text);};
    const timer=setTimeout(()=>finish('runtime-validation-timeout'),90000);
    ws.onopen=()=>ws.send(JSON.stringify({token,options:{app_name:APP,container_id:containers[0].id,command:'/bin/sh'}}));
    ws.onmessage=e=>{
      if(typeof e.data==='string') {
        try {const m=JSON.parse(e.data);if(m.msg==='failed'){finish('runtime-console-auth');return;}if(m.msg==='connected'){authenticated=true;ws.send(Buffer.from(command));return;}}catch{}
      }else if(authenticated){text+=Buffer.from(e.data).toString('utf8');if(text.length>65536)finish('runtime-output-size');}
    };
    ws.onerror=()=>finish('runtime-console-transport');ws.onclose=()=>finish();
  });
  const result=parseRuntimeOutput(output);
  const after=await appState(client);
  if(after.active_workloads?.container_details?.[0]?.id!==r.containerId)fail('runtime-validation-restarted');
  saveReceipt({...r,validation:result,validatedAt:new Date().toISOString()});
  // Return only curated nonsecret JSON; arbitrary PTY output is never printed.
  return {app:APP,digest:r.digest,...result};
}
if(import.meta.main){
  const mode=process.argv[2];let client;
  try {
    if(!['inspect','inspect-parents','inspect-secrets','prepare','prompt-and-deliver','deploy','deploy-image','reconcile-deploy','validate-runtime'].includes(mode))fail('usage');
    client=await connect();let result;
    if(mode==='inspect'){const a=await appState(client);result={app:a.id,state:a.state,image:a.config.image,environmentNames:a.config.envs.map(x=>x.name),storage:a.config.storage};}
    if(mode==='inspect-secrets'){result=[];for(const path of [DIRECTORY,HOST_KEY]){try{const s=await client.call('filesystem.stat',[path]);result.push({path,exists:true,uid:s.uid,gid:s.gid,mode:s.mode,size:s.size,type:s.type,inode:s.inode});}catch(e){result.push({path,exists:false,phase:e.phase,errno:e.errno});}}}
    if(mode==='inspect-parents'){result=[];for(const path of ['/mnt/Lake','/mnt/Lake/Data']){const s=await client.call('filesystem.stat',[path]);const a=await client.call('filesystem.getacl',[path]);result.push({path,uid:s.uid,gid:s.gid,mode:s.mode,type:s.type,trivial:a.trivial,aclType:a.acltype});}}
    if(mode==='prepare')result=await prepare(client);
    if(mode==='prompt-and-deliver')result=await deliver(client);
    if(mode==='deploy')result=await deploy(client,process.argv[3]);
    if(mode==='deploy-image')result=await deployImage(client,process.argv[3]);
    if(mode==='reconcile-deploy')result=await reconcileDeploy(client);
    if(mode==='validate-runtime'){result=await validateRuntime(client);if(result.runtimeExit!==0||result.validationExit!==0||result.validation.outcome!=='success'||result.exporterNotices.length)process.exitCode=1;}
    console.log(JSON.stringify(result));
  }catch(e){console.error(JSON.stringify({phase:e instanceof SafeFailure?e.phase:'internal',jobId:e.jobId??null,errno:e.errno??null}));process.exitCode=1;}
  finally{client?.close();}
}
