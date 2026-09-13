'use strict';
const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name=session-token]').content;
let busy = false;
const companyNumbers=[], companyNames=new Map(), pendingNames=new Map();
async function api(path, body) {
  const response = await fetch(path,{method:body ? 'POST':'GET',headers:{'X-Session':token,...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{})});
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function inputChanged() {
  const collecting=['collect','full'].includes($('mode').value);
  const single=$('inputMode').value==='single', list=$('inputMode').value==='list';
  $('singleGroup').hidden=!single;$('fileGroup').hidden=single||list;
  $('listGroup').hidden=!list;
  $('listNumber').disabled=!(collecting&&list);
  $('registrationNumber').required=collecting&&single;
  $('registrationNumber').disabled=!(collecting&&single);
}
function renderCompanies(){
  $('companyList').replaceChildren(...companyNumbers.map(number=>{
    const row=document.createElement('li'),label=document.createElement('span'),remove=document.createElement('button');
    label.textContent=number+(companyNames.get(number)?.name?' — '+companyNames.get(number).name:'');remove.type='button';remove.textContent='Remove';remove.setAttribute('aria-label','Remove '+number);
    remove.addEventListener('click',()=>{companyNumbers.splice(companyNumbers.indexOf(number),1);renderCompanies();});
    row.append(label,remove);return row;
  }));
  $('listCount').textContent=companyNumbers.length?companyNumbers.length+' companies added.':'No companies added.';
}
function addCompany(){
  const number=$('listNumber').value.trim();$('listError').textContent='';
  if(!/^[0-9]{11}$/.test(number)){$('listError').textContent='Enter exactly 11 digits.';return;}
  if(companyNumbers.includes(number)){$('listError').textContent='This company is already in the list.';return;}
  if(companyNumbers.length>=100){$('listError').textContent='Maximum 100 companies per list.';return;}
  companyNumbers.push(number);lookupName(number).then(renderCompanies);$('listNumber').value='';$('listName').textContent='';renderCompanies();$('listNumber').focus();
}
function lookupName(number){
  if(companyNames.has(number))return Promise.resolve(companyNames.get(number));
  if(pendingNames.has(number))return pendingNames.get(number);
  const pending=api('/api/company-name',{registration_number:number}).then(result=>{companyNames.set(number,result);return result;})
    .catch(()=>({name:null,status:'unavailable'})).finally(()=>pendingNames.delete(number));
  pendingNames.set(number,pending);return pending;
}
for(const [input,output] of [['registrationNumber','singleName'],['listNumber','listName']]){
  $(input).addEventListener('input',async()=>{
    const number=$(input).value.trim();$(output).textContent='';
    if(!/^[0-9]{11}$/.test(number))return;
    $(output).textContent='Looking up saved company name…';
    const result=await lookupName(number);
    if($(input).value.trim()!==number)return;
    $(output).textContent=result.name?number+' — '+result.name:(result.status==='unavailable'?'Name lookup unavailable. You can still add this company.':'No saved name. It will be retrieved during collection.');
  });
}
$('toggleCompanies').addEventListener('click',()=>{
  const hidden=!$('companyList').hidden;$('companyList').hidden=hidden;
  $('toggleCompanies').setAttribute('aria-expanded',String(!hidden));
  $('toggleCompanies').textContent=hidden?'Show list':'Hide list';
});
$('addCompany').addEventListener('click',addCompany);
$('listNumber').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();addCompany();}});
$('inputMode').addEventListener('change',inputChanged);
function modeChanged() {
  const mode=$('mode').value;
  $('inputGroup').hidden=!['collect','full'].includes(mode);
  $('monitorGroup').hidden=mode!=='monitor';
  $('paidGroup').hidden=!['monitor','full'].includes(mode);
  $('start').textContent=mode==='full'?'Create report':'Check for changes';
  inputChanged();
}
$('mode').addEventListener('change',modeChanged);modeChanged();
$('upload').addEventListener('change',async()=>{
  const file=$('upload').files[0];if(!file)return;
  if(file.size>5*1024*1024){$('uploadResult').textContent='File exceeds 5 MB.';return;}
  try {
    const content=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
    const result=await api('/api/upload',{name:file.name,content});
    await refresh();$('input').value=result.name;$('uploadResult').textContent=result.companies+' companies validated and saved.';
  }catch(error){$('uploadResult').textContent=error.message;}
});
$('launchForm').addEventListener('submit',async event=>{
  event.preventDefault();$('error').textContent='';
  if(busy)return;
  try {
    if(['collect','full'].includes($('mode').value)&&$('inputMode').value==='list'){
      if($('listNumber').value.trim())throw new Error('Click Add company to include the entered number, or clear it.');
      if(!companyNumbers.length)throw new Error('Add at least one company to the list.');
    }
    $('start').disabled=true;
    await api('/api/start',{mode:$('mode').value,input:$('input').value,input_mode:$('inputMode').value,registration_number:$('registrationNumber').value.trim(),registration_numbers:companyNumbers,baseline:$('baseline').value,excel:true});
    await refresh();
  }catch(error){$('error').textContent=error.message;}
  finally{$('start').disabled=busy;}
});
async function refresh(){
  try{
    const data=await api('/api/status');
    const selected=$('input').value;
    if(JSON.stringify([...$('input').options].map(o=>o.value))!==JSON.stringify(data.inputs)){
      $('input').replaceChildren(...data.inputs.map(name=>new Option(name,name)));
      if(data.inputs.includes(selected))$('input').value=selected;
    }
    if(data.summary){
      $('companyCount').textContent=data.summary.companies;$('riskCount').textContent=data.summary.not_recommended;$('version').textContent=data.summary.methodology;
      $('assessmentDate').textContent='Generated '+new Date(data.summary.date).toLocaleString();
    }
    const labels={'latest.html':'Open full report','latest.csv':'Download compact CSV','latest.xlsx':'Download Excel workbook'};
    $('artifacts').replaceChildren(...data.artifacts.map(name=>{const a=document.createElement('a');a.href='/reports/'+name;a.target='_blank';a.rel='noopener';a.textContent=labels[name];return a;}));
    const job=data.job;busy=job?.status==='RUNNING';$('controls').disabled=busy;$('start').disabled=busy;
    $('jobStatus').textContent=job?.status||'Idle';$('jobStatus').dataset.state=job?.status||'IDLE';
    $('stage').textContent=job?job.stage+' · '+job.started_at:'Ready to start.';
    if(data.log && $('log').textContent!==data.log){$('log').textContent=data.log;if($('follow').checked)$('log').scrollTop=$('log').scrollHeight;}
  }catch(error){$('stage').textContent='Launcher connection unavailable: '+error.message;}
}
refresh();setInterval(refresh,2000);

let savedReports=[];
function showBaseline(){
  const report=savedReports.find(r=>r.id===$('baseline').value);
  $('baselineReport').hidden=!report;
  if(report)$('baselineReport').href='/reports/report-'+report.id+'.html';
  $('baselineInfo').textContent=report?report.names:'No saved reports with the current scoring rules.';
}
$('baseline').addEventListener('change',showBaseline);
async function loadReports(){
  try{
    savedReports=await api('/api/reports');const previous=$('baseline').value;
    $('baseline').replaceChildren(...savedReports.map(r=>new Option(new Date(r.date).toLocaleString()+' · '+r.companies+' companies · '+r.id.slice(0,8),r.id)));
    if(savedReports.some(r=>r.id===previous))$('baseline').value=previous;
    showBaseline();
  }catch(error){$('baselineInfo').textContent='Report history unavailable: '+error.message;}
}
loadReports();setInterval(loadReports,15000);
