const $ = id => document.getElementById(id);
let snapshot = {targets: [], jobs: []}, selected = localStorage.getItem('cloudshield.target') || '', busy = false;
const labels = {'judge':'Judge checks','repair':'AI repair','monitor':'Monitor & heal','watch':'Saved source check','plant':'Plant 5 breaks','fault':'Fault experiment','load':'JMeter experiment','prepare-lab':'Prepare managed lab','start-lab':'Start managed lab','aws-observe':'AWS observation','aws-recover':'AWS recovery','aws-fault':'AWS fault injection'};
const titles = {overview:'Control center',healing:'Self-healing',faults:'Fault laboratory',cloud:'Cloud connections',history:'Run history'};
function notice(message) { $('notice').textContent=message; $('notice').hidden=false; }
async function api(path, body) {
  const response=await fetch(path, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-CloudShield':'local'},body:JSON.stringify(body)});
  const result=await response.json(); if (!response.ok) throw new Error(result.error || 'Request failed'); return result;
}
function element(tag, text, className) { const node=document.createElement(tag); if(text!==undefined)node.textContent=text; if(className)node.className=className; return node; }
function target() { return snapshot.targets.find(t=>t.id===selected); }
function displayTime(value) { return value ? new Date(value).toLocaleString([], {month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}) : 'Queued'; }
function outcome(job) { const step=job.progress?.attempts?.at(-1);return (job.status==='running'?(step?`Attempt ${step.number}: ${step.stage}`:job.progress?.stage):null) || job.result?.status || job.result?.overall || job.result?.verdict || (job.exit_code===0?'Finished':job.detail || '—'); }
function setView(view) { document.querySelectorAll('.view').forEach(n=>n.hidden=n.id!==`${view}-view`); document.querySelectorAll('nav button').forEach(n=>n.classList.toggle('active',n.dataset.view===view)); $('page-title').textContent=titles[view]; }
async function refresh() {
  if (busy) return; busy=true;
  try {
    snapshot=await api('/api/state');
    if (!snapshot.targets.some(t=>t.id===selected)) selected=snapshot.targets[0]?.id || '';
    const dropdown=$('target-select'); dropdown.replaceChildren();
    if (!snapshot.targets.length) dropdown.append(element('option','Register your first application'));
    snapshot.targets.forEach(t=>{ const option=element('option',t.name);option.value=t.id;dropdown.append(option); }); dropdown.value=selected;
    $('worker-badge').textContent=snapshot.worker_online?'● WORKER ONLINE':'○ WORKER OFFLINE'; $('worker-badge').classList.toggle('good',snapshot.worker_online);
    const t=target(), jobs=snapshot.jobs.filter(j=>j.target_id===selected), latest=jobs.find(j=>j.result && ['judge','monitor','aws-observe','aws-recover'].includes(j.action)), active=jobs.find(j=>['queued','running','blocked'].includes(j.status));
    $('adapter-label').textContent=t?`${t.adapter} / ${t.name}`:'No target selected';
    $('health').textContent=latest?outcome(latest).replaceAll('_',' '):'Not checked';
    $('last-check').textContent=latest?`${labels[latest.action] || latest.action} · ${displayTime(latest.finished)}`:'Run a Judge check to begin';
    $('monitor-state').textContent=t?.monitoring?'On':'Off';
    $('monitor-detail').textContent=t?.monitoring?`Every ${t.policy.interval}s after a completed check`:'No scheduled observations';
    $('recovery-count').textContent=jobs.filter(j=>j.result?.status==='recovered').length;
    $('repair-mode').textContent=t?.policy.managed_recovery?'Auto deploy':t?.policy.ai?'Reviewed AI':'Observe';
    $('policy-detail').textContent=t?.adapter==='aws-ecs'?'AWS manifest controls restoration':t?.policy.managed_recovery?'Managed disposable target only':'Verified proposals retain approval boundary';
    $('monitor-toggle').checked=!!t?.monitoring; $('watch-toggle').checked=!!t?.watching;
    $('monitor-toggle').disabled=!t; $('watch-toggle').disabled=!t || t.adapter!=='docker-compose';
    document.querySelectorAll('[data-action]').forEach(button=>{ const aws=button.dataset.action.startsWith('aws-');button.disabled=!t || !!active || aws!==(t.adapter==='aws-ecs') || (button.dataset.action==='repair' && !t?.policy.ai); });
    $('jobs').replaceChildren(); $('empty').hidden=!!jobs.length; $('job-count').textContent=jobs.length;
    jobs.forEach(job=>{ const row=element('tr');row.append(element('td',labels[job.action] || job.action));const status=element('td');status.append(element('span',job.status,`status ${job.status}`));row.append(status,element('td',displayTime(job.started || job.created)),element('td',outcome(job)));
      const actions=element('td'), details=element('button','Details','quiet');details.addEventListener('click',()=>showReport(job.result || job.progress || {status:job.status,detail:job.status==='running'?'The background runner is executing this operation. Verification and cleanup finish before the final report is saved.':job.detail}));actions.append(details);
      if(job.status==='queued'){const cancel=element('button','Cancel','quiet');cancel.addEventListener('click',()=>perform('/api/cancel',{job_id:job.id}));actions.append(cancel);}row.append(actions);$('jobs').append(row); });
    const caps=snapshot.capabilities; $('capabilities').replaceChildren();
    [['Python '+caps.python,true],['Docker CLI',caps.docker_cli],['Gemini configured',caps.gemini_configured],['AWS CLI',caps.aws_cli],['Java',caps.java],['JMeter home',caps.jmeter]].forEach(([name,ready])=>$('capabilities').append(element('span',`${ready?'●':'○'} ${name}`,`capability ${ready?'ready':''}`)));
    $('aws-status').textContent=snapshot.targets.some(t=>t.adapter==='aws-ecs')?'REGISTERED · RUN OBSERVATION':'ACCOUNT CONNECTION REQUIRED';
    $('poll-status').textContent=active?`${labels[active.action] || active.action} ${active.status} · final evidence pending`:'Updates every 2 seconds';
  } catch(error) {notice(error.message);$('worker-badge').textContent='CONNECTION LOST';} finally {busy=false;}
}
async function perform(path, body) {try {const result=await api(path,body);notice(result.detail || (result.job_id?'Operation queued. The worker will save its final evidence.':'Updated.'));await refresh();}catch(error){notice(error.message);}}
function references(value, result=new Set()) {if(Array.isArray(value))value.forEach(v=>references(v,result));else if(value && typeof value==='object')Object.entries(value).forEach(([key,v])=>{if(typeof v==='string' && (key==='report' || key.endsWith('_report') || key==='recovery_judge') && v.endsWith('.json'))result.add(v);else if(typeof v==='object')references(v,result);});return result;}
function showReport(report) {
  $('report-json').textContent=JSON.stringify(report,null,2);$('report-summary').replaceChildren(element('p',`Outcome: ${report.status || report.overall || report.verdict || 'Evidence'}`));
  references(report).forEach(path=>{const button=element('button',path.split(/[\\/]/).at(-1),'report-link');button.title=path;button.addEventListener('click',async()=>{try {showReport(await api('/api/report?path='+encodeURIComponent(path)));}catch(error){notice(error.message);}});$('report-summary').append(button);});
  $('report-checks').replaceChildren();const checks=report.checks || report.scenarios || report.attempts || [];
  if(checks.length){const table=element('table'),head=element('tr');['Check / attempt','Status','Detail'].forEach(title=>head.append(element('th',title)));table.append(head);checks.forEach(check=>{const row=element('tr');row.append(element('td',check.id || check.scenario || `Attempt ${check.number}`),element('td',check.status || check.outcome || check.stage || '—'),element('td',check.detail || check.cleanup || ''));table.append(row);});$('report-checks').append(table);}
  if(report.detail)$('report-summary').append(element('p',report.detail));if(!$('report-dialog').open)$('report-dialog').showModal();
}
document.querySelectorAll('nav button').forEach(button=>button.addEventListener('click',()=>setView(button.dataset.view)));
document.querySelectorAll('[data-action]').forEach(button=>button.addEventListener('click',()=>{const action=button.dataset.action;if(action==='aws-fault' && !confirm('Inject DB_HOST drift into the registered disposable AWS sandbox?'))return;const options=action==='fault'?{scenario:$('fault-select').value}:action==='load'?{mode:$('load-select').value}:{};perform('/api/jobs',{target_id:selected,action,options});}));
$('target-select').addEventListener('change',event=>{selected=event.target.value;localStorage.setItem('cloudshield.target',selected);refresh();});
$('refresh').addEventListener('click',refresh);
$('monitor-toggle').addEventListener('change',event=>perform('/api/toggle',{target_id:selected,kind:'monitoring',enabled:event.target.checked}));
$('watch-toggle').addEventListener('change',event=>perform('/api/toggle',{target_id:selected,kind:'watching',enabled:event.target.checked}));
$('worker-start').addEventListener('click',()=>perform('/api/worker',{}));$('worker-stop').addEventListener('click',()=>perform('/api/pause',{}));
$('connect-open').addEventListener('click',()=>{const t=target();$('manifest-select').replaceChildren();snapshot.manifests.forEach(m=>{const option=element('option',m.manifest+' · '+m.adapter);option.value=m.manifest;$('manifest-select').append(option);});if(t){const option=Array.from($('manifest-select').options).find(o=>t.manifest.endsWith(o.value.replaceAll('/', '\\')) || t.manifest.endsWith(o.value));if(option)$('manifest-select').value=option.value;}$('target-name').value=t?.name || '';$('interval').value=t?.policy.interval || 30;$('ai').checked=!!t?.policy.ai;$('recover').checked=!!t?.policy.recover_services;$('managed').checked=!!t?.policy.managed_recovery;$('performance').value=t?.policy.performance || '';$('allowed-paths').value=(t?.policy.allowed_paths || snapshot.source_paths).join('\n');$('connect-dialog').showModal();});
$('connect-form').addEventListener('submit',async event=>{event.preventDefault();try{const result=await api('/api/targets',{name:$('target-name').value,manifest:$('manifest-select').value,interval:Number($('interval').value),ai:$('ai').checked,recover_services:$('recover').checked,managed_recovery:$('managed').checked,performance:$('performance').value,allowed_paths:$('allowed-paths').value.split('\n').map(v=>v.trim()).filter(Boolean)});selected=result.target_id;localStorage.setItem('cloudshield.target',selected);$('connect-dialog').close();notice('Application connected. Run a check or enable continuous monitoring.');await refresh();}catch(error){notice(error.message);}});
$('connect-close').addEventListener('click',()=>$('connect-dialog').close());$('report-close').addEventListener('click',()=>$('report-dialog').close());
refresh();setInterval(refresh,2000);
