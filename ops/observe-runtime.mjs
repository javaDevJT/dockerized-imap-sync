#!/usr/bin/env bun
// Bounded native app-log observation; raw mailbox/application logs stay private.
import {connect,APP} from './deploy-openwoof.mjs';
const counts={total:0,nonempty:0,detailed:0,exporterWarnings:0,configuredCredentialMatches:0};
const commands=new Set('APPEND|AUTHENTICATE|CAPABILITY|CHECK|CLOSE|COMPRESS|CONNECT|COPY|CREATE|DELETE|DELETEACL|ENABLE|EXAMINE|EXPUNGE|FETCH|GETACL|GETQUOTA|GETQUOTAROOT|ID|IDLE|LIST|LISTRIGHTS|LOGIN|LOGOUT|LSUB|MOVE|MYRIGHTS|NAMESPACE|NOOP|NOTIFY|OTHER|RENAME|SEARCH|SELECT|SETACL|SETQUOTA|SORT|STARTTLS|STATUS|STORE|SUBSCRIBE|THREAD|UID COPY|UID EXPUNGE|UID FETCH|UID MOVE|UID SEARCH|UID SORT|UID STORE|UID THREAD|UNSELECT|UNSUBSCRIBE|XLIST'.split('|'));
let allowedHosts=new Set();
let protectedValues=[];
const summaries=[];let subscription;
const client=await connect(message=>{
  if(message.method!=='collection_update'||!message.params?.collection?.startsWith('app.container_log_follow:'))return;
  const text=message.params.fields?.data;if(typeof text!=='string')return;
  for(const line of text.split(/[\r\n]+/)){
    counts.total++;if(line.trim())counts.nonempty++;
    if(protectedValues.some(value=>line.includes(value)))counts.configuredCredentialMatches++;
    if(/imapsync|Host[12]|Folder|messages|transferred|[0-9]+\s+msgs/i.test(line))counts.detailed++;
    if(line.includes('telemetry exporter reported an issue'))counts.exporterWarnings++;
    const start=line.indexOf('{');if(start<0)continue;
    try{const value=JSON.parse(line.slice(start));
      if(value.event==='imap.telemetry.summary'){
        const safe={event:value.event};
        for(const k of ['trace_id','span_id'])if(typeof value[k]==='string'&&/^[a-f0-9]{16,32}$/.test(value[k]))safe[k]=value[k];
        for(const k of ['request_spans','request_errors','stdout_lines','stderr_lines','malformed_events','export_failures','partial_successes','command_exit_code','server_addresses_omitted']){
          const v=value[k];if(typeof v==='number'&&Number.isSafeInteger(v)&&v>=0)safe[k]=v;
        }
        safe.command_counts=Object.fromEntries(Object.entries(value.command_counts??{}).filter(([k,v])=>commands.has(k)&&Number.isSafeInteger(v)&&v>=0));
        safe.server_roles=Object.fromEntries(Object.entries(value.server_roles??{}).filter(([k,v])=>['source','destination','unknown'].includes(k)&&Number.isSafeInteger(v)&&v>=0));
        safe.server_addresses=(value.server_addresses??[]).filter(x=>typeof x==='string'&&allowedHosts.has(x.toLowerCase()));
        for(const k of ['export_success','flush_success'])if(typeof value[k]==='boolean')safe[k]=value[k];
        if(['success','failure','interrupted'].includes(value.sync_outcome))safe.sync_outcome=value.sync_outcome;
        safe.exports=Object.fromEntries(Object.entries(value.exports??{}).filter(([k])=>['traces','metrics','logs'].includes(k)).map(([k,v])=>[k,{
          status:['success','failure','partial','missing'].includes(v.status)?v.status:'unknown',
          flush:['success','failure','not_run'].includes(v.flush)?v.flush:'unknown',
          failures:Number.isSafeInteger(v.failures)?v.failures:null,partial_successes:Number.isSafeInteger(v.partial_successes)?v.partial_successes:null,
        }]));
        summaries.push(safe);
      }
    }catch{}
  }
});
try{
  const [app]=await client.call('app.query',[[['id','=',APP]],{extra:{retrieve_config:true}}]);
  const containers=app?.active_workloads?.container_details??[];
  if(app?.state!=='RUNNING'||containers.length!==1)throw Error();
  allowedHosts=new Set((app.config?.envs??[]).filter(x=>['IMAP1_HOST','IMAP2_HOST'].includes(x.name)).map(x=>x.value.toLowerCase()));
  protectedValues=(app.config?.envs??[]).filter(x=>['IMAP1_USER','IMAP1_PASSWORD','IMAP2_USER','IMAP2_PASSWORD'].includes(x.name)).map(x=>x.value).filter(x=>typeof x==='string'&&x.length>0);
  subscription=await client.call('core.subscribe',['app.container_log_follow:'+JSON.stringify({app_name:APP,container_id:containers[0].id,tail_lines:500})]);
  await Bun.sleep(10000);
  console.log(JSON.stringify({app:APP,containerId:containers[0].id,at:new Date().toISOString(),counts,summaries:summaries.slice(-5)}));
}catch{console.error(JSON.stringify({app:APP,phase:'native-log-observation'}));process.exitCode=1;}
finally{client.close();}
