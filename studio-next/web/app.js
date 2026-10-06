'use strict';
const $ = id => document.getElementById(id);
const audio = $('audio');
const esc = text => String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const timeText = seconds => { seconds = Number(seconds) || 0; return `${Math.floor(seconds/60)}:${String(Math.floor(seconds%60)).padStart(2,'0')}`; };
const dateText = value => { const d = new Date(value); return Number.isNaN(d.valueOf()) ? '' : d.toLocaleDateString('en-GB',{day:'numeric',month:'short'}); };
const colors = [['#315a49','#d8daa4','#91b09d'],['#536668','#dfc9ab','#9caeae'],['#625065','#d4b9a6','#a4aa9e'],['#53684b','#c5d5a5','#91a57c'],['#695b42','#dacb9b','#a7b49c'],['#435c74','#cad7b0','#96b6ad']];
function palette(t) { const c=colors[parseInt(t.id.slice(0,4),16)%colors.length]; return `--c1:${c[0]};--c2:${c[1]};--c3:${c[2]}`; }
let tracks=[], local={overrides:{},playlists:[],drafts:[]}, collection='demo', view='library', selectedId=null, playingId=null, queue=[], draftId=null;
let toastTimer, scanTime='', saveChain=Promise.resolve(), loading=false, seeking=false, transferUndo=null, renderJob=null, renderPolling=false;
let preferences={continuous:true,pauseHidden:false,volume:.8};
try { preferences={...preferences,...JSON.parse(localStorage.getItem('yue2-next-preferences')||'{}')}; } catch {}
audio.volume=preferences.volume;
$('volume').value=preferences.volume;
$('continuous').checked=preferences.continuous;
$('pause-hidden').checked=preferences.pauseHidden;
function toast(message) { $('toast').textContent=message; $('toast').hidden=false; clearTimeout(toastTimer); toastTimer=setTimeout(()=>$('toast').hidden=true,3600); }
function track(id) { return tracks.find(t=>t.id===id); }
function savePreferences() { localStorage.setItem('yue2-next-preferences',JSON.stringify(preferences)); }
function saveLocal() {
  const payload=JSON.stringify(local);
  saveChain=saveChain.catch(()=>{}).then(async()=>{
    const result=await fetch('/api/state',{method:'POST',headers:{'Content-Type':'application/json'},body:payload});
    if(!result.ok) throw new Error('Could not save workspace');
  });
  return saveChain;
}
async function loadLibrary(manual=false) {
  if(loading) return;
  loading=true;
  try {
    await saveChain.catch(()=>{});
    const result=await fetch('/api/library'+(manual?'?refresh=1':''));
    if(!result.ok) throw new Error('Library scan failed');
    const data=await result.json();
    const previous=new Set(tracks.map(t=>t.id));
    const hadTracks=tracks.length>0;
    tracks=data.tracks;local=data.state;scanTime=data.scanned_at;
    renderCollections();renderList();renderDrafts();
    if(!selectedId) {selectedId=filteredTracks()[0]?.id||tracks[0]?.id;renderDetail();}
    if(hadTracks) {
      const added=tracks.filter(t=>!previous.has(t.id)).length;
      if(added) toast(`${added} new track${added===1?'':'s'} found · All renders and New & unrated`);
    }
    $('scan-status').textContent=data.warnings.length?data.warnings[0]:'Updated '+new Date(scanTime).toLocaleTimeString('en-GB',{hour:'2-digit',minute:'2-digit'});
    $('library-footnote').textContent=`Select a title to explore. Press play to listen. ${data.plans} folders without audio are excluded.`;
    if(manual) toast('Library refreshed · playback kept in place');
  } catch(error) { $('scan-status').textContent='Library unavailable · use Refresh to retry';toast(error.message); }
  finally {loading=false;}
}
function filteredTracks() {
  let items=tracks.filter(t=>collection==='demo'?t.curated:collection==='favorites'?t.rating>=4:collection==='unrated'?t.rating===null:collection.startsWith('playlist:')?(local.playlists.find(p=>p.id===collection.slice(9))?.tracks||[]).includes(t.id):true);
  const q=$('search').value.trim().toLowerCase();
  if(q) items=items.filter(t=>[t.title,t.style,t.lyrics,t.sound,t.song].join(' ').toLowerCase().includes(q));
  if($('sort').value==='newest') items.sort((a,b)=>b.created.localeCompare(a.created));
  else if($('sort').value==='title') items.sort((a,b)=>a.title.localeCompare(b.title));
  else items.sort((a,b)=>(b.rating??-1)-(a.rating??-1)||b.created.localeCompare(a.created));
  return items;
}
function renderCollections() {
  const defaults=[['demo','Demo selection','◈',tracks.filter(t=>t.curated).length],['all','All renders','▤',tracks.length],['favorites','Favorites','☆',tracks.filter(t=>t.rating>=4).length],['unrated','New & unrated','◷',tracks.filter(t=>t.rating===null).length]];
  const lists=defaults.concat(local.playlists.map(p=>['playlist:'+p.id,p.name,'♫',p.tracks.filter(id=>track(id)).length]));
  $('collections').innerHTML=lists.map(([id,name,icon,count])=>`<button class="collection-button ${collection===id?'active':''}" data-collection="${esc(id)}"><span class="symbol">${icon}</span><span>${esc(name)}</span><span class="count">${count}</span></button>`).join('');
  $('draft-count').textContent=local.drafts.length;
}
function renderList() {
  const items=filteredTracks();
  const names={demo:['Demo selection','Twelve favorites, plus three recent experiments.'],all:['All renders','Every playable track, discovered directly from your outputs.'],favorites:['Favorites','The pieces you rated four or five stars.'],unrated:['New & unrated','Newest first. A place to hear what happens next.']};
  const description=names[collection]||[local.playlists.find(p=>'playlist:'+p.id===collection)?.name||'Playlist','A collection with its own order and mood.'];
  $('collection-title').textContent=description[0];$('collection-description').textContent=description[1];
  document.querySelector('.collection-art span').textContent=items.length;
  $('track-count').textContent=`${items.length} track${items.length===1?'':'s'} · ${timeText(items.reduce((sum,t)=>sum+Number(t.duration||0),0))} total`;
  $('track-list').innerHTML=items.map(t=>`<div class="track-row ${t.id===selectedId?'selected':''} ${t.id===playingId?'playing':''}" role="listitem" data-track="${t.id}"><button class="row-play" data-play="${t.id}" aria-label="${t.id===playingId&&!audio.paused?'Pause':'Play'} ${esc(t.title)}">${t.id===playingId&&!audio.paused?'Ⅱ':'▶'}</button><div class="track-cover" style="${palette(t)}" aria-hidden="true">${esc(t.title.slice(0,1))}</div><button class="row-title" data-select="${t.id}" aria-label="Show details for ${esc(t.title)}"><strong>${esc(t.title)}</strong><small>${esc(dateText(t.created))} · ${esc(t.style||'No style prompt saved')}</small></button><span class="stars ${t.rating===null?'unrated':''}">${t.rating===null?'Unrated':t.rating===0?'0 / 5':'★'.repeat(t.rating)}</span><span class="track-duration">${t.duration?timeText(t.duration):'—'}</span></div>`).join('');
  $('library-empty').hidden=items.length>0;
  $('play-collection').disabled=!items.length;
}
function showView(next) {
  view=next;
  for(const name of ['library','create','drafts']) $(name+'-view').hidden=name!==next;
  document.querySelectorAll('[data-view]').forEach(b=>{b.classList.toggle('active',b.dataset.view===next);b.classList.toggle('selected',b.dataset.view===next);if(b.getAttribute('role')==='tab') b.setAttribute('aria-selected',String(b.dataset.view===next));});
  if(next==='library') renderList();
  if(next==='drafts') renderDrafts();
  // The audio element lives outside all views and is never remounted here.
}
function renderDetail() {
  const t=track(selectedId);
  if(!t) { $('track-detail').innerHTML='<p class="detail-copy">Select a track to see its prompt, lyrics, and musical structure.</p>';return; }
  $('track-detail').innerHTML=`
    <div class="detail-cover" style="${palette(t)}" aria-hidden="true">${esc(t.title.slice(0,1))}</div>
    <div class="detail-eyebrow">${t.curated?'DEMO SELECTION':'OUTPUT LIBRARY'}${t.id===playingId?' · NOW PLAYING':''}</div>
    <div class="detail-title-row"><h2 class="detail-title">${esc(t.title)}</h2><button type="button" class="text-button" id="edit-track-title" aria-label="Edit track title">Edit</button></div>
    <form id="rename-track-form" hidden><label class="field-label">Track title<input id="track-title-input" value="${esc(t.title)}" required maxlength="160"></label><div class="rename-actions"><button type="submit" class="quiet-button">Save title</button><button type="button" class="text-button" id="cancel-track-title">Cancel</button></div></form>
    <div class="detail-meta">${t.duration?timeText(t.duration)+' · ':''}${esc(dateText(t.created))} · ${t.rating===null?'Not rated':t.rating+'/5 stars'}</div>
    <div class="detail-actions"><button class="primary-button" id="detail-play">${t.id===playingId&&!audio.paused?'Ⅱ Pause':'▶ Play'}</button><button class="quiet-button" id="use-as-draft">New draft from track</button></div>
    <details class="transfer-box" open><summary>Send to Create</summary><div class="transfer-controls"><select id="transfer-part" aria-label="What to send to Create"><option value="settings-score">Settings + score</option><option value="settings-style">Settings + style prompt</option><option value="settings">Generation settings only</option><option value="score">ABC score only</option><option value="style">Append style prompt</option><option value="lyrics">Append lyrics</option></select><button class="quiet-button" id="send-to-create">Send →</button></div><p>Changes only the parts you choose.</p></details>
    <div class="rating-control" aria-label="Rate this track">${[1,2,3,4,5].map(n=>`<button class="${t.rating>=n?'on':''}" data-rate="${n}" aria-label="Rate ${n} star${n===1?'':'s'}">★</button>`).join('')}<button data-rate="0" class="clear">0</button><button data-rate="clear" class="clear">Clear</button></div>
    <div class="detail-playlist"><select id="add-playlist" aria-label="Choose playlist"><option value="">Add to a playlist…</option>${local.playlists.map(p=>`<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select><button id="add-to-playlist" class="quiet-button">Add</button></div>
    <section class="detail-section"><div class="detail-section-heading"><h3>Style & production</h3><span><button class="text-button" data-send="style" aria-label="Send style prompt to Create">Send to Create →</button> <button class="text-button" data-copy="style" aria-label="Copy style prompt">Copy</button></span></div><p class="detail-copy">${esc(t.style||'No prompt saved for this track.')}</p></section>
    <section class="detail-section"><div class="detail-section-heading"><h3>Lyrics</h3><span><button class="text-button" data-send="lyrics" aria-label="Send lyrics to Create">Send to Create →</button> <button class="text-button" data-copy="lyrics" aria-label="Copy lyrics">Copy</button></span></div><p class="detail-copy lyric-copy">${esc(t.lyrics||'Instrumental · no lyrics supplied')}</p></section>
    <section class="detail-section"><details><summary>Score & generation settings</summary><button type="button" id="view-sheet" class="quiet-button">View sheet music ↗</button><div class="detail-section-heading"><h3>ABC score</h3><span><button class="text-button" data-send="score" aria-label="Send ABC score to Create">Send to Create →</button> <button class="text-button" data-copy="score" aria-label="Copy ABC score">Copy</button></span></div><pre>${esc(t.score||'No ABC score saved.')}</pre><div class="detail-section-heading"><h3>Generation settings</h3><span><button class="text-button" data-send="settings" aria-label="Send generation settings to Create">Send to Create →</button> <button class="text-button" data-copy="parameters" aria-label="Copy generation settings">Copy</button></span></div><pre>${esc(JSON.stringify(t.parameters,null,2))}</pre><p class="detail-copy">${esc(t.folder)}</p></details></section>
    <section class="detail-section"><h3>Listening notes</h3><textarea id="track-notes" rows="3" aria-label="Listening notes" placeholder="What works? Where could this go?">${esc(t.notes)}</textarea><button id="save-notes" class="quiet-button notes-save">Save notes</button></section>`;
  $('view-sheet').onclick=()=>{$('sheet-title').textContent=t.title+' · sheet music';$('sheet-dialog').showModal();drawScore('library-sheet',t.score);};
  $('edit-track-title').onclick=()=>{$('rename-track-form').hidden=false;$('edit-track-title').hidden=true;$('track-title-input').focus();$('track-title-input').select();};
  $('cancel-track-title').onclick=()=>{$('rename-track-form').hidden=true;$('edit-track-title').hidden=false;};
  $('rename-track-form').onsubmit=async e=>{
    e.preventDefault();const title=$('track-title-input').value.trim();
    if(!title){toast('Enter a track title');return;}
    const previous=local.overrides[t.id];
    local.overrides[t.id]={...previous,title};
    try {await saveLocal();(track(t.id)||t).title=title;renderList();renderDetail();updatePlayer();toast('Title saved');}
    catch(error){if(previous)local.overrides[t.id]=previous;else delete local.overrides[t.id];toast(error.message);}
  };
  $('detail-play').onclick=()=>playTrack(t.id);
  $('use-as-draft').onclick=async()=>{
    try {const saved=await preserveCurrentDraft();fillDraft(t);rememberComposition();showView('create');toast('New draft from track'+(saved?' · your previous composition was saved':''));}catch(e){toast(e.message);}
  };
  $('send-to-create').onclick=()=>sendTrackParts(t,$('transfer-part').value);
  $('track-detail').querySelectorAll('[data-send]').forEach(b=>b.onclick=()=>sendTrackParts(t,b.dataset.send));
  $('track-detail').querySelectorAll('[data-copy]').forEach(b=>b.onclick=async()=>{
    const value=b.dataset.copy==='parameters'?JSON.stringify(t.parameters,null,2):t[b.dataset.copy];
    if(!value){toast('No '+b.dataset.copy+' saved for this track');return;}
    try {await navigator.clipboard.writeText(value);toast('Copied to clipboard');}
    catch {$('copy-text').value=value;$('copy-dialog').showModal();$('copy-text').select();}
  });
  $('add-to-playlist').onclick=async()=>{
    const p=local.playlists.find(p=>p.id===$('add-playlist').value);
    if(!p){toast('Create or choose a playlist first');return;}
    if(!p.tracks.includes(t.id))p.tracks.push(t.id);
    await persist('Added to '+p.name);renderCollections();renderList();
  };
  $('save-notes').onclick=async()=>{const notes=$('track-notes').value;local.overrides[t.id]={...local.overrides[t.id],notes};t.notes=notes;await persist('Listening notes saved');};
  $('track-detail').querySelectorAll('[data-rate]').forEach(b=>b.onclick=async()=>{
    const rating=b.dataset.rate==='clear'?null:Number(b.dataset.rate);
    const current=track(t.id)||t,previousRating=current.rating,previousOverride=local.overrides[t.id];
    local.overrides[t.id]={...previousOverride,rating};current.rating=rating;
    renderDetail();renderCollections();renderList();
    try {await saveLocal();toast('Rating saved');}
    catch(error){
      if(local.overrides[t.id]?.rating===rating){
        if(previousOverride)local.overrides[t.id]=previousOverride;else delete local.overrides[t.id];
        (track(t.id)||t).rating=previousRating;renderDetail();renderCollections();renderList();
      }
      toast(error.message);
    }
  });
}
async function persist(message) {try {await saveLocal();toast(message);}catch(e){toast(e.message);}}
function revealPlaying() {
  if(!track(playingId)) {toast('Choose a track and press play first');return;}
  selectedId=playingId;showView('library');
  if(!filteredTracks().some(t=>t.id===playingId)) {collection=track(playingId).curated?'demo':'all';$('search').value='';renderCollections();}
  renderList();renderDetail();
  document.querySelector(`[data-track="${playingId}"]`)?.scrollIntoView({block:'center',behavior:'smooth'});
}
async function playTrack(id, useQueue=false) {
  const t=track(id);if(!t)return;
  if(playingId===id){if(audio.paused){try{await audio.play();}catch{toast('Press play to start audio');}}else audio.pause();return;}
  if(!useQueue)queue=filteredTracks().map(t=>t.id);
  if(!queue.includes(id))queue.push(id);
  playingId=id;selectedId=id;
  audio.src=t.audio;audio.load();
  updatePlayer();renderList();renderDetail();
  try {await audio.play();}catch {toast('Press play to start audio');}
}
function updatePlayer() {
  const t=track(playingId);
  $('playing-title').textContent=t?.title||'Choose something to listen to';
  $('playing-subtitle').textContent=t?`${audio.paused?'Paused':'Playing'} · ${t.curated?'Demo selection':'Output library'}`:'Your player stays with you across views';
  $('toggle-play').textContent=audio.paused?'▶':'Ⅱ';$('toggle-play').setAttribute('aria-label',audio.paused?'Play':'Pause');
  $('elapsed').textContent=timeText(audio.currentTime);
  $('duration').textContent=timeText(Number.isFinite(audio.duration)?audio.duration:t?.duration);
  if(!seeking)$('seek').value=Number.isFinite(audio.duration)&&audio.duration>0?audio.currentTime/audio.duration*100:0;
}
function stepTrack(step) {
  if(!queue.length){queue=filteredTracks().map(t=>t.id);}
  if(!queue.length)return;
  const index=queue.indexOf(playingId);
  const next=(Math.max(index,0)+step+queue.length)%queue.length;
  playTrack(queue[next],true);
}
audio.addEventListener('play',()=>{updatePlayer();renderList();renderDetail();});
audio.addEventListener('pause',()=>{updatePlayer();renderList();renderDetail();storePosition();});
audio.addEventListener('loadedmetadata',updatePlayer);
audio.addEventListener('timeupdate',()=>{updatePlayer();storePosition();});
audio.addEventListener('ended',()=>{if(preferences.continuous&&queue.length&&queue.indexOf(playingId)<queue.length-1)stepTrack(1);});
audio.addEventListener('error',()=>toast('Audio is unavailable. Try Refresh library or another track.'));
function storePosition(){if(playingId)localStorage.setItem('yue2-next-position',JSON.stringify({id:playingId,position:audio.currentTime,queue}));}
$('toggle-play').onclick=()=>{if(playingId)playTrack(playingId);else if(filteredTracks()[0])playTrack(filteredTracks()[0].id);};
$('previous').onclick=()=>{if(audio.currentTime>3)audio.currentTime=0;else stepTrack(-1);};$('next').onclick=()=>stepTrack(1);
$('seek').oninput=()=>{seeking=true;$('elapsed').textContent=timeText(Number($('seek').value)/100*audio.duration);};
$('seek').onchange=()=>{if(Number.isFinite(audio.duration))audio.currentTime=Number($('seek').value)/100*audio.duration;seeking=false;};
$('volume').oninput=()=>{preferences.volume=Number($('volume').value);audio.volume=preferences.volume;savePreferences();};
$('continuous').onchange=()=>{preferences.continuous=$('continuous').checked;savePreferences();};
$('pause-hidden').onchange=()=>{preferences.pauseHidden=$('pause-hidden').checked;savePreferences();};
document.addEventListener('visibilitychange',()=>{if(document.hidden&&preferences.pauseHidden)audio.pause();if(!document.hidden)loadLibrary();});
$('show-playing').onclick=revealPlaying;$('player-reveal').onclick=revealPlaying;
$('playback-settings').onclick=()=>$('playback-popover').hidden=!$('playback-popover').hidden;
$('track-list').onclick=e=>{const play=e.target.closest('[data-play]'),select=e.target.closest('[data-select]');if(play)playTrack(play.dataset.play);else if(select){selectedId=select.dataset.select;renderList();renderDetail();}};
$('collections').onclick=e=>{const b=e.target.closest('[data-collection]');if(b){collection=b.dataset.collection;$('sort').value=['all','unrated'].includes(collection)?'newest':'best';showView('library');renderCollections();renderList();}};
document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>showView(b.dataset.view));
$('search').oninput=renderList;$('sort').onchange=renderList;
$('refresh').onclick=()=>loadLibrary(true);
$('play-collection').onclick=()=>{const first=filteredTracks()[0];if(first){queue=filteredTracks().map(t=>t.id);if(first.id===playingId&&audio.paused)audio.play();else if(first.id!==playingId)playTrack(first.id,true);}};
$('new-playlist').onclick=()=>{$('playlist-name').value='';$('playlist-dialog').showModal();};
$('cancel-playlist').onclick=()=>$('playlist-dialog').close();
$('playlist-form').onsubmit=async e=>{e.preventDefault();const name=$('playlist-name').value.trim();if(!name)return;local.playlists.push({id:crypto.randomUUID(),name,tracks:[]});await persist('Playlist created');$('playlist-dialog').close();renderCollections();renderDetail();};
const controls=[
  ['Score planning',[['score_temp','Exploration · temperature',.75,.1,2,.01],['score_top_p','Top-P',.9,.1,1,.01],['score_rep_pen','Repetition penalty',1.005,1,1.3,.005],['score_top_k','Candidate pool · Top-K',30,5,200,1],['score_pen_win','Penalty window',100,1,100,1]]],
  ['Vocal delivery & arrangement',[['sem_temp','Wildness · temperature',1.15,.1,2,.01],['sem_top_p','Top-P',.95,.1,1,.01],['rep_pen','Repetition penalty',1.18,1,1.5,.005],['sem_top_k','Candidate pool · Top-K',100,5,400,1],['sem_pen_win','Penalty window',50,1,100,1],['sem_min_tokens','Minimum tokens',200,0,2000,1],['sem_max_tokens','Maximum tokens',9000,1000,14000,1]]],
  ['Decoding',[['cfg_scale','Guidance · CFG',1,1,3,.1]]]
];
$('advanced-controls').innerHTML=controls.map(([title,items])=>`<section class="controls-group"><h3>${title}</h3><div class="controls-grid">${items.map(([key,label,value,min,max,step])=>`<label class="field-label">${label}<input id="${key}" name="${key}" type="number" value="${value}" min="${min}" max="${max}" step="${step}"></label>`).join('')}</div></section>`).join('');
const defaults=Object.fromEntries(controls.flatMap(([,items])=>items.map(([key,,value])=>[key,value])));
Object.assign(defaults,{seed:404,flow_steps:12,cot_mode:'full'});
function captureComposition() {
  return {draftId,values:Object.fromEntries(Array.from($('composition-form').querySelectorAll('input,textarea,select')).map(e=>[e.id,e.value]))};
}
function rememberComposition() {localStorage.setItem('yue2-next-composition',JSON.stringify(captureComposition()));}
function restoreComposition(snapshot) {
  if(!snapshot?.values)return;
  draftId=snapshot.draftId||null;
  for(const [id,value]of Object.entries(snapshot.values))if($(id))$(id).value=value;
}
async function sendTrackParts(t,part) {
  const previous=captureComposition();
  let changed=false;
  if(['settings','settings-score','settings-style'].includes(part)) {
    for(const [key,value]of Object.entries(t.parameters||{}))if(Object.hasOwn(defaults,key)&&$(key)&&value!==null){$(key).value=value;changed=true;}
  }
  if((part==='score'||part==='settings-score')&&t.score){$('draft-score').value=t.score;changed=true;}
  if((part==='style'||part==='settings-style')&&t.style){$('draft-style').value=[$('draft-style').value.trim(),t.style].filter(Boolean).join('\n\n');changed=true;}
  if(part==='lyrics'&&t.lyrics){$('draft-lyrics').value=[$('draft-lyrics').value.trim(),t.lyrics].filter(Boolean).join('\n\n');changed=true;}
  if(!changed){toast('This track has no saved '+part.replace('-',' + '));return;}
  transferUndo={draftId:previous.draftId,values:Object.fromEntries(Object.entries(previous.values).filter(([id,value])=>$(id).value!==value))};
  const labels={'settings-score':'Settings and score sent','settings-style':'Settings sent and style prompt appended','settings':'Generation settings sent','score':'ABC score sent','style':'Style prompt appended','lyrics':'Lyrics appended'};
  $('transfer-message').textContent=labels[part]+' from '+t.title;
  $('transfer-status').hidden=false;rememberComposition();showView('create');if($('draft-sheet-details').open)drawScore('draft-sheet',$('draft-score').value);
  toast(labels[part]+' · your other fields are kept');
}
async function preserveCurrentDraft() {
  const hasText=['draft-title','draft-style','draft-lyrics','draft-score'].some(id=>$(id).value.trim());
  const hasSettings=Object.entries(defaults).some(([key,value])=>$(key).value!==String(value));
  if(!hasText&&!hasSettings)return false;
  const current=collectDraft();draftId=current.id;
  const index=local.drafts.findIndex(d=>d.id===current.id);
  if(index<0)local.drafts.unshift(current);else local.drafts[index]=current;
  await saveLocal();renderDrafts();return true;
}
$('undo-transfer').onclick=()=>{if(transferUndo){restoreComposition(transferUndo);transferUndo=null;$('transfer-status').hidden=true;rememberComposition();if($('draft-sheet-details').open)drawScore('draft-sheet',$('draft-score').value);toast('Transfer undone');}};
$('close-copy').onclick=()=>$('copy-dialog').close();
function fillDraft(t=null) {
  draftId=t?.draftId||null;
  $('draft-title').value=t?.title||'';$('draft-style').value=t?.style||'';$('draft-lyrics').value=t?.lyrics||'';$('draft-score').value=t?.score||'';
  for(const [key,value]of Object.entries({...defaults,...t?.parameters}))if($(key))$(key).value=value;
  $('draft-status').textContent='';$('transfer-status').hidden=true;transferUndo=null;if($('draft-sheet-details').open)drawScore('draft-sheet',$('draft-score').value);
}
function collectDraft() {
  const parameters={};for(const key of Object.keys(defaults))parameters[key]=key==='cot_mode'?$(key).value:Number($(key).value);
  if(parameters.sem_min_tokens>parameters.sem_max_tokens)throw new Error('Minimum tokens must be less than maximum tokens.');
  return {id:draftId||crypto.randomUUID(),title:$('draft-title').value.trim()||'Untitled composition',style:$('draft-style').value.trim(),lyrics:$('draft-lyrics').value.trim(),score:$('draft-score').value,parameters,updated:new Date().toISOString()};
}
function renderDrafts() {
  $('draft-count').textContent=local.drafts.length;
  $('draft-list').innerHTML=local.drafts.length?local.drafts.map(d=>`<article class="draft-card"><div><strong>${esc(d.title)}</strong><small>Saved ${dateText(d.updated)}</small></div><button class="quiet-button" data-draft="${esc(d.id)}">Open draft →</button></article>`).join(''):'<p class="empty-state">Your next piece starts in Create. Save it here whenever you are ready.</p>';
}
$('draft-list').onclick=e=>{const b=e.target.closest('[data-draft]');if(b){const d=local.drafts.find(d=>d.id===b.dataset.draft);fillDraft({...d,draftId:d.id});rememberComposition();showView('create');}};
$('new-draft').onclick=async()=>{try{await preserveCurrentDraft();fillDraft();rememberComposition();showView('create');}catch(e){toast(e.message);}};
$('composition-form').onsubmit=async e=>{e.preventDefault();try{const draft=collectDraft();draftId=draft.id;const index=local.drafts.findIndex(d=>d.id===draft.id);if(index<0)local.drafts.unshift(draft);else local.drafts[index]=draft;await saveLocal();rememberComposition();renderDrafts();$('draft-status').textContent='Draft saved · '+draft.title;toast('Draft saved');}catch(error){toast(error.message);}};
function minutesText(seconds) {return Math.max(0,Number(seconds)/60).toFixed(1)+' min';}
function drawScore(id,score) {
  const paper=$(id);paper.replaceChildren();
  if(!score?.trim()){paper.textContent='No score yet. Paste ABC or inspect a completed track.';return;}
  if(!window.ABCJS){paper.textContent='The notation renderer is unavailable. ABC text is still editable.';return;}
  try {window.ABCJS.renderAbc(id,score,{responsive:'resize',staffwidth:800,add_classes:true});if(!paper.querySelector('svg'))paper.textContent='No readable musical notation found in this ABC.';}
  catch {paper.textContent='This score could not be drawn. You can still inspect and edit its ABC text.';}
}
let scorePreviewTimer;
$('close-sheet').onclick=()=>$('sheet-dialog').close();
$('draft-sheet-details').addEventListener('toggle',()=>{if($('draft-sheet-details').open)drawScore('draft-sheet',$('draft-score').value);});
$('draft-score').addEventListener('input',()=>{clearTimeout(scorePreviewTimer);scorePreviewTimer=setTimeout(()=>{if($('draft-sheet-details').open)drawScore('draft-sheet',$('draft-score').value);},500);});
function updateRenderClock() {
  if(!renderJob||renderJob.state==='idle')return;
  const active=['connecting','rendering'].includes(renderJob.state);
  const elapsed=active&&renderJob.started_at?Date.now()/1000-renderJob.started_at:renderJob.elapsed_seconds||0;
  let text=(active?'Elapsed ':'Finished in ')+minutesText(elapsed);
  const prediction=renderJob.estimated_total;
  if(active&&prediction) {
    if(elapsed>prediction.high)text+=' · longer than previous comparable renders';
    else text+=' · roughly '+minutesText(Math.max(0,prediction.low-elapsed))+'–'+minutesText(Math.max(0,prediction.high-elapsed))+' remaining (estimated '+minutesText(prediction.low)+'–'+minutesText(prediction.high)+' total)';
  }else if(active)text+=' · learning finish estimates ('+(renderJob.estimate_samples||0)+'/3 comparable renders)';
  $('render-timing').textContent=text;
}
function displayRender(job) {
  renderJob=job;
  const active=['connecting','rendering'].includes(job.state);
  $('create-track').disabled=active;
  $('create-track').textContent=active?'Creating…':'Create';
  $('render-status').hidden=job.state==='idle';
  $('render-title').textContent=job.title||'Current render';
  $('render-message').textContent=job.message||'';
  updateRenderClock();
  const stages=job.stages||[];
  const completed=stages.filter(s=>s.status==='done'||s.status==='skipped').length;
  $('render-summary').textContent=completed+' of '+stages.length+' stages complete · details';
  $('render-stages').innerHTML=stages.map(s=>`<li class="stage-${s.status}"><span>${s.status==='done'?'✓':s.status==='skipped'?'—':s.status==='active'?'◉':s.status==='error'?'!':'○'}</span> ${esc(s.label)} <small>${esc(s.status)}</small></li>`).join('');
  $('render-engine-note').textContent='Configured flow steps: '+(job.configured_flow_steps??'—')+'. Engine progress is approximate; stage completion does not predict time remaining.';
  $('render-progress').hidden=!active;
  if(typeof job.progress==='number')$('render-progress').value=job.progress;else $('render-progress').removeAttribute('value');
  $('play-render').hidden=job.state!=='complete';
}
async function pollRender() {
  if(renderPolling)return;
  renderPolling=true;
  try {
    const response=await fetch('/api/render');if(!response.ok)return;
    const next=await response.json();const previous=renderJob;
    displayRender(next);
    if(next.state==='complete'&&previous?.state!=='complete'){await loadLibrary(true);toast('Your new track is ready');}
  }catch{}finally{renderPolling=false;}
}
$('create-track').onclick=async()=>{
  if(!$('composition-form').reportValidity())return;
  const button=$('create-track');button.disabled=true;
  try {
    const composition=collectDraft();
    if(!composition.style){toast('Add a style and production prompt first');return;}
    await preserveCurrentDraft();
    const response=await fetch('/api/render',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(composition)});
    const job=await response.json();if(!response.ok)throw new Error(job.error||'Could not start creating');
    displayRender(job);toast('Creating your track · listening continues');
  }catch(error){toast(error.message);}finally{if(!['connecting','rendering'].includes(renderJob?.state))button.disabled=false;}
};
$('play-render').onclick=async()=>{await loadLibrary(true);if(track(renderJob?.track_id)){collection='all';$('search').value='';renderCollections();showView('library');await playTrack(renderJob.track_id);}else toast('Refresh the library to find the new track');};
pollRender();setInterval(pollRender,2500);setInterval(updateRenderClock,1000);
$('export-request').onclick=()=>{
  if(!$('composition-form').reportValidity())return;
  try {
    const draft=collectDraft();
    const request={track_title:draft.title,style:draft.style,lyrics:draft.lyrics,abc:draft.score,parameters:draft.parameters};
    const url=URL.createObjectURL(new Blob([JSON.stringify(request,null,2)],{type:'application/json'}));
    const a=document.createElement('a');a.href=url;a.download=draft.title.toLowerCase().replace(/[^a-z0-9]+/g,'-')+'.request.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),10000);
    toast('Request exported · load it using Open files in the existing studio');
  }catch(error){toast(error.message);}
};
document.addEventListener('keydown',e=>{if(e.code==='Space'&&!['INPUT','TEXTAREA','SELECT','BUTTON','SUMMARY'].includes(document.activeElement.tagName)&&!$('playlist-dialog').open){e.preventDefault();$('toggle-play').click();}});
fillDraft();
try{restoreComposition(JSON.parse(localStorage.getItem('yue2-next-composition')||'null'));}catch{}
function compositionEdited() {$('transfer-status').hidden=true;transferUndo=null;rememberComposition();}
$('composition-form').addEventListener('input',compositionEdited);
$('composition-form').addEventListener('change',compositionEdited);
(async()=>{await loadLibrary();try{const saved=JSON.parse(localStorage.getItem('yue2-next-position')||'null');if(saved&&track(saved.id)){playingId=saved.id;queue=(saved.queue||[]).filter(id=>track(id));audio.src=track(saved.id).audio;audio.addEventListener('loadedmetadata',()=>{audio.currentTime=Math.min(saved.position||0,audio.duration||0);updatePlayer();},{once:true});updatePlayer();renderList();}}catch{}})();
setInterval(()=>loadLibrary(),30000);
