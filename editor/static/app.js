"use strict";
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const copy = (value) => JSON.parse(JSON.stringify(value));
const escapeHTML = (value) => String(value).replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
let state, draft, geometry, catalog, layer = 0, selected = 0, view = "keyboard", recording = false, busy = false, staleDraft = false, draftRevision;
const storageKey = "skree-control-draft-v1";
const symbols = {ESC:"Esc",TAB:"Tab",LSHFT:"Shift",RSHFT:"Shift",LCTRL:"Ctrl",RCTRL:"Ctrl",LALT:"Alt",RALT:"Alt",LGUI:"Super",RGUI:"Super",SPACE:"Space",BSPC:"Bksp",RET:"Enter",DEL:"Del",GRAVE:"`",EQUAL:"=",MINUS:"−",SEMI:";",SQT:"'",COMMA:",",DOT:".",FSLH:"/",BSLH:"\\",LBKT:"[",RBKT:"]",LEFT:"←",RIGHT:"→",UP:"↑",DOWN:"↓",CAPS:"Caps",HOME:"Home",END:"End",PG_UP:"PgUp",PG_DN:"PgDn",INS:"Ins"};
const shifted = {"1":"!","2":"@","3":"#","4":"$","5":"%","6":"^","7":"&","8":"*","9":"(","0":")","=":"+","−":"_",";":":","'":'"',"`":"~",",":"<",".":">","/":"?","\\":"|","[":"{","]":"}"};
const behaviors = {kp:"Type a key",mo:"Hold a layer",to:"Switch layer",tog:"Toggle layer",mkp:"Mouse button",trans:"Transparent",none:"Disabled",bt:"Bluetooth",rgb_ug:"Lighting",bootloader:"Bootloader",sys_reset:"Restart"};
const mouseNames = {LCLK:"Left click",RCLK:"Right click",MCLK:"Middle click",MB4:"Back",MB5:"Forward"};
const mouseShort = {LCLK:"L click",RCLK:"R click",MCLK:"M click",MB4:"Back",MB5:"Fwd"};
const rgbNames = {RGB_TOG:"Toggle lighting",RGB_ON:"Lighting on",RGB_OFF:"Lighting off",RGB_EFF:"Next effect",RGB_EFR:"Previous effect",RGB_HUI:"Hue +",RGB_HUD:"Hue −",RGB_SAI:"Saturation +",RGB_SAD:"Saturation −",RGB_BRI:"Brightness +",RGB_BRD:"Brightness −",RGB_SPI:"Effect speed +",RGB_SPD:"Effect speed −"};
const titles = {keyboard:["Keyboard","Select a key to change what it does."],trackball:["Trackball","Tune movement and scrolling. Save to apply on this computer."],history:["History","Your configuration, versioned in Git."]};
const settingNames = {swapSlashes:"Slash / backslash swap",holdToScroll:"Hold to scroll",scrollButton:"Scroll button",middleClick:"Middle click",scrollFactor:"Scroll speed",naturalScroll:"Natural scrolling",sensitivity:"Sensitivity",accelProfile:"Acceleration",declaredCpi:"Desktop sensor resolution",cpi:"Firmware sensor resolution",autoMouseTimeoutMs:"Mouse-layer duration"};

function toast(message, error=false) {
  const node=$("#toast");node.textContent=message;node.hidden=false;node.classList.toggle("error",error);
  clearTimeout(toast.timer);toast.timer=setTimeout(()=>node.hidden=true,error?9000:5500);
}
function showError(message) { const node=$("#connection-error");node.textContent=message;node.hidden=!message; }
function isDirty() { return state && JSON.stringify(draft)!==JSON.stringify(state.config); }
function persistDraft() { try { if(isDirty())localStorage.setItem(storageKey,JSON.stringify({revision:draftRevision,config:draft}));else localStorage.removeItem(storageKey); } catch {} }
function localChanges() {
  if(!state)return [];
  const changes=[];
  draft.firmware.layers.forEach((l,li)=>{const old=state.config.firmware.layers[li];if(l.name!==old.name)changes.push({scope:"firmware",label:`Layer ${li} name`,before:old.name,after:l.name});l.bindings.forEach((binding,i)=>{if(binding!==old.bindings[i])changes.push({scope:"firmware",label:`${l.name} · key ${i+1}`,before:old.bindings[i],after:binding});});});
  [["firmware",state.config.firmware.trackball,draft.firmware.trackball],["desktop",state.config.hyprland,draft.hyprland]].forEach(([scope,before,after])=>Object.keys(after).forEach(k=>{if(after[k]!==before[k])changes.push({scope,label:k,before:before[k],after:after[k]});}));
  return changes;
}
async function api(path,payload) {
  const options=payload===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json","X-Skree-CSRF":state.csrfToken},body:JSON.stringify({...payload,revision:state.revision})};
  const response=await fetch(path,options);const body=await response.json();
  if(!response.ok)throw new Error(body.error||`Request failed (${response.status})`);
  return body;
}
function effectiveBinding(index) {
  let result=draft.firmware.layers[layer].bindings[index];
  if(result==="&trans"&&layer!==0)result=draft.firmware.layers[0].bindings[index];
  return result;
}
function labelFor(binding,short=false) {
  const [behavior,arg,extra]=binding.split(/\s+/);
  if(behavior==="&kp"&&["C_PLAY_PAUSE","C_MUTE","C_VOL_DN","C_VOL_UP"].includes(arg))return ({C_PLAY_PAUSE:short?"Play":"Play/Pause",C_MUTE:"Mute",C_VOL_DN:short?"Vol −":"Volume down",C_VOL_UP:short?"Vol +":"Volume up"})[arg];
  if(behavior==="&kp")return symbols[arg]||(/^N[0-9]$/.test(arg)?arg.slice(1):arg);
  if(behavior==="&mo")return `L${arg}`;
  if(behavior==="&to")return `→ L${arg}`;
  if(behavior==="&tog"&&arg==="4")return short?"Scrl":"Scroll mode";
  if(behavior==="&tog")return `↔ L${arg}`;
  if(behavior==="&mkp")return (short?mouseShort:mouseNames)[arg]||arg;
  if(behavior==="&trans")return "▽";
  if(behavior==="&none")return "—";
  if(behavior==="&bootloader")return "Boot";
  if(behavior==="&sys_reset")return "Reset";
  if(behavior==="&bt")return {BT_CLR:"BT clear",BT_NXT:"BT next",BT_PRV:"BT prev",BT_SEL:`BT ${Number(extra)+1}`}[arg]||"BT";
  if(behavior==="&rgb_ug")return {RGB_TOG:"RGB on/off",RGB_EFF:"RGB next",RGB_EFR:"RGB prev"}[arg]||rgbNames[arg]||"RGB";
  return binding;
}
function locationFor(index) {
  if(index<48)return `${Math.floor(index/12)+1}${["st","nd","rd","th"][Math.floor(index/12)]} row · ${index%12<6?"left":"right"} hand`;
  if(index<52)return `${index<50?"Left":"Right"} arrow cluster`;
  return `${[52,53,54,56,57,58].includes(index)?"Left":"Right"} thumb cluster`;
}
function renderKeyboard() {
  $("#layer-tabs").innerHTML=draft.firmware.layers.map((item,i)=>i===2&&!$("#show-all-layers").checked?"":`<button type="button" class="layer-tab ${i===layer?"active":""}" role="tab" aria-selected="${i===layer}" aria-controls="keyboard-hands" data-layer="${i}"><span>0${i}</span>${escapeHTML(item.name)}</button>`).join("");
  $("#keyboard-hands").innerHTML=[0,1].map(side=>{
    const outline=side===0?'M12 67 Q160 9 317 69 L324 251 Q316 279 294 283 L306 439 Q237 456 165 385 L20 276 Z':'M12 69 Q160 9 317 67 L312 276 L170 385 Q100 456 31 439 L44 283 Q17 279 10 251 Z';
    const keys=geometry.keys.filter(k=>k.side===side).map(k=>{
      const raw=draft.firmware.layers[layer].bindings[k.i],binding=effectiveBinding(k.i),label=labelFor(binding,true),baseLabel=labelFor(draft.firmware.layers[0].bindings[k.i]);
      const klass=["key",binding.startsWith("&mo")||binding.startsWith("&to")||binding.startsWith("&tog")?"key-layer":"",binding.startsWith("&mkp")?"key-mouse":"",k.i>=48&&k.i<=51?"key-arrow":"",raw==="&trans"?"key-transparent":"",k.i===selected?"selected":"",raw!==state.config.firmware.layers[layer].bindings[k.i]?"changed":"",k.i===46?"hardware-fault":""].filter(Boolean).join(" ");
      const sup=layer===0&&binding.startsWith("&kp")?shifted[label]:binding.startsWith("&mo")?"hold":"";
      const labelMarkup=label.includes(" ")?label.split(" ").map(x=>`<span class="two-line">${escapeHTML(x)}</span>`).join(""):`<span>${escapeHTML(label)}</span>`;
      return `<button type="button" class="${klass}" data-key="${k.i}" aria-label="${escapeHTML(`${baseLabel}, position ${k.i+1}: ${labelFor(binding)}${raw==="&trans"?", transparent":""}${k.i===46?", known unresponsive position":""}`)}" aria-pressed="${k.i===selected}">${sup?`<span class="shift-symbol">${escapeHTML(sup)}</span>`:""}${labelMarkup}</button>`;
    }).join("");
    return `<div class="keyboard-hand ${side?"right":"left"}"><div class="hand-heading">${side?"RIGHT":"LEFT"} HAND<span>${side?"30":"32"} KEYS</span></div><div class="hand-layout"><svg class="hand-case" viewBox="-18 34 358 450" aria-hidden="true"><path d="${outline}"/></svg><div class="palm-rest" aria-hidden="true">PALM REST</div>${side?'<div class="trackball-ball" aria-hidden="true"></div><span class="trackball-caption">TRACKBALL</span>':""}${keys}</div></div>`;
  }).join("");
  $$("[data-layer]").forEach(button=>button.addEventListener("click",()=>{layer=Number(button.dataset.layer);recording=false;renderKeyboard();renderInspector();}));
  $$("[data-key]").forEach(button=>{
    const k=geometry.keys.find(item=>item.i===Number(button.dataset.key));
    button.style.left=((k.x+18)/358*100)+"%";
    button.style.top=((k.y-34)/450*100)+"%";
    button.style.transform=`rotate(${k.a}deg)`;
    button.addEventListener("click",()=>{selected=Number(button.dataset.key);recording=false;renderKeyboard();renderInspector();});
  });
}
function renderInspector() {
  if(view==="keyboard")$("#page-description").textContent=titles.keyboard[1];
  const binding=draft.firmware.layers[layer].bindings[selected];
  const [behavior,arg,extra]=binding.split(/\s+/);const kind=behavior.slice(1);
  $("#selected-index").textContent=`POSITION ${String(selected+1).padStart(2,"0")}`;
  $("#selected-preview").textContent=labelFor(effectiveBinding(selected),true);
  $("#selected-preview").style.fontSize=labelFor(effectiveBinding(selected),true).length>8?"13px":"";
  $("#selected-location").textContent=locationFor(selected);
  $("#behavior").value=kind;
  let args="";
  const option=(value,label)=>`<option value="${escapeHTML(value)}" ${value===arg?"selected":""}>${escapeHTML(label)}</option>`;
  if(kind==="kp")args=`<label class="field-label" for="key-symbol">Key</label><input type="text" id="key-symbol" list="keycode-list" value="${escapeHTML(arg||"A")}" autocomplete="off" spellcheck="false"><button type="button" id="record-key" class="button subtle record-button ${recording?"recording":""}">${recording?"Press a key…":"Record a single key"}</button><p class="argument-help">Search a ZMK symbol, such as F1, LBKT, or CAPS. Modifier expressions such as LC(C) are also supported.</p>`;
  if(["mo","to","tog"].includes(kind))args=`<label class="field-label" for="binding-value">Layer</label><select id="binding-value">${draft.firmware.layers.map((l,i)=>option(String(i),`${i} · ${l.name}`)).join("")}</select>`;
  if(kind==="mkp")args=`<label class="field-label" for="binding-value">Mouse button</label><select id="binding-value">${Object.entries(mouseNames).map(([code,name])=>option(code,name)).join("")}</select>`;
  if(kind==="rgb_ug")args=`<label class="field-label" for="binding-value">Lighting action</label><select id="binding-value">${catalog.rgb.map(code=>option(code,rgbNames[code]||code)).join("")}</select>`;
  if(kind==="bt")args=`<label class="field-label" for="binding-value">Bluetooth action</label><select id="binding-value">${Object.entries({BT_NXT:"Next profile",BT_PRV:"Previous profile",BT_CLR:"Clear current pairing",BT_SEL:"Choose profile"}).map(([code,name])=>option(code,name)).join("")}</select>${arg==="BT_SEL"?`<label class="field-label" for="bt-profile">Profile</label><select id="bt-profile">${[0,1,2,3,4].map(i=>`<option value="${i}" ${String(i)===extra?"selected":""}>Profile ${i+1}</option>`).join("")}</select>`:""}`;
  $("#binding-arguments").innerHTML=args;
  $("#binding-source").textContent=binding;
  $("#binding-hint").textContent=kind==="trans"?`Transparent: the next active layer supplies this key. Base preview: ${labelFor(draft.firmware.layers[0].bindings[selected])}.`:kind==="bootloader"?"This key enters firmware update mode. It will not type a character.":kind==="bt"&&arg==="BT_CLR"?"This clears the current Bluetooth profile’s pairing when used on the keyboard.":kind==="mkp"?`This key sends ${mouseNames[arg]?.toLowerCase()||"a mouse button"} while this layer is active.`:kind==="mo"?`Keep this key held to use ${draft.firmware.layers[Number(arg)].name}.`:`${behaviors[kind]||kind}: ${labelFor(binding)}.`;
  $("#reset-key").disabled=binding===state.config.firmware.layers[layer].bindings[selected];
  if($("#key-symbol"))$("#key-symbol").addEventListener("change",event=>assign(`&kp ${event.target.value.trim().toUpperCase()}`));
  if($("#record-key"))$("#record-key").addEventListener("click",()=>{recording=!recording;renderInspector();});
  if($("#binding-value"))$("#binding-value").addEventListener("change",event=>assign(`&${kind} ${event.target.value}${kind==="bt"&&event.target.value==="BT_SEL"?" 0":""}`));
  if($("#bt-profile"))$("#bt-profile").addEventListener("change",event=>assign(`&bt BT_SEL ${event.target.value}`));
}
function assign(binding) {
  if(binding.startsWith("&kp ")) {
    const value=binding.slice(4);
    if(!catalog.keycodes.includes(value)&&!/^((LC|RC|LS|RS|LA|RA|LG|RG)\(){1,4}[A-Z][A-Z0-9_]*\){1,4}$/.test(value)){toast("Choose a ZMK key symbol from the list, or a valid modifier expression.",true);return;}
  }
  draft.firmware.layers[layer].bindings[selected]=binding;changed();renderKeyboard();renderInspector();
}
function changed() { persistDraft();renderStatus(); }
function renderStatus() {
  if(state.pendingPush && !$("#retry-push")) {
    if($("#connection-error").hidden)showError("This branch has local commits waiting to reach GitHub.");
    const retry=document.createElement("button");retry.id="retry-push";retry.className="button secondary";retry.textContent="Retry push";retry.addEventListener("click",retryPush);$("#connection-error").append(document.createElement("br"),retry);
  }
  const dirty=isDirty(),count=localChanges().length;
  $("#change-count").textContent=count;
  $("#change-count").hidden=!count;
  $("#discard").hidden=!dirty&&!staleDraft;
  $("#save-state").classList.toggle("unsaved",dirty);
  $("#save-state").innerHTML=`<i></i>${dirty?`${count} unsaved change${count===1?"":"s"}`:state.dirty?"Checkout has file edits":state.pendingPush?"Local commits awaiting push":"Saved to GitHub"}`;
  $("#review").disabled=busy||!dirty||staleDraft||Boolean(state.sourceDrift?.length);
  $("#apply-desktop").disabled=busy||dirty||state.dirty||Boolean(state.sourceDrift?.length);
  $("#apply-desktop").hidden=state.desktopApplied;
  $("#apply-state").textContent=dirty?"Draft changes · desktop settings apply when you save.":state.desktopApplied?"Desktop settings applied · firmware edits require flashing.":"Saved desktop settings haven’t been applied yet.";
  $(".profile-status").classList.toggle("pending",!state.desktopApplied||dirty);
  $("#repository-name").textContent=state.repository.replace("/"," / ");$("#repository-link").href=state.branchUrl||state.repositoryUrl;
  $("#branch-name").textContent=state.branch;$("#head-sha").textContent=state.head.slice(0,7);
}
function renderAccelerationHint() {
  $("#accel-help").textContent=draft.hyprland.accelProfile==="flat"?`Constant ${Math.max(0.005,1+Number(draft.hyprland.sensitivity)).toFixed(3)}× gain on raw movement. Changing the CPI declaration won’t adjust flat-mode speed.`:"Slow rolls for precision; faster rolls cover more ground.";
}
function renderSettings() {
  renderAccelerationHint();
  $$('[data-setting]').forEach(input=>{const value=draft.hyprland[input.dataset.setting];if(input.type==="checkbox")input.checked=value;else input.value=value;});
  $("#scroll-factor-value").textContent=Number(draft.hyprland.scrollFactor).toFixed(1)+"×";
  $("#sensitivity-value").textContent=Number(draft.hyprland.sensitivity).toFixed(2);
  $("#dpi-state").textContent=state.dpiInstalled&&draft.hyprland.declaredCpi===state.config.hyprland.declaredCpi?"Matches the installed desktop declaration.":"This DPI declaration needs a local system installation before it can take effect.";
  $("#sensor-cpi").value=draft.firmware.trackball.cpi;$("#mouse-timeout").value=draft.firmware.trackball.autoMouseTimeoutMs;
}
function changeValue(change,value) {
  if(typeof value==="boolean")return value?"On":"Off";
  if(typeof value==="string"&&value.startsWith("&"))return labelFor(value);
  if(change.label==="scrollFactor")return Number(value).toFixed(1)+"×";
  if(change.label==="sensitivity")return Number(value).toFixed(2);
  if(["cpi","declaredCpi"].includes(change.label))return value?`${value} CPI`:"System default";
  if(change.label==="autoMouseTimeoutMs")return `${value} ms`;
  return ({"left-right-chord":"Left + right together",off:"Off",left:"Left click",right:"Right click",middle:"Middle click",adaptive:"Adaptive",flat:"Flat"})[value]||String(value);
}
function renderChangeRows(changes) {
  if(!changes.length)return '<div class="empty-state"><strong>Everything is in sync.</strong>Choose a key or adjust a setting to start a draft.</div>';
  return changes.map(c=>`<div class="change-row"><div>${escapeHTML(settingNames[c.label]||c.label)}<small>${c.label==="declaredCpi"?"System · separate installation":c.scope==="firmware"?"Keyboard · requires flash":"Desktop · applies when saved"}</small></div><code>${escapeHTML(changeValue(c,c.before))}</code><span>→</span><code>${escapeHTML(changeValue(c,c.after))}</code></div>`).join("");
}
function renderDiffs(files) {return files.map(file=>`<details class="diff-file"><summary>${escapeHTML(file.path)}</summary><pre>${escapeHTML(file.diff)}</pre></details>`).join("");}
function renderHistory() {
  $("#history-list").innerHTML=state.history.length?state.history.map(item=>`<article class="history-row"><h3>${escapeHTML(item.message)}</h3><div class="history-meta"><a href="${escapeHTML(item.url)}" target="_blank" rel="noreferrer">${item.sha.slice(0,7)}</a><time>${escapeHTML(new Date(item.date).toLocaleString(undefined,{dateStyle:"medium",timeStyle:"short"}))}</time></div><div class="history-actions"><button class="text-button" data-restore="${item.sha}">Restore as draft ↶</button><a class="text-button" href="${escapeHTML(item.url)}" target="_blank" rel="noreferrer">View commit ↗</a></div></article>`).join(""):'<div class="empty-state"><strong>Your history starts here.</strong>The first configuration commit will appear after repository setup.</div>';
  $$('[data-restore]').forEach(button=>button.addEventListener("click",async()=>{if(isDirty()&&!confirm("Replace your current draft with this saved version?"))return;try{const result=await api("/api/restore",{sha:button.dataset.restore});draft=result.config;draftRevision=state.revision;staleDraft=false;showError("");changed();renderAll();switchView("keyboard");await review();toast("Previous version loaded for review. Nothing has been saved yet.");}catch(error){toast(error.message,true);}}));
}
function renderAll(){renderStatus();renderKeyboard();renderInspector();renderSettings();renderHistory();}
function switchView(next) {
  view=next;recording=false;
  $$(".view").forEach(node=>node.hidden=node.id!==`view-${next}`);
  $$(".nav-item").forEach(button=>button.classList.toggle("active",button.dataset.view===next));
  $("#breadcrumb-current").textContent=next==="keyboard"?"KEYBOARD":next.toUpperCase();
  const [title,description]=titles[next];$("#page-title").textContent=title;$("#page-description").textContent=description;
  if(next==="keyboard")renderInspector();
  renderStatus();
}
async function review() {
  $("#review").disabled=true;
  try {
    const result=await api("/api/preview",{config:draft});
    const firmware=result.firmwareRequiresFlash;
    const dpi=draft.hyprland.declaredCpi!==state.config.hyprland.declaredCpi;
    $("#review-content").innerHTML=renderChangeRows(result.changes)+'<div class="review-info">Save creates a Git commit, pushes to GitHub, and applies your desktop settings.'+(firmware?' Keyboard changes still need a firmware build and flash.':'')+(dpi?' The DPI declaration needs a separate system installation.':'')+'</div>';
    $("#review-diffs").innerHTML=renderDiffs(result.files);
    $(".review-details").open=false;
    const changes=result.changes;
    $("#commit-message").value=changes.length===1?`Update ${settingNames[changes[0].label]||changes[0].label}`:changes.some(c=>c.scope==="firmware")?"Update Skree keymap and preferences":"Tune Skree desktop and trackball settings";
    $("#commit-destination").textContent=state.branch;$("#save-error").hidden=true;$("#review-dialog").showModal();
  }catch(error){toast(error.message,true);}finally{renderStatus();}
}
async function saveGitHub() {
  if(busy)return;busy=true;$("#save-github").disabled=true;$("#save-github").textContent="Saving & applying…";
  try {
    const result=await api("/api/save",{config:draft,message:$("#commit-message").value,applyDesktop:true});state=result.state;draft=copy(state.config);draftRevision=state.revision;staleDraft=false;persistDraft();renderAll();$("#review-dialog").close();
    showError("");
    const problems=[];
    if(!result.pushed)problems.push(`Saved locally; GitHub push failed: ${result.pushError}. Your commit is retained.`);
    if(result.applyError)problems.push(`Saved, but desktop apply failed: ${result.applyError}. Previous desktop settings were restored. Use Apply saved settings to retry.`);
    if(problems.length){showError(problems.join(" "));toast("Saved; an update needs attention.",true);}
    else toast(`Saved to GitHub · desktop settings applied · ${result.commit.slice(0,7)}`);
    renderStatus();

  }catch(error){$("#save-error").hidden=false;$("#save-error").textContent=error.message;persistDraft();}
  finally{busy=false;$("#save-github").disabled=false;$("#save-github").innerHTML='Save &amp; apply <span>↗</span>';renderStatus();}
}
async function retryPush(){try{const result=await api("/api/push",{});state=result.state;showError("");renderStatus();renderHistory();toast("Local commits pushed to GitHub.");}catch(error){toast(error.message,true);}}
async function refreshGitHub(){if(busy)return;if(isDirty()&&!confirm("Discard the current browser draft and refresh from GitHub?"))return;busy=true;$("#refresh").disabled=true;try{const result=await api("/api/refresh",{});state=result.state;draft=copy(state.config);draftRevision=state.revision;staleDraft=false;persistDraft();showError("");renderAll();toast("Repository refreshed from GitHub.");}catch(error){toast(error.message,true);}finally{busy=false;$("#refresh").disabled=false;renderStatus();}}
async function applyDesktop(){if(busy||isDirty())return;busy=true;$("#apply-desktop").textContent="Applying & validating…";renderStatus();try{const result=await api("/api/apply",{});state=result.state;renderStatus();toast(result.message);}catch(error){toast(error.message,true);}finally{busy=false;$("#apply-desktop").innerHTML='Apply saved settings';renderStatus();}}

async function start() {
  try {
    const values=await Promise.all([api("/api/state"),fetch("/geometry.json").then(r=>r.json()),api("/api/catalog")]);
    [state,geometry,catalog]=values;draft=copy(state.config);draftRevision=state.revision;
    try {const saved=JSON.parse(localStorage.getItem(storageKey));if(saved?.revision===state.revision&&saved.config){draft=saved.config;toast("Your unsaved browser draft was restored.");}else if(saved?.config){staleDraft=true;draftRevision=saved.revision;showError("A draft from an older repository revision is still in browser storage. Export it before starting over, or discard it to use the current repository.");draft=saved.config;}}catch{}
    $("#keycode-list").innerHTML=catalog.keycodes.map(code=>`<option value="${code}">${escapeHTML(symbols[code]||code)}</option>`).join("");
    $("#declared-cpi").innerHTML='<option value="0">System default · no override</option>'+Array.from({length:16},(_,i)=>`<option value="${(i+1)*200}">${(i+1)*200} CPI</option>`).join("");
    $("#sensor-cpi").innerHTML=Array.from({length:16},(_,i)=>`<option value="${(i+1)*200}">${(i+1)*200} CPI</option>`).join("");
    if(state.sourceDrift?.length)showError("Generated files differ from the saved profile: "+state.sourceDrift.join(", ")+". Reconcile those edits before saving; they will not be overwritten.");
    renderAll();
  }catch(error){showError("Could not load the local repository. "+error.message);$("#save-state").textContent="Connection unavailable";$("#review").disabled=true;return;}
  $$(".nav-item").forEach(button=>button.addEventListener("click",()=>switchView(button.dataset.view)));
  $("#show-all-layers").addEventListener("change",()=>{if(layer===2&&!$("#show-all-layers").checked)layer=0;renderKeyboard();renderInspector();});
  $("#behavior").addEventListener("change",event=>{const kind=event.target.value;assign({kp:"&kp A",mo:"&mo 1",to:"&to 0",tog:"&tog 1",mkp:"&mkp MCLK",trans:"&trans",none:"&none",bt:"&bt BT_NXT",rgb_ug:"&rgb_ug RGB_TOG",bootloader:"&bootloader",sys_reset:"&sys_reset"}[kind]);});
  $("#reset-key").addEventListener("click",()=>assign(state.config.firmware.layers[layer].bindings[selected]));
  $$("[data-setting]").forEach(input=>input.addEventListener(input.type==="range"?"input":"change",()=>{draft.hyprland[input.dataset.setting]=input.type==="checkbox"?input.checked:input.type==="range"||input.dataset.setting==="declaredCpi"?Number(input.value):input.value;changed();if(input.dataset.setting==="declaredCpi")renderSettings();if(input.dataset.setting==="accelProfile"||input.dataset.setting==="sensitivity")renderAccelerationHint();if(input.dataset.setting==="scrollFactor")$("#scroll-factor-value").textContent=Number(input.value).toFixed(1)+"×";if(input.dataset.setting==="sensitivity")$("#sensitivity-value").textContent=Number(input.value).toFixed(2);}));
  $("#sensor-cpi").addEventListener("change",event=>{draft.firmware.trackball.cpi=Number(event.target.value);changed();});
  $("#mouse-timeout").addEventListener("change",event=>{const value=Number(event.target.value);if(!Number.isInteger(value)||value<100||value>5000){toast("Choose a duration from 100 to 5000 ms.",true);renderSettings();return;}draft.firmware.trackball.autoMouseTimeoutMs=value;changed();});
  $("#open-mouse-layer").addEventListener("click",()=>{layer=3;selected=54;renderStatus();renderKeyboard();renderInspector();switchView("keyboard");});
  $("#review").addEventListener("click",review);$("#save-github").addEventListener("click",saveGitHub);$("#refresh").addEventListener("click",refreshGitHub);$("#apply-desktop").addEventListener("click",applyDesktop);
  $("#review-dialog").addEventListener("cancel",event=>{if(busy)event.preventDefault();});
  $$(".close-dialog").forEach(button=>button.addEventListener("click",()=>{if(!busy)$("#review-dialog").close();}));
  $("#discard").addEventListener("click",()=>{if((isDirty()||staleDraft)&&confirm("Discard all unsaved changes in this draft?")){draft=copy(state.config);draftRevision=state.revision;staleDraft=false;showError("");persistDraft();renderAll();toast("Draft discarded. Saved configuration restored.");}});
  $("#export-config").addEventListener("click",()=>{const blob=new Blob([JSON.stringify(draft,null,2)+"\n"],{type:"application/json"});const url=URL.createObjectURL(blob);const link=document.createElement("a");link.href=url;link.download="skree-config.json";link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
  window.addEventListener("keydown",event=>{if(!recording)return;event.preventDefault();event.stopPropagation();const codes={Escape:"ESC",Backspace:"BSPC",Enter:"RET",Space:"SPACE",Tab:"TAB",Delete:"DEL",ShiftLeft:"LSHFT",ShiftRight:"RSHFT",ControlLeft:"LCTRL",ControlRight:"RCTRL",AltLeft:"LALT",AltRight:"RALT",MetaLeft:"LGUI",MetaRight:"RGUI",ArrowLeft:"LEFT",ArrowRight:"RIGHT",ArrowUp:"UP",ArrowDown:"DOWN",Backquote:"GRAVE",Minus:"MINUS",Equal:"EQUAL",BracketLeft:"LBKT",BracketRight:"RBKT",Backslash:"BSLH",Semicolon:"SEMI",Quote:"SQT",Comma:"COMMA",Period:"DOT",Slash:"FSLH",CapsLock:"CAPS",Home:"HOME",End:"END",PageUp:"PG_UP",PageDown:"PG_DN",Insert:"INS"};const code=codes[event.code]||(/^Key[A-Z]$/.test(event.code)?event.code.slice(3):/^Digit[0-9]$/.test(event.code)?"N"+event.code.slice(5):/^F[0-9]+$/.test(event.code)?event.code:null);recording=false;if(code&&catalog.keycodes.includes(code))assign("&kp "+code);else{renderInspector();toast("That key could not be captured. Choose its symbol from the list.",true);}},true);
}
start();
