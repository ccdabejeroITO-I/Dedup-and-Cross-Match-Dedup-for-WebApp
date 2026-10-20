"use strict";
const $ = id => document.getElementById(id);
const semantic = {none:"Keep only",full_name:"Full name",first:"First name",middle:"Middle name",last:"Last name",suffix:"Suffix",birthdate:"Birthdate",birth_month:"Birth month",birth_day:"Birth day",birth_year:"Birth year",exact:"Other exact data",person_id:"Beneficiary / person ID",death_status:"Death status",date_of_death:"Date of death",case_status:"Beneficiary status",payment_status:"Payment status",date_accomplished:"Date accomplished",paid_at:"Paid at",payout_site:"Payout site",province:"Province",municipality:"Municipality",barangay:"Barangay"};
const coreKeys = ["full_name","first","middle","last","suffix","birthdate","birth_month","birth_day","birth_year"];
const exactKeys = new Set(["exact","person_id"]);
// v6.32.3: Custom-only builder; confidence thresholds use stable automatic defaults.
// Legacy saved built-in toggles are ignored rather than becoming invisible rules.
const conditionState = {rules:[],profiles:[],mode:'file-a',byMode:{'file-a':[],cross:[]}};
const state = {session:null,files:{},rows:{},busy:false,timer:null,job:null};
const storageKey = "crossmatch:v631:" + document.body.dataset.username;
function showMessage(message, error=true) { $("message").textContent=message; $("message").className="notice " + (error?"error":"success"); }
async function api(url, options={}) {
  const response = await fetch(url, {credentials:"same-origin",...options});
  if(response.status===401){location.href="/login";throw Error("Please sign in again.");}
  // Reverse proxies may return HTML for file-size or upstream errors.
  // Handle those cases without hiding the actual upload failure.
  const contentType=response.headers.get("content-type")||"";
  let data;
  if(contentType.includes("application/json")){
    try{data=await response.json();}catch(_){throw Error(`Server returned an invalid response (HTTP ${response.status}).`);}
  }else{
    const text=await response.text();
    if(!response.ok)throw Error(`Server rejected the request (HTTP ${response.status}). Check the upload limit and server logs.`);
    throw Error(`Unexpected server response (HTTP ${response.status}): ${text.slice(0,90)}`);
  }
  if(!response.ok) throw Error(typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail || data));
  return data;
}
function json(method, data){return {method,headers:{"Content-Type":"application/json"},body:JSON.stringify(data)};}
const FRONTEND_VERSION = "6.32.64";
function showRunError(message){
  showMessage(message);
  const status=$("run-readiness");
  if(status){
    status.textContent="Cannot start: "+message;
    status.classList.remove("is-ready");
    status.classList.add("is-error");
    status.scrollIntoView({block:"center",behavior:"smooth"});
  }
}
function checkUICompatibility(){
  const required=["workspace","mode","run","run-readiness","file-a","file-b","retry-a","retry-b","upload-state-a","upload-state-b","mapping-a","message"];
  const missing=required.filter(id=>!$(id));
  const pageVersion=document.body.dataset.uiVersion||"";
  if(missing.length || (pageVersion && pageVersion!==FRONTEND_VERSION)){
    const reason=missing.length?`Missing UI elements: ${missing.join(", ")}.`:`Page version ${pageVersion}, JavaScript version ${FRONTEND_VERSION}.`;
    showMessage("CrossMatch update incomplete. "+reason+" Reinstall ALL patch files and rebuild Docker.");
    return false;
  }
  return true;
}
function refreshRunReadiness(){
  const cross=$("mode").value==="cross";
  const ready=Boolean(state.session?.session_id && state.files.a && (!cross || state.files.b));
  $("run").disabled=state.busy || !ready;
  $("run-readiness").textContent=state.busy?"Please wait until the current action finishes.":
    !state.session?.session_id?"Session not ready. Refresh the page or select New session.":
    !state.files.a?"Upload File A successfully before processing.":
    cross&&!state.files.b?"File A ready. Upload File B to start Cross Match.":
    "Files ready. Review header mapping, then start processing.";
  $("run-readiness").classList.toggle("is-ready",ready);
  $("run-readiness").classList.remove("is-error");
}
function setBusy(value){state.busy=value;$("workspace").disabled=value;$("new-session").disabled=value;refreshRunReadiness();}
function setUploadStatus(side,status,message){
  const badge=$("upload-state-"+side);
  badge.textContent=message;badge.dataset.state=status;
  $("card-"+side).dataset.uploadState=status;
  $("retry-"+side).classList.toggle("hidden",status!=="error");
}
function refreshControlStatus(){
  const exhaustive=$('scan-preset').value==='exhaustive';
  const deep=$('scan-preset').value!=='quick';
  const fuzzy=$('fuzzy').checked;
  const swap=$('swap').checked;
  const csv=$('export-format').value==='csv_zip';
  const profile=$('profile-status');
  profile.textContent=exhaustive?'Combined recall':deep?'Comprehensive':'Quick';
  profile.className='status-pill '+(deep?'status-purple':'status-blue');
  for(const [key,on] of [['fuzzy',fuzzy],['swap',swap]]){
    const pill=$('status-'+key);
    pill.textContent=on?'ON':'OFF';
    pill.className='status-pill '+(on?'status-green':'status-gray');
    $('tile-'+key).classList.toggle('is-off',!on);
  }
  const output=$('status-export');
  output.textContent=csv?'CSV ZIP':'EXCEL';
  output.className='status-pill '+(csv?'status-blue':'status-amber');
  $('profile-help').textContent=exhaustive
    ?'Comprehensive + Maximum recall: checks every eligible pair using exact, fuzzy and optional swapped names. Different or missing DOB and suffix conflicts are labeled for manual verification. Cross Match requires A × B comparisons; within-file requires N × (N−1) / 2. Very large datasets may take hours or be impractical.'
    :deep
    ?'Comprehensive: checks the full v6.31 fuzzy paths. Dense similar-name datasets can take longer; no candidate cap applies.'
    :'Quick: checks exact names and birthdates with optional swaps. Spelling variations are not checked in this pass.';
}
function applyPreset(){
  $('fuzzy').checked=$('scan-preset').value!=='quick';
  refreshControlStatus();
}
function normalizeHeaderName(value){return String(value||'').toLowerCase().replace(/[^a-z0-9]+/g,'');}
function isMatchMeaning(key){return coreKeys.includes(key)||exactKeys.has(key);}
function applyMode(){
  const cross=$("mode").value==="cross";
  conditionState.byMode[conditionState.mode]=conditionState.rules;
  conditionState.mode=cross?'cross':'file-a';
  conditionState.rules=conditionState.byMode[conditionState.mode];
  $("card-b").classList.toggle("hidden",!cross);
  document.querySelector(".files").classList.toggle("cross",cross);
  $('mapping-scope').textContent=cross?'File A ↔ File B':'Within File A';
  $('settings-current').textContent=cross?'File A ↔ File B':'File A only';
  $('add-condition-title').textContent=cross?'+ Compare File A ↔ File B':'+ Check File A';
  $('add-condition-help').textContent=cross?'Compare two fields from an accepted A/B match.':'Check a field in File A against text, a number, or a date.';
  $('condition-start-help').textContent=cross
    ?'Cross Match: add a comparison between File A and its matched File B record.'
    :'File A only: add a check for a field in File A.';
  $('condition-scope-note').textContent='Conditions are kept separately for each processing mode. Only the conditions shown here run.';
  $('mapping-intro').textContent=cross
    ?'Auto-detect File A meanings and suggest similar File B headers. Confirm uncertain suggestions.'
    :'Auto-detect File A meanings and review similar header suggestions.';
  $('files-mode-help').textContent=cross?'Upload File A and File B, then map their corresponding headers.':'Upload File A and review its headers for within-file deduplication.';
  $('condition-records-help').textContent=cross?'An accepted File A ↔ File B pair.':'File A records only.';
  $('condition-check-help').textContent=cross?'Compare File A with File B; optionally add a File A value check.':'Check a value, number, date, or blank field in File A.';
  document.querySelector('.cross-rule-explainer').classList.toggle('hidden',!cross);
  const title=$("paired-header-title");
  if(title)title.textContent=cross?'Detected Header in File B':'Detected Header in File A';
  if(state.rows.a)renderMapping('a');
  renderRules();
  refreshRunReadiness();
}
// The semantic meaning is the single source of truth for matching; no hidden Use toggle.
// Unknown headers remain in all exports and may still be selected in Conditions.
function parseRows(info,side='a') {
  const m=info.mapping;
  return info.headers.map((header,i)=>{
    const det=(info.header_detection||[])[i]||{};
    const key=det.key || coreKeys.find(k=>m[k]===header) || ((m.exact_fields||[]).includes(header)?'person_id':(m.custom_meanings||{})[header]||'none');
    return {header,key,pairA:header,pairB:'',pairBManual:false,pairConfidence:0,pairMethod:'',
      confidence:det.confidence??0,band:det.band||'conflict',warning:det.warning||'',
      suggestion:det.suggestion||'',confirmed:det.confirmed??false,method:det.method||'',
      sampleValues:det.sample_values||[]};
  });
}
function emptyMapping(){return {...Object.fromEntries(coreKeys.map(k=>[k,''])),exact_fields:[],exact_review_fields:[],exact_dedup_fields:[],exact_ignored_fields:[],review_headers:[],ignored_headers:[],custom_meanings:{},review_fields:[],dedup_fields:[],ignored_fields:[]};}
function addMappedField(m,key,header){
  if(!header||key==='none')return;
  if(exactKeys.has(key)){
    m.exact_fields.push(header);
    if(key==='person_id')m.custom_meanings[header]=key;
    m.exact_dedup_fields.push(header);
  }else if(coreKeys.includes(key)){
    m[key]=header;m.dedup_fields.push(key);
  }else{
    m.custom_meanings[header]=key;
  }
}
function mapping(side){
  const m=emptyMapping(),cross=$('mode').value==='cross';
  if(side==='b'){
    if(!cross)return m;
    for(const row of (state.rows.a||[])){
      if(exactKeys.has(row.key)&&unifiedFieldModes.get(unifiedModeKey(row.header))==='ignore')continue;
      if(exactKeys.has(row.key)&&unifiedFieldModes.get(unifiedModeKey(row.header))==='evidence')continue;
      addMappedField(m,row.key,row.pairB);
    }
  }else{
    for(const row of (state.rows.a||[])){
      if(exactKeys.has(row.key)&&['ignore','evidence'].includes(unifiedFieldModes.get(unifiedModeKey(row.header))))continue;
      addMappedField(m,row.key,cross?row.header:(row.pairA||row.header));
    }
  }
  if(m.exact_dedup_fields.length)m.dedup_fields.push('exact_fields');
  // Pass explicitly reviewed semantic choices to the engine. The legacy
  // lexical repair stage must not erase correctly confirmed fuzzy typos.
  m.explicit_mapping=true;  // User-reviewed fields are authoritative; no backend auto-fill.
  m.confirmed_semantics={};
  for(const row of (state.rows.a||[])){
    if(!coreKeys.includes(row.key) || !row.confirmed)continue;
    const selected=side==='b'?row.pairB:(cross?row.header:(row.pairA||row.header));
    if(selected)m.confirmed_semantics[row.key]=selected;
  }
  return m;
}
function stringSimilarity(a,b){
  a=normalizeHeaderName(a);b=normalizeHeaderName(b);
  if(!a||!b)return 0;if(a===b)return 100;
  const prev=Array.from({length:b.length+1},(_,i)=>i);
  for(let i=1;i<=a.length;i++){
    let diag=prev[0];prev[0]=i;
    for(let k=1;k<=b.length;k++){
      const prior=prev[k];prev[k]=Math.min(prev[k]+1,prev[k-1]+1,diag+(a[i-1]===b[k-1]?0:1));diag=prior;
    }
  }
  return Math.round(100*(1-prev[b.length]/Math.max(a.length,b.length)));
}
// Meaning agreement is more reliable than spelling alone. Spelling matches
// below 85% or competing close alternatives require manual File B selection.
function findSuggestedPair(row,targetSide){
  const rows=state.rows[targetSide]||[];
  if(!rows.length)return {header:'',score:0,method:''};
  const sameMeaning=rows.filter(r=>row.key!=='none' && (r.key===row.key || (r.suggestion===row.key && r.confidence>=80)));
  const confirmed=sameMeaning.filter(r=>r.key===row.key&&r.confirmed);
  // For identity/DOB fields, only auto-pair verified File B meanings.
  // An uncertain spelling suggestion needs an explicit manual selection.
  const candidates=coreKeys.includes(row.key)?confirmed:(confirmed.length?confirmed:sameMeaning);
  if(candidates.length===1)return {header:candidates[0].header,score:candidates[0].key===row.key?100:candidates[0].confidence,method:'detected meaning'};
  if(candidates.length>1)return {header:'',score:0,method:'ambiguous meaning'};
  // Never auto-pair different identity meanings (e.g. Full Birthdate to Birth Day).
  if(coreKeys.includes(row.key))return {header:'',score:0,method:'no equivalent detected meaning; choose manually'};
  const ranked=rows.map(r=>({header:r.header,score:stringSimilarity(row.header,r.header)})).sort((a,b)=>b.score-a.score);
  const best=ranked[0],second=ranked[1];
  if(best && best.score>=85 && (!second||best.score-second.score>=8))return {...best,method:best.score===100?'identical header':'similar header'};
  return {header:'',score:best?.score||0,method:'needs review'};
}
function needsMeaningConfirmation(row){return Boolean(row && row.key!=='none' && !row.confirmed);}
function updateMappingReviewStatus(){
  const status=$('mapping-review-status');if(!status)return;
  const rows=state.rows.a||[];
  const suggested=rows.filter(r=>r.key==='none'&&r.suggestion);
  const pending=rows.filter(needsMeaningConfirmation);
  status.textContent=pending.length
    ? `${pending.length} assigned meaning(s) need confirmation. Review highlighted header(s) before processing.`
    :suggested.length
    ? `${suggested.length} possible header meaning(s) detected. Select Apply suggestion if correct; unselected columns remain in output.`
    :'Automatic mapping ready. All other columns remain in the result and are available to Conditions.';
  status.classList.toggle('mapping-review-warning',Boolean(pending.length||suggested.length));
  status.classList.toggle('hidden',!(pending.length||suggested.length));
}
function focusMappingIssue(index){
  unifiedEditorFilter='';
  applyUnifiedEditorFilter();
  openEditorPopup('header-mapping-editor');
  const target=$('mapping-a')?.querySelector(`tr[data-header-index="${index}"]`);
  if(!target)return;
  target.classList.add('mapping-attention');target.scrollIntoView({block:'center',behavior:'smooth'});
  const sel=target.querySelector('td:nth-child(2) select');if(sel)sel.focus({preventScroll:true});
}
function refreshPairSuggestions(){
  if(!state.rows.a)return;
  const bHeaders=new Set((state.rows.b||[]).map(r=>r.header));
  for(const row of state.rows.a){
    if(!row.pairA)row.pairA=row.header;
    if(row.pairBManual){if(!bHeaders.has(row.pairB)){row.pairBManual=false;row.pairB='';}else continue;}
    const suggestion=findSuggestedPair(row,'b');
    row.pairB=suggestion.header;row.pairConfidence=suggestion.score;row.pairMethod=suggestion.method;
  }
}
function setDetectedMeaning(index,key){
  const row=state.rows.a[index];if(!row)return;
  if(key!=='none'&&!exactKeys.has(key)&&state.rows.a.some((r,i)=>i!==index&&r.key===key)){
    showMessage(`Detected Meaning "${semantic[key]||key}" is already assigned to another File A column. Choose the correct column first.`);renderMapping('a');return;
  }
  row.key=key;row.confirmed=true;row.band='manual';row.pairBManual=false;
  row.pairB='';refreshPairSuggestions();renderMapping('a');renderRules();refreshRunReadiness();
}
function renderMapping(side){
  if(side!=='a')return;
  const tbody=$('mapping-a');if(!tbody)return;tbody.replaceChildren();
  const cross=$('mode').value==='cross';refreshPairSuggestions();
  const targetRows=cross?(state.rows.b||[]):(state.rows.a||[]);
  for(const [index,row] of (state.rows.a||[]).entries()){
    const tr=document.createElement('tr');tr.className='unified-header-row';tr.dataset.headerIndex=String(index);
    tr.classList.toggle('mapping-attention',needsMeaningConfirmation(row));
    const head=document.createElement('td');const title=document.createElement('strong');title.textContent=row.header;head.append(title);
    if(row.sampleValues?.length){const ex=document.createElement('small');ex.className='mapping-cell-help';
      ex.textContent='Sample: '+row.sampleValues.join(' | ');head.append(ex);}
    tr.append(head);
    const meaningCell=document.createElement('td'),meaning=document.createElement('select');
    meaning.setAttribute('aria-label',`File A ${row.header} detected meaning`);
    for(const [key,label] of Object.entries(semantic))meaning.add(new Option(label,key));
    meaning.value=row.key;meaning.addEventListener('change',()=>setDetectedMeaning(index,meaning.value));
    meaningCell.append(meaning);
    const conf=document.createElement('small');conf.className='mapping-confidence '+(row.confirmed?(row.band==='high'?'confidence-high':'confidence-manual'):'confidence-suggest');
    conf.textContent=row.key!=='none'?(row.confirmed?`✓ ${row.band==='manual'?'Confirmed manually':`Auto-detected · ${row.confidence}%`}`:'Confirmation required'):
      row.suggestion?`Possible match · ${row.confidence}% (${row.method})`:'No recognized meaning · kept in output';
    meaningCell.append(conf);
    if(row.key==='none'&&row.suggestion&&semantic[row.suggestion]){
      const suggestBtn=document.createElement('button');suggestBtn.type='button';suggestBtn.className='mapping-suggestion-button';
      suggestBtn.textContent=`↳ Apply suggestion: ${semantic[row.suggestion]}`;
      suggestBtn.title=`Verify this meaning before applying. ${row.warning||''}`;
      suggestBtn.addEventListener('click',()=>setDetectedMeaning(index,row.suggestion));meaningCell.append(suggestBtn);
    }
    tr.append(meaningCell);
    const pairCell=document.createElement('td'),pair=document.createElement('select');
    pair.setAttribute('aria-label',cross?`Detected Header in File B for ${row.header}`:`Detected Header in File A for ${row.header}`);
    const placeholder=cross?(targetRows.length?'— Choose File B header (optional for non-matching fields) —':'Upload File B to detect headers'):'— Select File A header —';
    pair.add(new Option(placeholder,''));for(const target of targetRows)pair.add(new Option(target.header,target.header));
    const current=cross?row.pairB:row.pairA;pair.value=targetRows.some(r=>r.header===current)?current:'';
    pair.disabled=cross&&!targetRows.length;
    pair.addEventListener('change',()=>{if(cross){row.pairB=pair.value;row.pairBManual=Boolean(pair.value);row.pairMethod='manual';row.pairConfidence=100;}else row.pairA=pair.value||row.header;renderMapping('a');});
    pairCell.append(pair);
    const pairHelp=document.createElement('small');pairHelp.className='mapping-cell-help';
    pairHelp.textContent=cross?(row.pairB?(row.pairBManual?'✓ Manually selected':`✓ Suggested ${row.pairMethod} · ${row.pairConfidence}%`):'No confident File B match. Select manually if needed.'):'Selected File A header will represent this meaning.';
    pairCell.append(pairHelp);tr.append(pairCell);
    const conditionCell=document.createElement('td');conditionCell.className='mapping-condition-cell';
    const conditionButton=document.createElement('button');conditionButton.type='button';conditionButton.className='secondary mapping-condition-add';
    conditionButton.textContent='+ Add condition';
    conditionButton.setAttribute('aria-label',`Add condition for ${row.header}`);
    conditionButton.addEventListener('click',()=>addMappingFieldCondition(index));
    conditionCell.append(conditionButton);tr.append(conditionCell);
    tbody.append(tr);
  }
  updateMappingReviewStatus();
  renderUnifiedMapping();
}
function updateFileBHeaderSummary(){
  const box=$("file-b-header-summary"),list=$("file-b-headers");if(!box||!list)return;
  const headers=state.files.b?.headers||[];box.textContent=headers.length?`${headers.length} File B header${headers.length===1?'':'s'} detected. All are available in the File A mapping dropdown.`:'File B headers will be detected automatically after upload.';
  list.replaceChildren();for(const h of headers){const chip=document.createElement('span');chip.className='header-chip';chip.textContent=h;list.append(chip);}
}
function displayFile(side,info){
  if(!info || !Array.isArray(info.headers) || !Array.isArray(info.sheet_details) || !info.mapping || !Array.isArray(info.sheets)){
    throw Error(`File ${side.toUpperCase()}: the server returned incomplete upload details.`);
  }
  // Prepare the rows before replacing a previously working upload.
  const rows=parseRows(info,side);
  state.files[side]=info;state.rows[side]=rows;$("details-"+side).classList.remove("hidden");
  if(side==='a')$("mapping-workspace")?.classList.remove("hidden");
  $("info-"+side).textContent=`${info.filename} · ${info.columns} columns · header row ${info.header_row}`+(info.rows==null?"":` · about ${info.rows.toLocaleString()} rows`);
  const sheet=$("sheet-"+side);sheet.replaceChildren();for(const item of info.sheet_details){sheet.add(new Option(item.name+(item.state!=="visible"?` (${item.state})`:""),item.name));}sheet.value=info.sheet;sheet.disabled=info.sheets.length<2;
  $("warnings-"+side).textContent=(info.warnings||[]).join(" ");$("warnings-"+side).classList.toggle("hidden",!(info.warnings||[]).length);
  setUploadStatus(side,"ready","✓ Uploaded and ready");
  if(side==='b')updateFileBHeaderSummary();refreshPairSuggestions();renderMapping('a');renderRules();
  refreshRunReadiness();
}
async function upload(side){
  const file=$("file-"+side).files[0];if(!file)return;
  if(state.busy)return;
  if(!state.session?.session_id){
    setUploadStatus(side,"error","Upload failed — session not ready");
    return showMessage("Session is not ready. Choose New session, then upload again.");
  }
  const previous=state.files[side];
  setBusy(true);
  setUploadStatus(side,"uploading","Uploading…");
  $("info-"+side).textContent=`Uploading ${file.name}…`;
  showMessage(`Uploading File ${side.toUpperCase()}…`,false);
  try{
    const data=new FormData();data.append("file",file);
    const info=await api(`/api/upload/${side}?session_id=${encodeURIComponent(state.session.session_id)}&high=${HEADER_HIGH_DEFAULT}&suggest=${HEADER_SUGGEST_DEFAULT}`,{method:"POST",body:data});
    displayFile(side,info);
    showMessage(`File ${side.toUpperCase()} uploaded: ${info.filename}. Review its header mapping.`,false);
  }catch(error){
    // Never destroy an earlier accepted upload when a new selection fails.
    if(previous){
      $("info-"+side).textContent=`Still using previously uploaded ${previous.filename}. Replacement ${file.name} was not accepted.`;
    }else{
      $("info-"+side).textContent=`${file.name} was selected but NOT uploaded. Please retry or choose another file.`;
    }
    setUploadStatus(side,"error",previous?"Upload failed · previous file retained":"Upload failed · not ready");
    showMessage(`File ${side.toUpperCase()} upload failed: ${error.message}`);
    // The upload error remains beside its upload card; Start is gated by server readiness.
  }finally{
    // Do not clear the file input: that made the selected filename disappear
    // after both successful and failed uploads. It also prevented simple retry.
    setBusy(false);
  }
}
async function selectSheet(side){
  const previous=state.files[side].sheet;setBusy(true);showMessage("Reading the selected worksheet…",false);
  try{displayFile(side,await api(`/api/select-sheet/${side}?session_id=${state.session.session_id}`,json("POST",{sheet:$("sheet-"+side).value,high:HEADER_HIGH_DEFAULT,suggest:HEADER_SUGGEST_DEFAULT})));showMessage("Worksheet loaded. Review its header mapping.",false);}
  catch(error){$("sheet-"+side).value=previous;showMessage(error.message);}finally{setBusy(false);}
}
function renderJob(job){
  state.job=job;$("job-panel").classList.remove("hidden");$("job-state").textContent=job.state.charAt(0).toUpperCase()+job.state.slice(1);
  $("job-message").textContent=job.message;$("log").textContent=job.log_tail||"";
  const indexed=['a','b'].filter(side=>job['indexed_rows_'+side]!==undefined).map(side=>`File ${side.toUpperCase()}: ${job['indexed_rows_'+side].toLocaleString()} indexed (${(job['rows_per_second_'+side]||0).toLocaleString()} rows/s)`);
  $("job-stage").textContent=[job.phase||'',...indexed].filter(Boolean).join(' · ');
  $("elapsed").textContent=job.started_at?`${Math.round(((job.finished_at||Date.now()/1000)-job.started_at))} seconds`:"Waiting for processing slot";
  const active=["queued","running"].includes(job.state);setBusy(active);$("job-progress").classList.toggle("hidden",!active);$("result-actions").classList.toggle("hidden",job.state!=="completed");$("result-help").classList.toggle("hidden",job.state!=="completed");
  $("counts").replaceChildren();
  $("counts-status").textContent="";
  if(job.state==="completed"){
    const c=job.result?.counts||{};
    const required=["File A Rows","Validated Records","Duplicate Records","Records for Review"];
    const good=required.every(k=>Number.isSafeInteger(c[k])&&c[k]>=0)
      && c["Validated Records"]+c["Duplicate Records"]+c["Records for Review"]===c["File A Rows"]
      && job.result.integrity_check===true && job.result.export_integrity_check===true;
    if(!good){
      $("result-actions").classList.add("hidden");
      $("result-help").classList.add("hidden");
      $("counts-status").textContent="Count verification failed; results were not confirmed. Check the processing log.";
      showMessage("Result counts do not reconcile with File A. Do not use this export.");
      return false;
    }
    for(const [title,labelText] of [["File A Rows","Total File A Records"],["Validated Records","Validated Records"],["Duplicate Records","Duplicate Records (File A)"],["Records for Review","Records for Review"]]){
      const div=document.createElement("div"),strong=document.createElement("strong"),label=document.createElement("span");
      div.className="metric";strong.textContent=c[title].toLocaleString();label.textContent=labelText;
      div.append(strong,label);$("counts").append(div);
    }
    const extra=c["File B Reference Rows in Duplicate Sheet"]||0;
    const duplicateSheetRows=c["Duplicate Sheet Rows"];
    $("counts-status").textContent=Number.isSafeInteger(duplicateSheetRows)
      ? `Verified: ${c["File A Rows"].toLocaleString()} File A records classified exactly once. Duplicate sheet: ${duplicateSheetRows.toLocaleString()} rows`+(extra?` (including ${extra.toLocaleString()} File B reference rows, not counted as File A duplicates).`:".")
      : `Verified: ${c["File A Rows"].toLocaleString()} File A records classified exactly once.`;
    // Both formats are available after completion, even for Fast CSV ZIP jobs.
    // The server converts the finished CSV ZIP to Excel on demand without
    // repeating any deduplication, matching, or advanced-condition rules.
    $("download").classList.remove('hidden');
    $("download").href=`/api/download/${job.session_id}/${job.job_id}`;
    $("download-csv").href=`/api/download-csv/${job.session_id}/${job.job_id}`;
    $("download").title=job.result.paths?.workbook?'Download the original formatted Excel workbook.':'Generate Excel from completed CSV ZIP results. Large files may take a few minutes.';
    showMessage("Processing completed. Download your result file.",false);
  }else if(job.state==="failed")showMessage(job.message);
  return active;
}
async function poll(jobId){
  clearTimeout(state.timer);
  try{
    const job=await api(`/api/job/${state.session.session_id}/${jobId}`);
    if(renderJob(job))state.timer=setTimeout(()=>poll(jobId),1500);
  }catch(error){showMessage(error.message+" Reconnecting to job status…");state.timer=setTimeout(()=>poll(jobId),5000);}
}
async function run(){
  if(!state.files.a)return showRunError("Upload File A first.");
  const cross=$("mode").value==="cross";
  if(cross&&!state.files.b)return showRunError("Upload File B for Cross Match.");
  for(const id of ["threshold","min-age","max-age"]){if(!$(id).checkValidity()||$(id).value===""){openEditorPopup("comparison-settings-editor");$(id).reportValidity();return showRunError("Check the threshold and age values in Comparison Settings.");}}
  const rowsA=state.rows.a||[];
  const pendingIndex=rowsA.findIndex(needsMeaningConfirmation);
  if(pendingIndex!==-1){
    showRunError(`File A header "${rowsA[pendingIndex].header}": confirm the Detected Meaning.`);
    focusMappingIssue(pendingIndex);return;
  }
  if(cross){
    // Only identity fields require File B mapping. Custom condition fields can
    // remain A-only; Conditions may select physical B headers independently.
    const missingPairIndex=rowsA.findIndex(r=>isMatchMeaning(r.key)&&!r.pairB&&!(exactKeys.has(r.key)&&['ignore','evidence'].includes(unifiedFieldModes.get(unifiedModeKey(r.header)))));
    if(missingPairIndex!==-1){
      showRunError(`File A header "${rowsA[missingPairIndex].header}": choose the corresponding File B header for identity matching.`);
      focusMappingIssue(missingPairIndex);return;
    }
    const used=new Map();
    for(const r of rowsA.filter(x=>x.pairB&&x.key!=='none'&&!(exactKeys.has(x.key)&&['ignore','evidence'].includes(unifiedFieldModes.get(x.header))))){
      if(used.has(r.pairB)&&used.get(r.pairB)!==r.key)return showRunError(`File B header "${r.pairB}" is assigned to multiple meanings. Choose distinct corresponding headers.`);
      used.set(r.pairB,r.key);
    }
  }
  const min=Number($("min-age").value),max=Number($("max-age").value);
  if(min&&max&&min>=max){openEditorPopup("comparison-settings-editor");$("min-age").focus();return showRunError("Minimum age must be lower than maximum age. Use 0 to disable either limit.");}
  setBusy(true);$("message").classList.add("hidden");$("result-actions").classList.add("hidden");$("result-help").classList.add("hidden");
  $("run-readiness").textContent="Submitting processing request…";
  try{
    const job=await api("/api/run",json("POST",{session_id:state.session.session_id,mode:$("mode").value,fuzzy_threshold:Number($("threshold").value),min_age:min,max_age:max,exhaustive:$("scan-preset").value==="exhaustive",fuzzy:$("fuzzy").checked,swap:$("swap").checked,export_format:$("export-format").value,mapping_a:mapping("a"),mapping_b:cross?mapping("b"):null,conditions:conditionsPayload()}));
    renderJob(job);poll(job.job_id);
  }catch(error){setBusy(false);showRunError(error.message);
    if(/^(Condition|Rule)\s+\d+:/i.test(error.message))openEditorPopup('unified-conditions-editor');
  }
}
async function deleteTemporaryFiles(){
  if(!state.session||state.busy)return;
  if(!window.confirm("Delete ALL source uploads, processing indexes, logs, and exports for THIS session? Complete your downloads first. This cannot be undone."))return;
  const sid=state.session.session_id;
  setBusy(true);
  try{
    await api(`/api/session/${sid}`,{method:"DELETE"});
    localStorage.removeItem(storageKey);
    state.session=null;
    await newSession();
    showMessage("Previous session and all its temporary beneficiary files were deleted. A fresh session is ready.",false);
  }catch(error){showMessage(error.message);}finally{setBusy(false);}
}
async function newSession(){
  setBusy(true);
  try{
    state.session=await api("/api/session",{method:"POST"});localStorage.setItem(storageKey,state.session.session_id);
    state.files={};state.rows={};clearTimeout(state.timer);$("job-panel").classList.add("hidden");$("result-actions").classList.add("hidden");$("result-help").classList.add("hidden");
    for(const side of ["a","b"]){$("info-"+side).textContent="No file uploaded.";$("details-"+side).classList.add("hidden");$("file-"+side).value="";setUploadStatus(side,"empty","Not uploaded");}
    $("mapping-workspace")?.classList.add("hidden");
    for(const id of ['header-mapping-editor','unified-conditions-editor'])closeEditorPopup(id);
    showMessage("New session ready. Earlier sessions expire automatically.",false);
  }catch(error){showMessage(error.message);}finally{setBusy(false);}
}
function localISODate(){const t=new Date();return new Date(t.getTime()-t.getTimezoneOffset()*60000).toISOString().slice(0,10);}
const ruleOperators={equals:'Equals (exact match)',not_equals:'Not Equals (different)',contains:'Contains text / phrase',is_any_of:'Matches any of these values',is_blank:'Is Blank',is_not_blank:'Is Not Blank',greater_than:'Greater Than',less_than:'Less Than',before_date:'Before selected date',after_date:'After selected date',within_last_months:'Within last N calendar months',older_than_months:'Older than N calendar months',future_date:'Future Date',invalid_date:'Invalid Date'};
const HEADER_HIGH_DEFAULT=95, HEADER_SUGGEST_DEFAULT=80;
const ruleActions={validated_records:'VALIDATED RECORDS (CLEAN)',duplicate_records:'DUPLICATE RECORDS',records_for_review:'RECORDS FOR REVIEW'};
const actionGuidance={
  validated_records:'Use CLEAN / Validated when no duplicate or separate review finding applies.',
  duplicate_records:'Use only for a verified identity match. If no accepted match exists, the system sends it to REVIEW instead.',
  records_for_review:'Send the record for checking. Verified duplicates stay in DUPLICATE RECORDS and receive a review flag.'
};
function normalizedRuleAction(action){return ({flag_review:'records_for_review',note_only:'validated_records'})[action]||action;}

// Every original uploaded column is available in Conditions, even if its
// meaning is unknown; removing the Use control must not hide source columns.
function eligibleHeaders(side){return (state.rows[side]||[]).map(row=>row.header);}
function findHeader(side,patterns){
  const headers=eligibleHeaders(side);
  for(const pattern of patterns){const found=headers.find(h=>pattern.test(String(h).toLowerCase()));if(found)return found;}
  return '';
}
function nextRuleGroup(){return String(conditionState.rules.length+1);}
function newRule(cross=false){return {name:cross?`Compare A/B ${conditionState.rules.length+1}`:`Rule ${conditionState.rules.length+1}`,source:cross?'cross':'a',header:eligibleHeaders('a')[0]||'',compare_header:cross?(eligibleHeaders('b')[0]||''):'',operator:cross?'not_equals':'equals',value:'',value_operator:cross?'none':'',action:'records_for_review',priority:100,group:String(conditionState.rules.length+1),join:'AND',enabled:true};}
function ruleControl(label,element){const wrapper=document.createElement('label');wrapper.textContent=label;wrapper.append(element);return wrapper;}
function updateRuleHeaders(select,rule){
  select.replaceChildren();const side=rule.source==='cross'?'a':rule.source;
  const headers=eligibleHeaders(side);
  select.add(new Option(headers.length?'— Select File '+side.toUpperCase()+' column —':`Upload File ${side.toUpperCase()} to select a column`,''));
  for(const header of headers)select.add(new Option(header,header));
  // Do not overwrite a saved profile's chosen header before a file is uploaded.
  select.value=headers.includes(rule.header)?rule.header:'';
}
function updateComparisonHeader(select,rule){
  const headers=eligibleHeaders('b');select.replaceChildren();
  select.add(new Option(headers.length?'— Select File B column —':'Upload File B to select its column',''));
  for(const header of headers)select.add(new Option(header,header));
  select.value=headers.includes(rule.compare_header)?rule.compare_header:'';
}
function renderRules(){
  const target=$('custom-rules');target.replaceChildren();
  $('custom-rule-status').textContent=conditionState.rules.length
    ?`${conditionState.rules.length} custom condition(s). Complete each enabled condition before processing.`
    :$('mode').value==='cross'?'No conditions yet. Choose Compare File A ↔ File B above.':'No conditions yet. Choose Check File A above.';

  const guidedStep=(number,title,control,extraClass='')=>{
    const wrap=document.createElement('section');wrap.className=`guided-rule-step ${extraClass}`.trim();
    const head=document.createElement('div');head.className='guided-step-heading';
    const badge=document.createElement('span');badge.className='guided-step-number';badge.textContent=String(number);
    const label=document.createElement('strong');label.textContent=title;head.append(badge,label);
    wrap.append(head,control);return wrap;
  };

  conditionState.rules.forEach((rule,index)=>{
    rule.action=normalizedRuleAction(rule.action);
    const card=document.createElement('article');card.className='rule-item';card.classList.toggle('disabled',!rule.enabled);
    card.dataset.action=rule.action;
    const top=document.createElement('div');top.className='rule-title';
    const headerGroup=document.createElement('div');headerGroup.className='rule-card-heading';
    const number=document.createElement('span');number.className='rule-number';number.textContent=String(index+1);
    const title=document.createElement('strong');title.textContent=rule.name?.trim()||`Condition ${index+1}`;
    const typeBadge=document.createElement('span');typeBadge.className='rule-type-badge';typeBadge.textContent=rule.source==='cross'?'A ↔ B comparison':'Single-file check';
    headerGroup.append(number,title,typeBadge);
    const topActions=document.createElement('div');topActions.className='rule-card-actions';
    const active=document.createElement('label');active.className='inline-toggle';
    const toggle=document.createElement('input');toggle.type='checkbox';toggle.checked=rule.enabled;
    toggle.addEventListener('change',()=>{rule.enabled=toggle.checked;card.classList.toggle('disabled',!rule.enabled);});
    active.append(toggle,document.createTextNode('Enabled'));
    const remove=document.createElement('button');remove.className='secondary tiny danger-outline';remove.type='button';remove.textContent='Remove';
    remove.setAttribute('aria-label',`Remove Condition ${index+1}`);
    remove.addEventListener('click',()=>{conditionState.rules.splice(index,1);renderRules();});
    topActions.append(active,remove);top.append(headerGroup,topActions);card.append(top);

    const isCross=rule.source==='cross';card.classList.toggle('cross-compare-rule',isCross);
    if(isCross && !rule.value_operator)rule.value_operator=(rule.value||'').trim()?'equals':'none';

    // Rule identity: short name + check type. The actual cross-file flow appears below in numbered order.
    const identity=document.createElement('div');identity.className='rule-identity-grid';
    const name=document.createElement('input');name.value=rule.name;name.maxLength=65;name.placeholder=isCross?'Example: Compare death status':'Example: Review deceased records';
    name.addEventListener('input',()=>{rule.name=name.value;title.textContent=name.value.trim()||`Condition ${index+1}`;});
    const nameControl=ruleControl('Rule name (optional)',name);nameControl.classList.add('friendly-control');
    const nameHelp=document.createElement('small');nameHelp.className='control-help';nameHelp.textContent='Give this rule a short name so it is easy to recognize later.';nameControl.append(nameHelp);identity.append(nameControl);

    // v6.32.55: this selector was previously disabled even in Cross Match mode.
    // All choices use the existing, supported condition operators and payload.
    const comparisonType=document.createElement('select');
    const checkKinds=[
      ['single','Check one field against text / value'],
      ['number','Check a numeric field'],
      ['date','Check a date field (calendar)'],
      ['blank','Check blank / missing information']
    ];
    if($('mode').value==='cross')checkKinds.push(['cross','Compare File A field ↔ File B field']);
    for(const [value,label] of checkKinds)comparisonType.add(new Option(label,value));
    function kindForRule(){
      if(rule.source==='cross')return 'cross';
      if(['is_blank','is_not_blank'].includes(rule.operator))return 'blank';
      if(['before_date','after_date','future_date','invalid_date','within_last_months','older_than_months'].includes(rule.operator))return 'date';
      if(['greater_than','less_than'].includes(rule.operator))return 'number';
      return 'single';
    }
    comparisonType.value=kindForRule();
    comparisonType.addEventListener('change',()=>{
      const kind=comparisonType.value;
      const previouslyCross=rule.source==='cross';
      rule.source=kind==='cross'?'cross':previouslyCross?'a':rule.source;
      rule.operator=kind==='cross'?'equals':kind==='number'?'greater_than':kind==='date'?'before_date':kind==='blank'?'is_blank':'equals';
      rule.value='';
      rule.value_operator=kind==='cross'?'none':'';
      if(!previouslyCross && kind==='cross')rule.compare_header='';
      renderRules();
    });
    const typeControl=ruleControl('What kind of check?',comparisonType);typeControl.classList.add('friendly-control');
    const typeHelp=document.createElement('small');typeHelp.className='control-help';
    typeHelp.textContent='Choose text, number, date, or blank checks. In Cross Match mode you can also compare File A with File B.';
    typeControl.append(typeHelp);identity.append(typeControl);

    if(!isCross){
      const source=document.createElement('select');const sources=$('mode').value==='cross'?[['a','File A – Main'],['b','File B – Reference']]:[['a','File A']];for(const [v,l] of sources)source.add(new Option(l,v));source.value=rule.source;
      source.addEventListener('change',()=>{rule.source=source.value;rule.header='';renderRules();});
      const sourceControl=ruleControl('Which file should be checked?',source);sourceControl.classList.add('friendly-control');
      const sourceHelp=document.createElement('small');sourceHelp.className='control-help';sourceHelp.textContent=$('mode').value==='cross'?'File A is the Main file. File B is the Master / Reference file.':'This condition checks File A only.';sourceControl.append(sourceHelp);identity.append(sourceControl);
    }
    card.append(identity);

    const selectedHeader=document.createElement('select');updateRuleHeaders(selectedHeader,rule);
    selectedHeader.addEventListener('change',()=>rule.header=selectedHeader.value);
    const selectedControl=ruleControl(isCross?'File A field to check':'Which field should be checked?',selectedHeader);selectedControl.classList.add('friendly-control');
    const selectedHelp=document.createElement('small');selectedHelp.className='control-help';selectedHelp.textContent=isCross?'Start here: choose the File A / Main column you want to check.':'Choose the column where the system should look.';selectedControl.append(selectedHelp);

    let targetHeader=null,targetControl=null;
    if(isCross){
      targetHeader=document.createElement('select');updateComparisonHeader(targetHeader,rule);
      targetHeader.addEventListener('change',()=>rule.compare_header=targetHeader.value);
      targetControl=ruleControl('File B field to compare against',targetHeader);targetControl.classList.add('friendly-control');
      const targetHelp=document.createElement('small');targetHelp.className='control-help';targetHelp.textContent='After choosing the check above, select the matching File B / Master column.';targetControl.append(targetHelp);
    }

    const op=document.createElement('select');
    const crossOperatorLabels={
      equals:'Same value (Equals)',not_equals:'Different values (Not Equals)',contains:'File A contains File B text',
      greater_than:'File A number is greater than File B',less_than:'File A number is less than File B',
      before_date:'File A date is before File B date',after_date:'File A date is after File B date'
    };
    for(const [v,l] of Object.entries(ruleOperators)){
      if(isCross && ['is_any_of','is_blank','is_not_blank','within_last_months','older_than_months','future_date','invalid_date'].includes(v))continue;
      op.add(new Option(isCross?(crossOperatorLabels[v]||l):l,v));
    }
    if(!Array.from(op.options).some(option=>option.value===rule.operator))rule.operator='equals';
    op.value=rule.operator;
    const opControl=ruleControl(isCross?'How should File A compare with File B?':'How should this field be checked?',op);opControl.classList.add('friendly-control');
    const opHelp=document.createElement('small');opHelp.className='control-help';opControl.append(opHelp);
    const crossOperatorHelp={
      equals:'The two selected values must be the same.',
      not_equals:'The two selected values must be different.',
      contains:'The File A text must contain the File B text.',
      greater_than:'Both selected fields should contain numbers. File A must be greater than File B.',
      less_than:'Both selected fields should contain numbers. File A must be less than File B.',
      before_date:'Both selected fields should contain dates. File A must be earlier than File B.',
      after_date:'Both selected fields should contain dates. File A must be later than File B.'
    };
    function updateOperatorHelp(){opHelp.textContent=isCross?(crossOperatorHelp[op.value]||'Choose how the two fields should be compared.'):'Choose the rule the system should apply to this field.';}
    updateOperatorHelp();

    const val=document.createElement('input');val.value=rule.value||'';val.maxLength=500;
    const valueWrap=document.createElement('label');valueWrap.className='rule-value-control';
    const valueLabel=document.createElement('span');valueLabel.className='rule-value-label';
    const valueHelp=document.createElement('small');valueHelp.className='rule-value-help';
    // An explicit calendar control remains clickable even where the browser's
    // native calendar icon is difficult to see inside the scrollable pop-up.
    const dateLine=document.createElement('div');dateLine.className='rule-date-line';
    const datePicker=document.createElement('button');datePicker.type='button';datePicker.className='secondary rule-date-picker';
    datePicker.textContent='📅 Choose date';datePicker.hidden=true;
    datePicker.setAttribute('aria-label','Open calendar for selected date');
    datePicker.addEventListener('click',()=>{
      if(val.disabled || val.type!=='date')return;
      try{if(typeof val.showPicker==='function'){val.showPicker();return;}}catch(_){ /* Browser may restrict native picker. */ }
      val.focus();
    });
    dateLine.append(val,datePicker);valueWrap.append(valueLabel,dateLine,valueHelp);

    const valueModes={
      equals:{label:'Value or text to match exactly',type:'text',placeholder:'Example: Value',help:'Enter the exact word, phrase, sentence, or number that should match the selected column.'},
      not_equals:{label:'Value or text that must be different',type:'text',placeholder:'Example: Active',help:'The condition triggers when the selected column is different from this value.'},
      contains:{label:'Text or phrase to find',type:'text',placeholder:'Example: transferred to another municipality',help:'Enter a word, phrase, or sentence fragment to look for.'},
      is_any_of:{label:'Values to check for',type:'text',placeholder:'Example: Value 1, Value 2, Value 3',help:'Enter several accepted values separated by commas.'},
      greater_than:{label:'Number to compare',type:'number',placeholder:'Example: 60',help:'Enter a number. The selected field must be greater than this number.'},
      less_than:{label:'Number to compare',type:'number',placeholder:'Example: 18',help:'Enter a number. The selected field must be less than this number.'},
      before_date:{label:'Select date',type:'date',placeholder:'',help:'Choose a date using the calendar. The selected record date must be earlier.'},
      after_date:{label:'Select date',type:'date',placeholder:'',help:'Choose a date using the calendar. The selected record date must be later.'},
      within_last_months:{label:'Number of months',type:'number',placeholder:'Example: 3',help:'Example: 3 means within the last 3 calendar months, including the boundary date.'},
      older_than_months:{label:'Number of months',type:'number',placeholder:'Example: 3',help:'Example: 3 means older than the last 3 calendar months.'},
      is_blank:{hidden:true,help:'No value is needed. The condition checks whether the selected column is empty.'},
      is_not_blank:{hidden:true,help:'No value is needed. The condition checks whether the selected column contains a value.'},
      future_date:{hidden:true,help:'No value is needed. The condition checks whether the selected date is later than the processing date.'},
      invalid_date:{hidden:true,help:'No value is needed. The condition checks for a non-blank date that the system cannot read.'}
    };

    // Cross-file rules may optionally add a second, fixed-value check on File A.
    // This preserves old profiles: an old nonblank `value` becomes Equals automatically.
    let extraOperator=null,extraOperatorControl=null;
    if(isCross){
      extraOperator=document.createElement('select');
      for(const [v,l] of [
        ['none','No extra File A check'],['equals','Equals a text / number'],['not_equals','Does not equal a text / number'],
        ['contains','Contains text / phrase'],['greater_than','Number is greater than'],['less_than','Number is less than'],
        ['before_date','Date is before'],['after_date','Date is after']
      ])extraOperator.add(new Option(l,v));
      if(!rule.value_operator)rule.value_operator=(rule.value||'').trim()?'equals':'none';
      extraOperator.value=rule.value_operator;
      extraOperatorControl=ruleControl('Optional extra check for File A',extraOperator);extraOperatorControl.classList.add('friendly-control','extra-operator-control');
      const extraHelp=document.createElement('small');extraHelp.className='control-help';extraHelp.textContent='Optional. Use this only if File A must also meet a fixed text, number, or date condition.';extraOperatorControl.append(extraHelp);
    }

    function setInputMode(mode,optional=false){
      const hidden=Boolean(mode?.hidden);
      valueWrap.classList.toggle('no-value-needed',hidden);
      val.disabled=hidden;val.hidden=hidden;valueLabel.hidden=hidden;
      dateLine.hidden=hidden;
      valueLabel.textContent=mode?.label||'Value / text to check';valueHelp.textContent=mode?.help||'';
      const oldType=val.type;val.type=mode?.type||'text';val.placeholder=mode?.placeholder||'';
      datePicker.hidden=hidden||val.type!=='date';
      val.inputMode=val.type==='number'?'decimal':val.type==='text'?'text':'';
      val.removeAttribute('min');val.removeAttribute('max');val.removeAttribute('step');
      if(['within_last_months','older_than_months'].includes(optional?extraOperator?.value:op.value)){val.min='1';val.max='120';val.step='1';}
      if((optional?extraOperator?.value:op.value) && ['greater_than','less_than'].includes(optional?extraOperator.value:op.value))val.step='any';
      if(optional&&extraOperator?.value==='none'){
        valueWrap.classList.add('no-value-needed');val.disabled=true;val.hidden=true;dateLine.hidden=true;valueLabel.hidden=true;
        valueHelp.textContent='No extra value is required. The rule will use only the File A ↔ File B comparison.';
      }
      // Avoid leaving invalid text trapped in a date/number control after the user changes the operator.
      if(val.type==='date' && rule.value && !/^\d{4}-\d{2}-\d{2}$/.test(rule.value)){rule.value='';val.value='';}
      if(val.type==='number' && rule.value && !Number.isFinite(Number(rule.value))){rule.value='';val.value='';}
      if(oldType!==val.type)val.value=rule.value||'';
    }
    function valueMode(){
      if(isCross){
        const x=extraOperator?.value||'none';
        if(x==='none')return setInputMode({hidden:true,help:'No extra value is required. The rule will use only the File A ↔ File B comparison.'},true);
        const mode=valueModes[x]||valueModes.equals;
        const adapted={...mode,label:mode.label.replace(/^Value or text/,'Optional value or text').replace(/^Number/,'Optional number').replace(/^Select date/,'Optional fixed date')};
        if(x==='before_date'||x==='after_date')adapted.help=(x==='before_date'?'Choose a fixed date. File A must also be before this date.':'Choose a fixed date. File A must also be after this date.');
        else adapted.help=`Optional extra filter for File A. ${mode.help}`;
        return setInputMode(adapted,true);
      }
      return setInputMode(valueModes[op.value]||valueModes.equals,false);
    }

    const actionBox=ruleControl('If this condition matches, where should the record go?',document.createElement('select'));actionBox.className='condition-action-box friendly-control';
    const action=actionBox.querySelector('select');for(const [v,l] of Object.entries(ruleActions))action.add(new Option(l,v));action.value=rule.action;
    const actionHelp=document.createElement('small');actionHelp.className='action-guidance';actionHelp.textContent=actionGuidance[rule.action]||'';
    function setAction(){rule.action=action.value;card.dataset.action=rule.action;actionHelp.textContent=actionGuidance[rule.action]||'';}
    action.addEventListener('change',setAction);actionBox.append(actionHelp);

    if(isCross){
      const flow=document.createElement('div');flow.className='cross-guided-flow';
      flow.append(
        guidedStep(1,'Start with File A',selectedControl,'step-file-a'),
        guidedStep(2,'Choose how to compare',opControl,'step-operator'),
        guidedStep(3,'Optional extra File A check',document.createElement('div'),'step-extra'),
        guidedStep(4,'Then choose File B',targetControl,'step-file-b'),
        guidedStep(5,'Choose the result',actionBox,'step-action')
      );
      const extraStep=flow.querySelector('.step-extra');
      const extraStack=document.createElement('div');extraStack.className='extra-check-stack';extraStack.append(extraOperatorControl,valueWrap);extraStep.append(extraStack);
      card.append(flow);
    }else{
      const logic=document.createElement('div');logic.className='rule-logic-grid';
      logic.append(opControl,valueWrap,actionBox);card.append(selectedControl);card.append(logic);
    }

    const summaryBox=document.createElement('div');summaryBox.className='rule-human-summary';
    const summaryLabel=document.createElement('strong');summaryLabel.textContent='Rule summary';
    const summaryText=document.createElement('span');
    function readableField(value,fallback){return value&&String(value).trim()?String(value):fallback;}
    function updateRuleSummary(){
      const actionText=ruleActions[rule.action]||rule.action;
      if(isCross){
        const a=readableField(rule.header,'[choose File A field]');const b=readableField(rule.compare_header,'[choose File B field]');
        const opText=(op.options[op.selectedIndex]?.text||rule.operator);const v=(rule.value||'').trim();
        let extra='';
        if(extraOperator&&extraOperator.value!=='none'&&v){const extraText=extraOperator.options[extraOperator.selectedIndex]?.text||extraOperator.value;extra=`; additionally File A “${a}” ${extraText.toLowerCase()} “${v}”`;}
        summaryText.textContent=`Compare File A “${a}” with File B “${b}” using “${opText}”${extra} → ${actionText}.`;
      }else{
        const sourceText=rule.source==='b'?'File B':'File A';const field=readableField(rule.header,'[choose field]');
        const opText=(op.options[op.selectedIndex]?.text||rule.operator);const v=(rule.value||'').trim();
        summaryText.textContent=`Check ${sourceText} “${field}” using “${opText}”${v?` with “${v}”`:''} → ${actionText}.`;
      }
    }
    summaryBox.append(summaryLabel,summaryText);card.append(summaryBox);

    selectedHeader.addEventListener('change',updateRuleSummary);
    if(targetHeader)targetHeader.addEventListener('change',updateRuleSummary);
    op.addEventListener('change',()=>{
      rule.operator=op.value;
      // Previously valueMode() was only called on initial render, leaving an
      // old text input visible after choosing date/number/blank operators.
      valueMode();updateOperatorHelp();updateRuleSummary();
      comparisonType.value=kindForRule();
    });
    val.addEventListener('input',()=>{rule.value=val.value;updateRuleSummary();});
    action.addEventListener('change',updateRuleSummary);
    if(extraOperator){
      extraOperator.addEventListener('change',()=>{rule.value_operator=extraOperator.value;if(extraOperator.value==='none'){rule.value='';val.value='';}valueMode();updateRuleSummary();});
    }
    valueMode();updateRuleSummary();

    const adv=document.createElement('details');adv.className='rule-advanced';
    const summary=document.createElement('summary');summary.textContent='Advanced options — combine rules or change priority';adv.append(summary);
    const advanced=document.createElement('div');advanced.className='rule-advanced-grid';
    const priority=document.createElement('input');priority.type='number';priority.min=1;priority.max=999;priority.value=rule.priority;
    priority.addEventListener('change',()=>rule.priority=Math.max(1,Math.min(999,Number(priority.value)||100)));
    const group=document.createElement('input');group.value=rule.group;group.maxLength=16;group.addEventListener('input',()=>rule.group=group.value);
    const join=document.createElement('select');join.add(new Option('AND','AND'));join.add(new Option('OR','OR'));join.value=rule.join;
    join.addEventListener('change',()=>rule.join=join.value);
    advanced.append(ruleControl('Rule priority (1 runs first)',priority),ruleControl('Group number',group),ruleControl('Combine with previous rule in group',join));
    const groupHelp=document.createElement('small');groupHelp.className='muted';groupHelp.textContent='Rules in each group run by priority. This AND/OR combines the current rule with the previous result; the first rule has no previous result. Use separate groups for File B-only checks and File A or A/B checks. Records for Review takes precedence over Clean; Duplicate still requires accepted matching evidence.';
    adv.append(advanced,groupHelp);card.append(adv);
    target.append(card);
  });
  updateUnifiedRuleSummary();
}
function conditionsPayload(forSave=false){
  for(const [i,r] of conditionState.rules.entries()){
    if(!$('conditions-enabled').checked||!r.enabled)continue;
    const selectedSide=r.source==='cross'?'a':r.source;
    if(!r.header||!eligibleHeaders(selectedSide).includes(r.header))throw Error(`Condition ${i+1}: upload File ${selectedSide.toUpperCase()} and select a non-ignored column.`);
    if(r.source==='cross'||r.source==='b'){
      if($('mode').value!=='cross')throw Error(`Condition ${i+1}: select Cross Match processing mode to compare File A with File B.`);
      if(r.source==='cross'&&(!r.compare_header||!eligibleHeaders('b').includes(r.compare_header)))throw Error(`Condition ${i+1}: upload File B and select a non-ignored File B comparison column.`);
    }
    if(r.source==='cross'){
      const extra=r.value_operator||((r.value||'').trim()?'equals':'none');
      if(extra!=='none' && !(r.value||'').trim())throw Error(`Condition ${i+1}: enter the optional File A value required by the extra check, or choose No extra File A check.`);
      if(['greater_than','less_than'].includes(extra) && !Number.isFinite(Number(r.value)))throw Error(`Condition ${i+1}: enter a valid number for the extra File A check.`);
      if(['before_date','after_date'].includes(extra) && !/^\d{4}-\d{2}-\d{2}$/.test(String(r.value||'')))throw Error(`Condition ${i+1}: choose a valid date for the extra File A check.`);
    }
    if(r.source!=='cross' && !['is_blank','is_not_blank','future_date','invalid_date'].includes(r.operator) && !(r.value||'').trim())throw Error(`Condition ${i+1}: enter the value, text, number, date, or months required by the selected operator.`);
    if(['within_last_months','older_than_months'].includes(r.operator)){const months=Number(r.value);if(!Number.isInteger(months)||months<1||months>120)throw Error(`Condition ${i+1}: enter a whole number of months from 1 to 120.`);}
  }
  return {mode:$('mode').value,enabled:$('conditions-enabled').checked,builtins:{},
    rules:conditionState.rules.map(r=>({...r,action:normalizedRuleAction(r.action),enabled:(forSave||$('conditions-enabled').checked)&&Boolean(r.enabled)})),
    reference_date:localISODate(),header_high:HEADER_HIGH_DEFAULT,header_suggest:HEADER_SUGGEST_DEFAULT};
}
function applyConditionConfig(config){
  const d=config||{};
  $('conditions-enabled').checked=d.enabled!==false;
  const rules=(d.rules||[]).map(r=>({...r,action:normalizedRuleAction(r.action)}));
  const target=rules.some(r=>['cross','b'].includes(r.source))||d.mode==='cross'?'cross':d.mode==='file-a'?'file-a':$('mode').value;
  conditionState.byMode[conditionState.mode]=conditionState.rules;
  conditionState.byMode[target]=rules;
  conditionState.rules=conditionState.byMode[conditionState.mode];
  renderRules();renderUnifiedMapping();
}
async function loadConditionProfiles(){
  try{const found=await api('/api/condition-profiles');conditionState.profiles=found.profiles||[];renderProfileOptions();}
  catch(e){showMessage('Cannot load condition profiles: '+e.message);}
}
function renderProfileOptions(selectId=''){
  const select=$('condition-profile');const wanted=selectId||select.value;select.replaceChildren();
  select.add(new Option('Unsaved / default profile',''));
  for(const p of conditionState.profiles)select.add(new Option(p.name,p.id));
  if(conditionState.profiles.some(p=>p.id===wanted))select.value=wanted;
}
function selectedProfile(){return conditionState.profiles.find(p=>p.id===$('condition-profile').value);}
async function saveConditionProfile(duplicate=false){
  try{
    const config=conditionsPayload(true);const chosen=selectedProfile();
    let name=window.prompt(duplicate?'Name for the duplicated profile:':chosen?'Edit profile name:':'New profile name:',duplicate?(chosen?.name||'Conditions')+' copy':chosen?.name||'My Conditions');
    if(name===null)return;name=name.trim();if(!name)return showMessage('Profile name is required.');
    const overwrite=chosen&&!duplicate;
    const saved=await api(overwrite?`/api/condition-profiles/${chosen.id}`:'/api/condition-profiles',json(overwrite?'PUT':'POST',{name,config}));
    await loadConditionProfiles();renderProfileOptions(saved.id);showMessage('Condition profile saved.',false);
  }catch(e){showMessage(e.message);}
}
function initConditions(){
  applyConditionConfig(null);
  function addCondition(cross){
    if(cross!==($('mode').value==='cross'))return;
    if(conditionState.rules.length>=40)return showMessage('Maximum 40 custom conditions.');
    conditionState.rules.push(newRule(cross));renderRules();
    const last=$('custom-rules').lastElementChild;
    if(last){last.scrollIntoView({behavior:'smooth',block:'nearest'});}
  }
  $('add-condition').addEventListener('click',()=>addCondition($('mode').value==='cross'));
  $('save-profile').addEventListener('click',()=>saveConditionProfile(false));
  $('duplicate-profile').addEventListener('click',()=>saveConditionProfile(true));
  $('load-profile').addEventListener('click',()=>{const chosen=selectedProfile();if(!chosen)return showMessage('Select a saved condition profile.');applyConditionConfig(chosen.config);showMessage('Profile loaded into its matching mode. Processing mode is unchanged; select that mode above to review its conditions.',false);});
  $('reset-profile').addEventListener('click',()=>{if(!window.confirm('Reset current conditions to defaults? This does not delete saved profiles.'))return;applyConditionConfig(null);$('condition-profile').value='';showMessage('Current conditions reset.',false);});
  $('delete-profile').addEventListener('click',async()=>{const chosen=selectedProfile();if(!chosen)return showMessage('Select a saved profile to delete.');if(!window.confirm(`Delete rule profile "${chosen.name}"?`))return;
    try{await api(`/api/condition-profiles/${chosen.id}`,{method:'DELETE'});await loadConditionProfiles();renderProfileOptions('');showMessage('Saved profile deleted.',false);}catch(e){showMessage(e.message);}
  });
}

// v6.32.52 unified interface: a presentation/controller layer over the existing
// mapping, rule-builder, and API payload functions. Source data and match scoring
// remain unchanged. No extra field silently becomes an identity key.
function openEditorPopup(id){
  const popup=$(id);
  if(!popup || state.busy)return;
  for(const otherId of ['comparison-settings-editor','header-mapping-editor','unified-conditions-editor']){
    if(otherId!==id && $(otherId)?.open)$(otherId).close();
  }
  if(!popup.open)popup.showModal();
}
function closeEditorPopup(id){const popup=$(id);if(popup?.open)popup.close();}
const unifiedExtraHeaders = new Set();
const unifiedFieldModes = new Map();
function unifiedModeKey(header){return `${$('mode').value}:${header}`;}
const unifiedCoreName = new Set(['full_name','first','middle','last','suffix']);
const unifiedCoreDob = new Set(['birthdate','birth_month','birth_day','birth_year']);
let unifiedEditorFilter = '';
function unifiedChipList(headers){
  const list=document.createElement('div');list.className='unified-chips';
  if(!headers.length){const empty=document.createElement('span');empty.className='unified-chip empty';empty.textContent='Not detected — review mapping';list.append(empty);}
  for(const header of [...new Set(headers)]){const chip=document.createElement('span');chip.className='unified-chip';chip.textContent=header;list.append(chip);}
  return list;
}
function unifiedRuleFor(header){return conditionState.rules.find(r=>r.source==='a'&&(r.uiHeader===header||r.header===header))||null;}
function updateUnifiedRuleSummary(){
  // No redundant status badges on the main screen. Retain a concise accessible
  // summary on the Rules button; the full rules remain inside the pop-up.
  const n=conditionState.rules.filter(r=>r.enabled).length;
  const button=$('open-conditions-popup');
  if(button)button.setAttribute('aria-label',`Rules and saved setups (${n} enabled)`);
}
function applyUnifiedEditorFilter(){
  const rows=state.rows.a||[],filter=unifiedEditorFilter;
  let visible=0;
  for(const el of $('mapping-a').querySelectorAll('tr[data-header-index]')){
    const row=rows[Number(el.dataset.headerIndex)];
    const show=!filter||(row&&(filter==='name'?unifiedCoreName.has(row.key):filter==='dob'?unifiedCoreDob.has(row.key):row.header===filter));
    el.classList.toggle('unified-editor-filtered',!show);
    if(show)visible++;
  }
  const title=$('mapping-popup-title'),description=$('mapping-popup-description');
  if(filter==='name'){
    title.textContent='Edit Name headers';
    description.textContent='Only Name fields are shown (Full, First, Middle, Last, Suffix). Add a condition for any listed field.';
  }else if(filter==='dob'){
    title.textContent='Edit Date of Birth (DOB) headers';
    description.textContent='Only DOB fields are shown (Full Birthdate or Birth Month, Day, Year). Add a condition for any listed field.';
  }else if(filter){
    title.textContent=`Edit header: ${filter}`;
    description.textContent='Edit this header or add a condition for its value.';
  }else{
    title.textContent='Edit detected meanings and header pairs';
    description.textContent='Review all detected headers. Use Add condition beside a field to open the Rule Builder.';
  }
  $('mapping-editor-empty').classList.toggle('hidden',visible!==0);
}
function unifiedOpenMapping(which){
  unifiedEditorFilter=which;
  applyUnifiedEditorFilter();
  openEditorPopup('header-mapping-editor');
}
// A rule is deliberately created for a single selected file column, not for an
// entire Name/DOB group. The existing Rule Builder handles all operators/actions.
function addMappingFieldCondition(index){
  const row=(state.rows.a||[])[index];
  if(!row||state.busy)return;
  if(conditionState.rules.length>=40){showMessage('Maximum 40 custom conditions. Remove an old rule before adding another.');return;}
  const cross=$('mode').value==='cross';
  const header=row.pairA||row.header;
  const rule=newRule(cross);
  rule.header=header;
  rule.name=`${header} — Condition`;
  if(cross){
    rule.source='cross';
    rule.compare_header=row.pairB||'';
    rule.operator='equals';
    rule.value_operator='none';
  }else{
    rule.source='a';
    rule.operator='equals';
    rule.value=''; // Require the user to configure a real check before running.
  }
  conditionState.rules.push(rule);
  renderRules();
  openEditorPopup('unified-conditions-editor');
  const idx=conditionState.rules.indexOf(rule);
  const card=$('custom-rules').children[idx];
  if(card)card.scrollIntoView({block:'nearest'});
  showMessage(`Condition added for “${header}”. Choose its check and outcome in the Rule Builder.`,false);
}
function unifiedOpenRule(header,action){
  if(conditionState.rules.length>=40 && !unifiedRuleFor(header)){showMessage('Maximum 40 conditions. Remove an old rule before adding another.');return;}
  let rule=unifiedRuleFor(header);
  if(!rule){
    // Start with a deliberately incomplete condition rather than silently
    // routing every row to REVIEW without the user's operator/value selection.
    rule=newRule(false);rule.source='a';rule.header=header;rule.compare_header='';
    rule.name=`${header} — ${action==='review'?'Review rule':'Condition'}`;
    rule.action='records_for_review';rule.operator='equals';rule.value='';
    rule.uiHeader=header;conditionState.rules.push(rule);
  }else {rule.enabled=true;if(action==='review')rule.action='records_for_review';}
  renderRules();openEditorPopup('unified-conditions-editor');
  // Find the associated rule editor without relying on untrusted header as selector.
  const idx=conditionState.rules.indexOf(rule);
  const card=$('custom-rules').children[idx];if(card)card.scrollIntoView({block:'center',behavior:'smooth'});
  showMessage(`Configure the check for “${header}” and save the rule if needed. It runs only after you start processing.`,false);
}
function unifiedActionChanged(header,mode){
  unifiedFieldModes.set(unifiedModeKey(header),mode);
  const existing=unifiedRuleFor(header);
  if(mode==='ignore'||mode==='evidence'||mode==='identity'){
    if(existing){
      if(existing.uiHeader===header)conditionState.rules.splice(conditionState.rules.indexOf(existing),1);
      else existing.enabled=false; // Keep saved-profile rules recoverable.
      renderRules();
    }
  }else unifiedOpenRule(header,mode);
  renderUnifiedMapping();
}
function renderUnifiedMapping(){
  const body=$('unified-decisions-body');if(!body)return;
  body.replaceChildren();
  const rows=state.rows.a||[],b=state.rows.b||[],cross=$('mode').value==='cross';
  for(const rule of conditionState.rules){
    if(rule.enabled&&rule.source==='a'&&rows.some(r=>r.header===rule.header&&!unifiedCoreName.has(r.key)&&!unifiedCoreDob.has(r.key)))unifiedExtraHeaders.add(rule.header);
  }
  $('unified-header-b').textContent=cross?'Detected Header in File B':'Within File A';
  const core=(set)=>rows.filter(r=>set.has(r.key));
  const appendCell=(tr,value)=>{const td=document.createElement('td');if(value instanceof Node)td.append(value);else td.textContent=value;tr.append(td);return td;};
  function defaultRow(kind,keys,title,description,actionLabel){
    const relevant=core(keys),tr=document.createElement('tr');
    const label=appendCell(tr,'');const strong=document.createElement('strong');strong.textContent=title;
    strong.className=kind==='name'?'unified-mapping-default':'unified-mapping-dob';strong.style.paddingLeft='10px';
    label.append(strong);const sub=document.createElement('small');sub.className='unified-sub';sub.textContent=description;label.append(sub);
    appendCell(tr,unifiedChipList(relevant.map(r=>r.header)));
    appendCell(tr,cross?unifiedChipList(relevant.map(r=>r.pairB).filter(Boolean)):'Uses File A only');
    const tag=document.createElement('span');tag.className='unified-tag'+(kind==='dob'?' green':'');tag.textContent=actionLabel;
    appendCell(tr,tag);
    const edit=document.createElement('button');edit.type='button';edit.className='unified-config-btn';edit.textContent='Edit ▾';edit.addEventListener('click',()=>unifiedOpenMapping(kind));appendCell(tr,edit);
    body.append(tr);
  }
  defaultRow('name',unifiedCoreName,'Name · Default','First, Middle, Last, Suffix; missing optional parts allowed','Primary Dedup');
  defaultRow('dob',unifiedCoreDob,'Date of Birth (DOB) · Default','Full DOB or separate Month / Day / Year','Supporting DOB');
  const available=rows.filter(r=>!unifiedCoreName.has(r.key)&&!unifiedCoreDob.has(r.key));
  for(const header of [...unifiedExtraHeaders]){
    const r=available.find(x=>x.header===header);if(!r){unifiedExtraHeaders.delete(header);unifiedFieldModes.delete(unifiedModeKey(header));continue;}
    const tr=document.createElement('tr');const label=appendCell(tr,'');const title=document.createElement('strong');title.textContent=r.header;label.append(title);
    const help=document.createElement('small');help.className='unified-sub';help.textContent=r.key==='none'?(r.suggestion?`Suggested: ${semantic[r.suggestion]||r.suggestion}`:'Original header retained'):`Detected: ${semantic[r.key]||r.key}`;label.append(help);
    appendCell(tr,unifiedChipList([r.header]));
    const pairCell=appendCell(tr,'');
    if(cross){
      const select=document.createElement('select');select.setAttribute('aria-label',`File B header for ${r.header}`);select.add(new Option('No File B header',''));
      for(const target of b)select.add(new Option(target.header,target.header));select.value=r.pairB||'';
      select.addEventListener('change',()=>{r.pairB=select.value;r.pairBManual=Boolean(select.value);r.pairConfidence=100;r.pairMethod='manual';});
      pairCell.append(select);
    }else pairCell.textContent='File A only';
    const actionCell=appendCell(tr,'');const action=document.createElement('select');action.setAttribute('aria-label',`Action for ${r.header}`);
    for(const [value,text] of [['ignore','Ignore for matching (keep in results)'],['evidence','Supporting evidence (reference only)'],['review','For Review — add rule'],['condition','Add Condition — configure rule']])action.add(new Option(text,value));
    if(exactKeys.has(r.key))action.add(new Option('Exact ID identity matching (use with care)','identity'));
    let mode=unifiedFieldModes.get(unifiedModeKey(header))||(exactKeys.has(r.key)?'identity':'ignore');if(unifiedRuleFor(header)?.enabled&&!['review','condition'].includes(mode)&&!unifiedFieldModes.has(unifiedModeKey(header)))mode='condition';
    action.value=mode;action.addEventListener('change',()=>unifiedActionChanged(header,action.value));actionCell.append(action);
    const config=appendCell(tr,'');const edit=document.createElement('button');edit.type='button';edit.className='unified-config-btn';
    edit.textContent=['review','condition'].includes(mode)?'Rule ▾':'Edit ▾';
    edit.addEventListener('click',()=>['review','condition'].includes(mode)?unifiedOpenRule(header,mode):unifiedOpenMapping(header));config.append(edit);
    const remove=document.createElement('button');remove.type='button';remove.className='secondary';remove.textContent='×';remove.title=`Remove ${header} from table (source column is not deleted)`;remove.setAttribute('aria-label',`Remove ${header} from table`);
    remove.addEventListener('click',()=>{unifiedActionChanged(header,'ignore');unifiedExtraHeaders.delete(header);renderUnifiedMapping();});config.append(remove);
    body.append(tr);
  }
  const add=$('unified-add-header'),prior=add.value;add.replaceChildren();add.add(new Option('Choose a header to add…',''));
  for(const r of available.filter(r=>!unifiedExtraHeaders.has(r.header)))add.add(new Option(r.header,r.header));
  if([...add.options].some(o=>o.value===prior))add.value=prior;
  $('unified-add-button').disabled=add.options.length<2;
  updateUnifiedRuleSummary();
  if($('header-mapping-editor')?.open)applyUnifiedEditorFilter();
}
function initUnifiedMapping(){
  $('unified-add-button').addEventListener('click',()=>{
    const header=$('unified-add-header').value;if(!header)return;
    unifiedExtraHeaders.add(header);renderUnifiedMapping();
  });
  $('unified-add-header').addEventListener('change',()=>{$('unified-add-button').disabled=!$('unified-add-header').value;});
  $('open-settings-popup').addEventListener('click',()=>openEditorPopup('comparison-settings-editor'));
  $('open-mapping-popup').addEventListener('click',()=>unifiedOpenMapping(''));
  $('open-conditions-popup').addEventListener('click',()=>openEditorPopup('unified-conditions-editor'));
  document.querySelectorAll('[data-close-editor]').forEach(button=>button.addEventListener('click',()=>closeEditorPopup(button.dataset.closeEditor)));
  for(const id of ['comparison-settings-editor','header-mapping-editor','unified-conditions-editor']){
    const popup=$(id);
    popup.addEventListener('close',()=>{
      if(id==='header-mapping-editor')unifiedEditorFilter='';
      // Restore focus to a visible trigger after closing by ESC, X, or Done.
      $(id==='comparison-settings-editor'?'open-settings-popup':id==='header-mapping-editor'?'open-mapping-popup':'open-conditions-popup').focus({preventScroll:true});
    });
    popup.addEventListener('click',event=>{if(event.target===popup)popup.close();});
  }
}
async function init(){
  if(!checkUICompatibility())return;
  initConditions();initUnifiedMapping();applyMode();applyPreset();setBusy(true);
  $("scan-preset").addEventListener('change',applyPreset);
  $("fuzzy").addEventListener('change',()=>{ $('scan-preset').value=$('fuzzy').checked?'exhaustive':'quick';applyPreset(); });
  $("swap").addEventListener("change",refreshControlStatus);
  $("export-format").addEventListener("change",refreshControlStatus);
  $("mode").addEventListener("change",applyMode);
  $("run").addEventListener("click",run);$("new-session").addEventListener("click",newSession);$("delete-session").addEventListener("click",deleteTemporaryFiles);
  for(const side of ["a","b"]){
    $("file-"+side).addEventListener("change",()=>upload(side));
    $("sheet-"+side).addEventListener("change",()=>selectSheet(side));
    $("retry-"+side).addEventListener("click",()=>upload(side));
    // Do not clear on picker-open: canceling the dialog must preserve selected files.
    // The Retry upload button explicitly supports uploading the same file again.
  }
  $("logout").addEventListener("click",async()=>{try{await api("/api/auth/logout",{method:"POST"});location.href="/login";}catch(error){showMessage(error.message);}});
  try{
    const sid=localStorage.getItem(storageKey);
    if(sid){const response=await fetch(`/api/session/${encodeURIComponent(sid)}`);if(response.ok)state.session=await response.json();else if(![403,404].includes(response.status))throw Error("Could not restore the session. Reload after signing in.");}
    if(!state.session)await newSession();
    else{for(const side of ["a","b"]){if(state.session["file_"+side])displayFile(side,state.session["file_"+side]);}applyMode();}
    setBusy(false);
    if(state.session.last_job_id)await poll(state.session.last_job_id);
    api("/api/auth/visit",{method:"POST"}).catch(()=>{});
    loadConditionProfiles();
  }catch(error){showMessage(error.message);setBusy(false);}
}
init();
