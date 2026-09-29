'use strict';
const $ = (query, root = document) => root.querySelector(query);
const $$ = (query, root = document) => [...root.querySelectorAll(query)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const paths = {
  ball:'<circle cx="12" cy="12" r="9"/><path d="M6 5c1 5 8 9 12 14M5 18c5-1 9-8 14-12"/>',
  moon:'<path d="M21 13A9 9 0 0111 3a9 9 0 1010 10z"/>',
  gear:'<path d="M9 3h6l1 4 4 2v6l-4 2-1 4H9l-1-4-4-2V9l4-2z"/><circle cx="12" cy="12" r="3"/>',
  close:'<path d="M6 6l12 12M18 6L6 18"/>', user:'<circle cx="12" cy="7" r="3"/><path d="M5 21v-3a7 7 0 0114 0v3"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M8 3v4m8-4v4"/>',
  sport:'<path d="M2 12h5l3-8 4 16 3-8h5"/>', campus:'<path d="M2 9l10-5 10 5-10 5zM6 12v5q6 5 12 0v-5"/>',
  venue:'<path d="M3 21h18M5 21V7l7-4 7 4v14M9 21v-6h6v6"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l4 2"/>',
  play:'<path d="M7 4l13 8-13 8z"/>', stop:'<rect x="5" y="5" width="14" height="14" rx="1"/>',
  alarm:'<circle cx="12" cy="13" r="8"/><path d="M12 9v4l2 2M5 3L2 6m17-3 3 3M6 19l-2 3m14-3 2 3"/>',
  bulb:'<path d="M8 17c0-3-3-3-3-7a7 7 0 0114 0c0 4-3 4-3 7M8 18h8m-7 3h6"/>',
  wallet:'<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 8l14-5v2m4 7h-6v5h6"/>'
};
function icon(name) { return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.clock}</svg>`; }
function icons(root=document) { $$('[data-icon]', root).forEach(el => { el.innerHTML = icon(el.dataset.icon); }); }
icons();
let state = null, logKey = '', orderKey = '', scheduleKey = '', initialized = false, formSlots = [], editingConfig = 'booking';
let scheduleConfig = null, serverOffset = 0, toastTimer;
const yuan = cents => cents == null ? '—' : `¥${(cents / 100).toFixed(2)}`;
const stamp = seconds => new Date(seconds * 1000 + 8 * 3600000).toISOString().slice(0,16).replace('T',' ');
function toast(text) { $('#toast').textContent = text; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { $('#toast').hidden = true; }, 4500); }
function askConfirmation(message) {
  const dialog = $('#confirm-dialog');
  if (dialog.open) return Promise.resolve(false);
  $('#confirm-message').textContent = message;
  dialog.returnValue = 'no';
  return new Promise(resolve => {
    dialog.addEventListener('close', () => resolve(dialog.returnValue === 'yes'), {once:true});
    dialog.showModal();
  });
}
async function api(action, payload = {}) {
  const response = await fetch('/api/action', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,payload})});
  const result = await response.json();
  if (!response.ok || !result.ok) throw new Error(result.error || '操作未完成');
  return result;
}
async function act(action, payload={}) { try { await api(action,payload); await refresh(); return true; } catch(error) { toast(error.message); return false; } }
function tab(name) {
  $$('nav [role=tab]').forEach(el => el.setAttribute('aria-selected', String(el.dataset.tab === name)));
  $$('main > section').forEach(el => el.hidden = el.id !== name);
}
$$('nav button').forEach(button => button.addEventListener('click', () => tab(button.dataset.tab)));
$('#go-schedule').onclick = () => tab('schedule');
$$('[data-close]').forEach(button => button.onclick = () => button.closest('dialog').close());
$$('dialog').forEach(dialog => dialog.addEventListener('click', event => { if(event.target === dialog) { const r = dialog.getBoundingClientRect(); if(event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.close(); } }));
document.documentElement.dataset.theme = localStorage.getItem('szu-theme') || 'light';
$('#theme').onclick = () => { const theme = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark'; document.documentElement.dataset.theme = theme; localStorage.setItem('szu-theme',theme); };
$('#hide').onclick = () => act('window_hide');
$('#quit').onclick = () => act('quit');
function openSettings() { if(!state) return; const form = $('#account-form'); form.elements.username.value = state.account.username; form.elements.real_name.value = state.account.real_name; form.elements.remember.checked = state.account.remembered; form.elements.password.value = ''; $('#settings-dialog').showModal(); }
$('#settings').onclick = openSettings;
$('#summary-user').onclick = openSettings;
function accountPayload() { const f=$('#account-form').elements; return {username:f.username.value.trim(),real_name:f.real_name.value.trim(),password:f.password.value,remember:f.remember.checked}; }
$('#account-form').onsubmit = async event => { event.preventDefault(); if(await act('account_save',accountPayload())) { $('#account-form').elements.password.value=''; toast('账号设置已保存'); } };
$('#login').onclick = async () => { const data=accountPayload(); if(await act('account_save',data)) { if(await act('login',{password:data.password})) { $('#account-form').elements.password.value=''; toast('请在官方登录窗口完成登录'); } } };

function venueOptions(keep) {
  const f=$('#booking-form').elements, gym=f.sport.value==='gym';
  const options=state.venues[f.sport.value]?.[f.campus.value] || [];
  f.venue.innerHTML=[...options,'全部'].map(value=>`<option value="${esc(value)}">${esc(value==='全部'?'全部场馆':value)}</option>`).join('');
  f.venue.value = [...options,'全部'].includes(keep) ? keep : options[0] || '全部';
  $('#venue-field').hidden=gym;
}
function renderTimes() {
  $('#time-options').innerHTML=Array.from({length:14},(_,i)=>{const hour=i+8; const slot=`${String(hour).padStart(2,'0')}:00-${String(hour+1).padStart(2,'0')}:00`; const index=formSlots.indexOf(slot); return `<button type="button" data-slot="${slot}" class="${index>=0?'selected':''}" aria-pressed="${index>=0}">${slot}${index>=0?` · ${index+1}`:''}</button>`;}).join('');
  $('#selected-times').textContent=formSlots.length ? '优先顺序：'+formSlots.join(' → ') : '请选择至少一个时段';
}
$('#time-options').onclick=event=>{const button=event.target.closest('[data-slot]');if(!button)return;const slot=button.dataset.slot;formSlots=formSlots.includes(slot)?formSlots.filter(s=>s!==slot):[...formSlots,slot];renderTimes();};
function openBooking(field='target_date',context='booking') {
  if(!state)return;
  editingConfig=context;
  const config=context==='schedule'?scheduleConfig:state.booking.config, f=$('#booking-form').elements;
  f.target_date.value=state.booking.target_date;
  f.target_date.closest('label').hidden=context==='schedule';
  f.sport.innerHTML=Object.entries(state.sports).map(([key,label])=>`<option value="${esc(key)}">${esc(label)}</option>`).join('');
  ['sport','campus','retry_interval','max_retries','request_timeout'].forEach(key=>f[key].value=config[key]);
  venueOptions(config.venue); formSlots=[...config.slots]; renderTimes();
  $('#booking-dialog').showModal(); if(f[field]) f[field].focus();
}
$$('.quick-edit').forEach(button=>button.onclick=()=>openBooking(button.dataset.field));
$('#booking-form').elements.sport.onchange=()=>venueOptions();
$('#booking-form').elements.campus.onchange=()=>venueOptions();
$('#booking-form').onsubmit=async event=>{
  event.preventDefault();if(!formSlots.length)return toast('请选择至少一个时段');
  const f=event.target.elements;
  const config={...(editingConfig==='schedule'?scheduleConfig:state.booking.config),sport:f.sport.value,campus:f.campus.value,venue:f.venue.value,slots:[...formSlots],retry_interval:Number(f.retry_interval.value),max_retries:Number(f.max_retries.value),request_timeout:Number(f.request_timeout.value)};
  if(editingConfig==='schedule'){scheduleConfig=config;renderScheduleConfig();$('#booking-dialog').close();return;}
  if(await act('booking_save',{config,target_date:f.target_date.value})){$('#booking-dialog').close();toast('预约设置已保存');}
};
$('#start').onclick=async()=>{
  if(!state)return;
  const active=state.tasks.some(t=>['queued','running','waiting_login'].includes(t.status));
  if(active){await act('booking_stop');return;}
  if(!state.account.has_session){toast('请先完成官方登录');openSettings();return;}
  await act('booking_start');
};
$('#clear-log').onclick=()=>act('logs_clear');
$('#resume-tasks').onclick=event=>{const button=event.target.closest('[data-resume]');if(button)act('task_resume',{id:button.dataset.resume});};

function populateCare(){const f=$('#care-form').elements;f.auto_pay.checked=state.care.auto_pay;f.max_cents.value=(state.care.max_cents/100).toFixed(2);f.daily_limit.value=(state.daily_limit/100).toFixed(2);f.cancel_minutes.value=state.care.cancel_minutes;const c=$('#court-form').elements;c.preferred.value=state.care.preferred;c.avoided.value=state.care.avoided;}
$('#care-form').onsubmit=async event=>{event.preventDefault();const f=event.target.elements;const payload={...state.care,auto_pay:f.auto_pay.checked,max_cents:Math.round(Number(f.max_cents.value)*100),daily_limit:Math.round(Number(f.daily_limit.value)*100),cancel_minutes:Number(f.cancel_minutes.value)};if(await act('care_save',payload))toast('已保存，仅用于之后的新订单');};
$('#court-form').onsubmit=async event=>{event.preventDefault();const f=event.target.elements;const payload={...state.care,daily_limit:state.daily_limit,preferred:f.preferred.value.trim(),avoided:f.avoided.value.trim()};if(await act('care_save',payload))toast('场地优先级已保存');};
$('#refresh-balance').onclick=()=>act('balance_refresh');
$('#show-history').onchange=()=>{orderKey='';renderOrders();};
function renderOrders(){
  const today=state.server_time.slice(0,10), jobs=state.orders.filter(j=>$('#show-history').checked||!(j.terminal&&j.date<today)).reverse();
  const key=JSON.stringify(jobs);if(orderKey===key)return;orderKey=key;
  $('#orders-list').innerHTML=jobs.length?jobs.map(job=>{
    const cutoff=job.cancel_at||job.start-job.cancel_minutes*60;
    return `<article class="card" data-order="${esc(job.id)}"><div class="order-title">${esc(job.venue)}</div><div class="order-period">${esc(job.date)}　${esc(job.slot)}</div><div class="order-state">${esc(job.message)}${!job.terminal?`<br>${job.confirmed?'已确认使用，不再自动取消':'自动取消：'+esc(stamp(cutoff))}`:''}</div><div class="order-id">订单 ${esc(job.id)}</div>${!job.terminal?`<div class="button-row"><button data-order-action="confirm" class="use-button" ${job.confirmed?'disabled':''}>${job.confirmed?'已确认使用':'使用场地'}</button><button data-order-action="cancel" class="danger">取消预约</button></div><details class="more"><summary>更多订单设置</summary><button data-order-action="time">修改自动取消时间</button><button data-order-action="stop_pay">停止此单自动付款</button>${!job.paid&&!job.pay_attempt?'<button data-order-action="retry_pay">重新核验余额付款</button>':''}</details>`:''}</article>`;
  }).join(''):'<div class="empty">还没有托管场地<br>抢到的场地会自动显示在这里</div>';
}
$('#orders-list').onclick=async event=>{
  const button=event.target.closest('[data-order-action]');if(!button)return;
  const id=button.closest('[data-order]').dataset.order, action=button.dataset.orderAction;
  if(action==='cancel'&&!await askConfirmation('确定取消这笔预约吗？即使已确认使用，仍会提交取消。退款结果以学校系统为准。'))return;
  if(action==='retry_pay'&&!await askConfirmation('按此订单的金额上限重新核验并尝试余额付款？'))return;
  if(action==='time'){const job=state.orders.find(j=>j.id===id),f=$('#order-time-form').elements;f.id.value=id;f.cancel_at.value=stamp(job.cancel_at||job.start-job.cancel_minutes*60).replace(' ','T');$('#order-dialog').showModal();return;}
  if(await act('order_'+action,{id}))toast(action==='cancel'?'取消请求已排队，请等待订单状态更新；这不代表学校已确认取消。':'操作已排队，请等待状态更新');
};
$('#order-time-form').onsubmit=async event=>{event.preventDefault();const f=event.target.elements;if(await act('order_time',{id:f.id.value,cancel_at:f.cancel_at.value})){$('#order-dialog').close();toast('自动取消时间已更新');}};

const dayLabels=['一','二','三','四','五','六','日'];
$('#weekdays').innerHTML=[6,0,1,2,3,4,5].map(day=>`<label><input type="checkbox" value="${day}">${dayLabels[day]}</label>`).join('');
function scheduleVisibility(){const f=$('#schedule-form').elements;$('#weekly-fields').hidden=f.kind.value==='once';$('#start-date-label').textContent=f.kind.value==='once'?'执行日期':'生效日期';$('#offset-field').hidden=f.date_mode.value==='fixed';$('#fixed-date-field').hidden=f.date_mode.value!=='fixed';const label=f.date_mode.value==='fixed'?`每次都预约 ${f.target_date.value||'指定日期'} 的场地`:`每次预约执行日${Number(f.day_offset.value)?'之后 '+f.day_offset.value+' 天':'当天'}的场地`;$('#schedule-date-preview').textContent=label+'；'+f.fire_time.value+' 开始查询。';}
function renderScheduleConfig(){if(!scheduleConfig)return;$('#schedule-config-summary').textContent=`${state.sports[scheduleConfig.sport]} · ${scheduleConfig.campus==='1'?'粤海':'丽湖'} · ${scheduleConfig.venue}\n${scheduleConfig.slots.join('，')}\n间隔 ${scheduleConfig.retry_interval}s · 重试 ${scheduleConfig.max_retries} · 超时 ${scheduleConfig.request_timeout}s`;}
$('#schedule-config-summary').addEventListener('click',()=>openBooking('sport','schedule'));
$('#schedule-config-summary').title='点击修改此任务的预约设置';
$('#schedule-config-summary').tabIndex=0;
$('#schedule-config-summary').addEventListener('keydown',e=>{if(e.key==='Enter')openBooking('sport','schedule');});
function fillSchedule(item=null){
  const f=$('#schedule-form').elements;const today=state.server_time.slice(0,10);
  const data=item||{id:crypto.randomUUID(),name:'每周预约',kind:'weekly',fire_time:'12:30:00',start_date:today,end_date:'',weekdays:[0,2,4],date_mode:'offset',day_offset:1,target_date:state.booking.target_date,duration_minutes:0,catchup_seconds:180,resume_on_restart:true,enabled:true};
  ['id','name','kind','fire_time','start_date','end_date','date_mode','day_offset','target_date','duration_minutes','catchup_seconds'].forEach(k=>f[k].value=data[k]);
  f.resume_on_restart.checked=data.resume_on_restart;f.enabled.checked=data.enabled;
  $$('#weekdays input').forEach(box=>box.checked=data.weekdays.includes(Number(box.value)));
  scheduleConfig=structuredClone(item?.config||state.booking.config);renderScheduleConfig();scheduleVisibility();
  $('#schedule-form-title').textContent=item?'编辑定时':'新建定时';
}
$('#schedule-form').addEventListener('change',scheduleVisibility);
$('#new-schedule').onclick=()=>{fillSchedule();$('#schedule-form').scrollIntoView({behavior:'smooth',block:'start'});};
$('#copy-booking').onclick=()=>{scheduleConfig=structuredClone(state.booking.config);renderScheduleConfig();toast('已同步当前预约设置');};
$('#schedule-form').onsubmit=async event=>{
  event.preventDefault();const f=event.target.elements;
  const payload={id:f.id.value,name:f.name.value.trim(),kind:f.kind.value,enabled:f.enabled.checked,fire_time:f.fire_time.value,start_date:f.start_date.value,end_date:f.kind.value==='once'?'':f.end_date.value,weekdays:$$('#weekdays input:checked').map(box=>Number(box.value)),date_mode:f.date_mode.value,day_offset:Number(f.day_offset.value),target_date:f.target_date.value,duration_minutes:Number(f.duration_minutes.value),catchup_seconds:Number(f.catchup_seconds.value),resume_on_restart:f.resume_on_restart.checked,config:scheduleConfig};
  if(await act('schedule_save',payload)){toast('定时预约已保存');$('#schedules-list').scrollIntoView({behavior:'smooth',block:'start'});}
};
function renderSchedules(){
  const key=JSON.stringify(state.schedules);if(key===scheduleKey)return;scheduleKey=key;
  $('#schedules-list').innerHTML=state.schedules.length?state.schedules.map(item=>`<article class="card" data-schedule="${esc(item.id)}"><div class="schedule-title"><strong>${esc(item.name)}</strong><span class="tag">${item.enabled?'已启用':'已暂停'}</span></div><div class="schedule-detail">${item.kind==='once'?esc(item.start_date):'每周 '+item.weekdays.map(i=>dayLabels[i]).join('、')} · ${esc(item.fire_time)}<br><span class="muted">${esc(state.sports[item.config.sport])} · ${esc(item.config.slots.join('，'))}<br>${item.date_mode==='offset'?`预约${item.day_offset===0?'当天':item.day_offset+'天后'}`:'预约 '+esc(item.target_date)}</span></div><div class="countdown" data-fire="${esc(item.next_fire||'')}">${item.enabled?'计算下一次执行…':'定时已暂停'}</div><div class="button-row"><button data-schedule-action="edit">编辑</button><button data-schedule-action="toggle">${item.enabled?'暂停定时':'启用定时'}</button><button class="danger" data-schedule-action="delete">删除</button></div></article>`).join(''):'<div class="empty">尚未设置定时任务<br>在下面设置单次或每周预约</div>';
}
$('#schedules-list').onclick=async event=>{const button=event.target.closest('[data-schedule-action]');if(!button)return;const id=button.closest('[data-schedule]').dataset.schedule,item=state.schedules.find(x=>x.id===id);if(button.dataset.scheduleAction==='edit'){fillSchedule(item);$('#schedule-form').scrollIntoView({behavior:'smooth',block:'start'});}else if(button.dataset.scheduleAction==='toggle'){await act('schedule_toggle',{id,enabled:!item.enabled});}else if(await askConfirmation('删除这条定时？已创建的订单会继续托管。'))await act('schedule_delete',{id});};
function countdown(iso){const seconds=Math.max(0,Math.floor((Date.parse(iso)-Date.now()-serverOffset)/1000));const d=Math.floor(seconds/86400),h=Math.floor(seconds%86400/3600),m=Math.floor(seconds%3600/60),s=seconds%60;return `倒计时：${d?d+'天':''}${h}时${m}分${s}秒`;}
function timers(){if(!state)return;$$('[data-fire]').forEach(el=>{if(el.dataset.fire)el.textContent=countdown(el.dataset.fire);else el.textContent=el.closest('[data-schedule]')&&state.schedules.find(s=>s.id===el.closest('[data-schedule]').dataset.schedule)?.enabled?'无后续执行日期':'定时已暂停';});const next=state.schedules.filter(s=>s.next_fire).sort((a,b)=>a.next_fire.localeCompare(b.next_fire))[0];$('#schedule-preview').hidden=!next;if(next)$('#nearest-timer').textContent=countdown(next.next_fire);}
function render(){
  const config=state.booking.config;
  $('#user-name').textContent=state.account.username?`${state.account.real_name} (${state.account.username})`:'设置账号并登录';
  $('#summary-date').textContent=state.booking.target_date.replaceAll('-','/');
  $('#summary-sport').textContent=state.sports[config.sport];$('#summary-campus').textContent=config.campus==='1'?'粤海':'丽湖';
  $('#summary-venue').textContent=config.venue==='全部'?'全部场馆':config.venue;$('#summary-venue-button').hidden=config.sport==='gym';
  $('#summary-slots').textContent=config.slots.join(', ');$('#summary-params').textContent=`间隔 ${config.retry_interval}s · 重试 ${config.max_retries} · 超时 ${config.request_timeout}s`;
  const task=state.tasks.find(t=>['queued','running','waiting_login'].includes(t.status))||state.tasks.at(-1);
  const active=task&&['queued','running','waiting_login'].includes(task.status);
  $('#progress').textContent=`进度 ${task?.completed.length||0}/${Math.min(2,(active?task.config:config).slots.length)}`;
  $('#start-label').textContent=active?'停止预约':'开始预约';$('#start [data-icon]').innerHTML=icon(active?'stop':'play');$('#start').classList.toggle('danger',Boolean(active));$('#start').classList.toggle('primary',!active);
  $('#run-state').textContent=task?`[ ${active?'运行中':'已结束'} ] ${task.message}`:'[ 就绪 ] 等待开始…';
  $('#resume-tasks').innerHTML=state.tasks.filter(t=>t.status==='paused_restart').map(t=>`<div class="compact-note">${esc(t.target_date)} 的预约已中断<button data-resume="${esc(t.id)}" class="text-button">恢复预约</button></div>`).join('');
  $('#connection').textContent=state.connection;$('.connection').classList.toggle('connected',state.connection==='已登录');$('#balance-small').textContent=state.balance_cents==null?'':`余额 ${yuan(state.balance_cents)}`;$('#balance-large').textContent=yuan(state.balance_cents);
  $('#login').disabled=state.login_busy;$('#login').textContent=state.login_busy?'登录窗口已打开':'打开官方登录';
  const key=JSON.stringify(state.logs);if(key!==logKey){const el=$('#logs'),bottom=el.scrollHeight-el.scrollTop-el.clientHeight<30;el.innerHTML=state.logs.map(line=>`<div class="log-line ${esc(line.level)}"><time>[${esc(line.time)}]</time><span>${esc(line.message)}</span></div>`).join('');if(bottom)el.scrollTop=el.scrollHeight;logKey=key;}
  renderOrders();renderSchedules();timers();
}
let refreshing=false;
async function refresh(){if(refreshing)return;refreshing=true;try{const response=await fetch('/api/state',{cache:'no-store'});const data=await response.json();if(!response.ok)throw new Error(data.error);state=data;serverOffset=Date.parse(data.server_time)-Date.now();if(!initialized){populateCare();fillSchedule();initialized=true;}render();}catch(error){$('#connection').textContent=error.message||'本地助手连接已断开';$('.connection').classList.remove('connected');}finally{refreshing=false;}}
refresh();setInterval(refresh,1000);setInterval(timers,1000);
