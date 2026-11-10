const $=id=>document.getElementById(id);
const state={token:sessionStorage.getItem('token'),user:null,catalog:null,tables:null,result:null,tab:'answer',sessionId:sessionStorage.getItem('conversation'),sessions:[]};
const questions=[
  ['01','生产与库存','按产品比较 2025 年生产数量与当前成品库存，找出库存偏低的产品。'],
  ['02','采购与发货','按供应商比较 2025 年采购订单数量与发货次数。'],
  ['03','来料质量','分析 2025 年各材料类别的来料检验不良数量，并画图。'],
  ['04','物料需求','对比各仓库原料库存与产品 BOM 所需材料。'],
  ['05','交付与质量','2025 年上半年哪些供应商交付表现恶化？质量是否也下降？'],
  ['06','数据边界','数据中能否分析设备停机原因？说明证据缺口。']
];
async function api(path,options={}){
  const headers={'Content-Type':'application/json',...(options.headers||{})};
  if(state.token)headers.Authorization=`Bearer ${state.token}`;
  const response=await fetch(path,{...options,headers});
  let data;try{data=await response.json()}catch{data={detail:'服务器响应无效'}}
  if(response.status===401&&path!='/auth/login'){signOut();throw Error('会话已过期，请重新登录')}
  if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:(data.detail?.safe_message||`请求失败 (${response.status})`));
  return data;
}
function node(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=String(text);return e}
function clear(el){el.replaceChildren()}
function fmt(value){if(value===null||value===undefined)return '—';if(typeof value==='number')return Number.isInteger(value)?value.toLocaleString('zh-CN'):value.toLocaleString('zh-CN',{maximumFractionDigits:6});return String(value)}
function currentView(name){
  document.querySelectorAll('.view').forEach(v=>v.classList.add('hidden'));
  $(`${name}View`).classList.remove('hidden');
  document.querySelectorAll('#nav button').forEach(b=>b.classList.toggle('active',b.dataset.view===name));
  $('currentSection').textContent={overview:'总览',ask:'智能问析',catalog:'数据与指标',history:'执行记录',admin:'用户管理'}[name];
  document.querySelector('.content').classList.toggle('chat-content',name==='ask');document.querySelector('.sidebar').classList.remove('mobile-open');
  if(name==='history')loadHistory();if(name==='admin')loadUsers();
}
function signOut(){state.token=null;state.user=null;state.sessionId=null;sessionStorage.removeItem('conversation');sessionStorage.removeItem('token');$('shell').classList.add('hidden');$('login').classList.remove('hidden');$('password').value=''}
async function signIn(){
  const data=await api('/auth/login',{method:'POST',body:JSON.stringify({username:$('username').value,password:$('password').value})});
  state.token=data.access_token;sessionStorage.setItem('token',state.token);state.user=data.user;
  await loadWorkspace();
}
async function loadWorkspace(){
  try{
    state.user=await api('/auth/me');
    const [catalog,tables,health]=await Promise.all([api('/catalog'),api('/tables'),api('/health')]);
    state.catalog=catalog;state.tables=tables;
    $('login').classList.add('hidden');$('shell').classList.remove('hidden');
    $('userBadge').textContent=state.user.username.slice(0,1).toUpperCase();
    $('adminNav').classList.toggle('hidden',state.user.role!=='admin');
    const canAsk=state.user.role!=='viewer';$('askNav').classList.toggle('hidden',!canAsk);$('heroAsk').classList.toggle('hidden',!canAsk);
    $('tableCount').textContent=tables.tables.length;$('metricCount').textContent=Object.keys(catalog.published_metrics||{}).length;
    $('rowCount').textContent=Number(tables.total_rows).toLocaleString('zh-CN');
    $('sideSnapshot').textContent=tables.snapshot_id;
    $('snapshotLabel').textContent=`快照 ${health.snapshot_id||'—'}`;
    renderExamples();renderCatalog();await loadSessions();currentView(canAsk?'ask':'overview');
  }catch(error){$('loginError').textContent=error.message}
}
function renderExamples(){
  clear($('examples'));clear($('quickCards'));
  questions.forEach(([index,title,question],i)=>{
    const button=node('button','example');button.type='button';button.append(node('span','',index),document.createTextNode(question));button.onclick=()=>{currentView('ask');$('question').value=question;$('question').focus()};$('examples').append(button);
    if(i<3&&state.user.role!=='viewer'){const card=node('button','quick-card');card.type='button';card.append(node('span','quick-index',`QUERY ${index}`),node('h3','',title),node('p','',question),node('span','arrow','↗'));card.onclick=button.onclick;$('quickCards').append(card)}
  });
}
function renderCatalog(){
  clear($('metricCards'));clear($('tableCards'));
  for(const [id,metric] of Object.entries(state.catalog.metrics)){
    const card=node('article','metric-card');card.append(node('h3','',metric.name),node('code','',id+' · '+(metric.status==='published'?'已发布':'待确认')),node('p','',metric.note),node('p','',`公式：${metric.formula}`));$('metricCards').append(card);
  }
  state.tables.tables.forEach(table=>{
    const item=node('details','table-card'),sum=node('summary');sum.append(node('span','',table.name),node('small','',`${fmt(table.rows)} 行 · ${table.columns.length} 字段`));item.append(sum);
    const cols=node('div','column-pills');table.columns.forEach(col=>cols.append(node('span',col.sensitive?'sensitive':'',`${col.name} · ${col.type}`)));item.append(cols);$('tableCards').append(item)
  });
}

function rememberSession(){if(state.sessionId)sessionStorage.setItem('conversation',state.sessionId);else sessionStorage.removeItem('conversation')}
function setChatTitle(){const current=state.sessions.find(s=>s.session_id===state.sessionId);$('chatTitle').textContent=current?.title||'新会话';$('chatSnapshot').textContent=state.tables?.snapshot_id?`数据快照 ${state.tables.snapshot_id}`:''}
function scrollChat(){const box=$('chatScroll');requestAnimationFrame(()=>{box.scrollTop=box.scrollHeight})}
async function loadSessions(){
  state.sessions=await api('/sessions');const list=$('sessionList');clear(list);
  state.sessions.slice().reverse().forEach(session=>{const button=node('button','session-item',session.title);button.type='button';button.title=session.title;button.classList.toggle('active',session.session_id===state.sessionId);button.onclick=async()=>{state.sessionId=session.session_id;state.result=null;rememberSession();await loadSessions();currentView('ask')};list.append(button)});
  if(!state.sessions.some(s=>s.session_id===state.sessionId))state.sessionId=null;
  if(!state.sessions.length)list.append(node('p','session-empty','暂无会话'));
  rememberSession();setChatTitle();await loadConversation();
}
function newConversation(){state.sessionId=null;state.result=null;rememberSession();closeArtifact();clear($('conversationLog'));$('resultEmpty').classList.remove('hidden');$('chatTitle').textContent='新会话';$('question').value='';$('startDate').value='';$('endDate').value='';document.querySelectorAll('.session-item').forEach(b=>b.classList.remove('active'));currentView('ask');$('question').focus()}
function messageShell(type){const article=node('article',`conversation-message ${type==='user'?'user-message':'assistant-message'}`);if(type==='assistant'){const meta=node('div','message-meta');meta.append(node('span','message-avatar','∴'),node('span','','问析助手'));article.append(meta)}const body=node('div','message-body');article.append(body);return {article,body}}
function addMessageText(body,content){if(!content)return;const text=node('div','message-text',content);body.append(text)}
function artifactLabel(type){return {table:'数据表',chart:'图表',text:'文字',report:'报告'}[type]||'产物'}
function artifactHeader(item){const head=node('div','artifact-heading'),left=node('div');left.append(node('span','artifact-kicker',artifactLabel(item.content_type)),node('h3','',item.title));head.append(left,node('span','artifact-id',item.artifact_id));return head}
async function renderInlineArtifact(id,host){
  const card=node('section','inline-artifact');card.append(node('p','muted','正在读取产物…'));host.append(card);
  try{
    const item=await api(`/artifacts/${encodeURIComponent(id)}`);clear(card);
    if(item.content_type==='text'){
      const data=await api(`/artifacts/${id}/content`);
      card.className='message-text';
      card.append(renderMarkdown(String(data.content||'')));
      const detail=node('button','artifact-open','查看依据');
      detail.type='button';
      detail.onclick=()=>showArtifact(id);
      card.append(detail);
      return;
    }
    card.append(artifactHeader(item));
    if(item.content_type==='table'){
      const data=await api(`/artifacts/${id}/content?offset=0&limit=6`);
      card.append(node('p','artifact-summary',`${item.summary||'查询结果'} · ${fmt(data.total_rows)} 行`));
      card.append(data.rows.length?makeTable(data.rows):node('p','muted','结果为空'));
      if(data.has_more)card.append(node('p','preview-note',`预览前 ${data.rows.length} 行，完整数据可分页查看`));
    }else if(item.content_type==='chart'){
      const data=await api(`/artifacts/${id}/content`);card.append(renderChart(data.content));
    }else{
      const data=await api(`/artifacts/${id}/content`),content=String(data.content||'');
      card.append(node('p','artifact-summary',item.summary||artifactLabel(item.content_type)));
      card.append(node('p','document-preview',content.slice(0,260)+(content.length>260?'…':'')));
    }
    const actions=node('div','artifact-actions'),open=node('button','artifact-open','查看详情与依据 ↗');open.type='button';open.onclick=()=>showArtifact(id);actions.append(open);
    if(item.content_type==='chart'){const source=item.source_artifact_ids?.[0];if(source){const dataButton=node('button','artifact-open','查看图表数据');dataButton.type='button';dataButton.onclick=()=>showArtifact(source);actions.append(dataButton)}}
    card.append(actions);
  }catch(error){clear(card);card.append(node('p','form-error',`产物读取失败：${error.message}`))}
}
async function loadConversation(){
  const box=$('conversationLog');clear(box);$('resultEmpty').classList.toggle('hidden',!!state.sessionId);
  if(!state.sessionId)return;
  try{
    const events=await api(`/sessions/${encodeURIComponent(state.sessionId)}/events`);
    const pendingCards=[];
    for(const event of events){
      if(!['user_message','assistant_response'].includes(event.event_type))continue;
      const type=event.event_type==='user_message'?'user':'assistant',view=messageShell(type);
      if(type==='user')addMessageText(view.body,event.payload.content);
      else{
        const seen=new Set();
        for(const block of event.payload.blocks||[]){
          if(block.artifact_id&&!seen.has(block.artifact_id)){seen.add(block.artifact_id);pendingCards.push(renderInlineArtifact(block.artifact_id,view.body))}
        }
      }
      box.append(view.article);
    }
    if(state.result?.session_id===state.sessionId)renderTurnStatus(state.result);
    scrollChat();
    await Promise.allSettled(pendingCards);
  }catch(error){box.append(node('p','form-error',error.message))}
}
function renderTurnStatus(result){
  if(result.status==='completed'||result.status==='needs_clarification')return;
  const view=messageShell('assistant'),status=node('div','turn-status');
  status.append(node('strong','',result.status==='waiting_input'?'分析已暂停':'本轮部分完成'));
  if(result.tasks?.length){const details=node('details','run-details'),summary=node('summary','','查看执行步骤');details.append(summary);for(const step of result.tasks)details.append(node('p','',`${step.assigned_agent.replace('Agent','')} · ${step.status}${step.summary?' · '+step.summary.slice(0,90):''}`));status.append(details)}
  for(const request of result.requests||[]){
    if(!['pending','failed'].includes(request.status))continue;
    const button=node('button','artifact-open',`继续执行 · ${request.title}`);button.type='button';
    button.onclick=async()=>{button.disabled=true;try{state.result=await api(`/requests/${request.request_id}/resume`,{method:'POST',body:JSON.stringify({continue_analysis:true})});await loadConversation()}catch(error){status.append(node('p','form-error',error.message));button.disabled=false}};
    status.append(button);
  }
  view.body.append(status);$('conversationLog').append(view.article);
}
async function askQuestion(event){
  event.preventDefault();const query=$('question').value.trim();if(!query)return;
  const button=$('askButton'),label=button.querySelector('span');button.disabled=true;label.textContent='处理中';
  const constraints={};if($('startDate').value)constraints.start=$('startDate').value;if($('endDate').value)constraints.end=$('endDate').value;
  const user=messageShell('user');addMessageText(user.body,query);$('resultEmpty').classList.add('hidden');$('conversationLog').append(user.article);
  const pending=messageShell('assistant');pending.body.append(node('p','thinking','正在分析问题和数据…'));$('conversationLog').append(pending.article);scrollChat();
  $('question').value='';
  try{state.result=await api('/ask',{method:'POST',body:JSON.stringify({question:query,session_id:state.sessionId,constraints})});state.sessionId=state.result.session_id;rememberSession();await loadSessions()}
  catch(error){pending.body.replaceChildren(node('p','form-error',error.message));$('question').value=query;scrollChat()}
  finally{button.disabled=false;label.textContent='发送';$('question').focus()}
}
function closeArtifact(){$('artifactDrawer').classList.add('hidden');$('drawerBackdrop').classList.add('hidden');document.body.classList.remove('drawer-open')}
async function showArtifact(id){
  currentView('ask');$('artifactDrawer').classList.remove('hidden');$('drawerBackdrop').classList.remove('hidden');document.body.classList.add('drawer-open');
  const box=$('drawerContent');clear(box);box.append(node('p','muted','正在读取完整产物…'));$('drawerTitle').textContent='查看结果';$('drawerMeta').textContent='';
  try{const item=await api(`/artifacts/${encodeURIComponent(id)}`);$('drawerType').textContent=artifactLabel(item.content_type);$('drawerTitle').textContent=item.title;$('drawerMeta').textContent=`${item.artifact_id} · ${item.summary}`;clear(box);await renderArtifactDetail(item,box)}
  catch(error){box.replaceChildren(node('p','form-error',error.message))}
  $('closeDrawer').focus();
}
async function renderArtifactDetail(item,box){
  const id=item.artifact_id;
  if(item.content_type==='table'){
    const origin=item.payload?.provenance?.method;
    if(origin)box.append(node('p','artifact-summary',({business_sql:'来源：业务数据库只读查询',artifact_compute:'来源：已有数据结果的精确计算',table_compose:'来源：根据需求制作的表格'})[origin]||`来源：${origin}`));
    let offset=0;const dataBox=node('div'),controls=node('div','artifact-controls');box.append(dataBox,controls);
    async function page(){const data=await api(`/artifacts/${id}/content?offset=${offset}&limit=20`);clear(dataBox);clear(controls);dataBox.append(data.rows.length?makeTable(data.rows):node('p','muted','没有匹配记录'));controls.append(node('span','muted',`${fmt(data.total_rows)} 行 · 当前 ${data.rows.length?offset+1:0}–${offset+data.rows.length}`));
      if(offset>0){const previous=node('button','text-button','上一页');previous.onclick=()=>{offset=Math.max(0,offset-20);page()};controls.append(previous)}
      if(data.has_more){const next=node('button','text-button','下一页');next.onclick=()=>{offset+=20;page()};controls.append(next)}}
    await page();
    const details=node('details','evidence-details'),summary=node('summary','','查看 SQL 与语义口径');details.append(summary);details.ontoggle=async()=>{if(!details.open||details.dataset.loaded)return;try{const data=await api(`/artifacts/${id}/sql`);details.append(node('pre','sql-block',data.sql||'相关依据见来源产物'),node('pre','sql-block',JSON.stringify(data.semantic_binding||item.payload,null,2)));details.dataset.loaded='1'}catch(error){details.append(node('p','form-error',error.message))}};box.append(details);
  }else{const data=await api(`/artifacts/${id}/content`);if(item.content_type==='chart')box.append(renderChart(data.content));else{box.append(renderMarkdown(String(data.content||'')));const download=node('button','artifact-open','下载 Markdown');download.onclick=()=>downloadArtifact(id);box.append(download)}}
  const refs=item.source_artifact_ids||[],sources=node('div','evidence-list');
  sources.append(node('span','',refs.length?'来源：':'来源：'+(item.producer||'系统')));
  for(const id of refs){
    const button=node('button','artifact-open',id);
    button.type='button';
    button.onclick=()=>showArtifact(id);
    sources.append(button);
  }
  box.append(sources);
}
function renderMarkdown(content){const wrap=node('div','document-body');for(const line of content.split('\n')){const heading=line.match(/^(#{1,3})\s+(.+)$/);if(heading)wrap.append(node(`h${Math.min(heading[1].length+1,4)}`,'',heading[2]));else if(/^[-*]\s+/.test(line))wrap.append(node('p','document-list',line.replace(/^[-*]\s+/,'')));else if(line.trim())wrap.append(node('p','',line));}return wrap}
async function downloadArtifact(id){const response=await fetch(`/artifacts/${id}/download`,{headers:{Authorization:`Bearer ${state.token}`}});if(!response.ok)throw Error('下载失败');const url=URL.createObjectURL(await response.blob()),link=node('a');link.href=url;link.download=`${id}.md`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000)}
function makeTable(rows){const wrap=node('div','table-wrap');if(!rows.length)return wrap;const table=node('table','data-table'),head=node('thead'),tr=node('tr'),columns=Object.keys(rows[0]);columns.forEach(col=>tr.append(node('th','',col)));head.append(tr);table.append(head);const body=node('tbody');rows.forEach(row=>{const line=node('tr');columns.forEach(col=>line.append(node('td','',fmt(row[col]))));body.append(line)});table.append(body);wrap.append(table);return wrap}
function renderChart(config){
  const wrap=node('div');const rows=config.dataset.source,series=config.series;
  if(!rows.length)return node('p','muted','无可绘制数据');
  const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 760 420');svg.setAttribute('class','chart-svg');svg.setAttribute('role','img');svg.setAttribute('aria-label',config.title?.text||'数据图表');
  const add=(tag,attrs,text)=>{const el=document.createElementNS(svg.namespaceURI,tag);Object.entries(attrs).forEach(([key,value])=>el.setAttribute(key,String(value)));if(text!==undefined)el.textContent=String(text);svg.append(el);return el};
  const horizontal=config.yAxis.type==='category',scatter=series[0].type==='scatter',left=110,right=710,top=45,bottom=330;
  const numeric=series.flatMap(s=>rows.map(row=>Number(row[s.encode[horizontal?'x':'y']])).filter(Number.isFinite));
  const lo=Math.min(0,...numeric),hi=Math.max(0,...numeric),span=hi-lo||1;
  const value=v=>horizontal?left+(Number(v)-lo)/span*(right-left):bottom-(Number(v)-lo)/span*(bottom-top);
  for(let i=0;i<5;i++){const val=lo+span*i/4,pos=value(val);add('line',horizontal?{x1:pos,x2:pos,y1:top,y2:bottom,stroke:'#e2e2e2'}:{x1:left,x2:right,y1:pos,y2:pos,stroke:'#e2e2e2'});add('text',horizontal?{x:pos,y:bottom+22,'font-size':10}:{x:left-8,y:pos+4,'text-anchor':'end','font-size':10},fmt(val))}
  series.forEach((s,j)=>{
    const color=['#202020','#666666','#a0a0a0','#454545'][j%4],category=s.encode[horizontal?'y':'x'],field=s.encode[horizontal?'x':'y'];const step=(horizontal?bottom-top:right-left)/rows.length;
    const xvals=scatter?rows.map(row=>Number(row[s.encode.x])):[],xmin=scatter?Math.min(...xvals):0,xspan=scatter?(Math.max(...xvals)-xmin||1):1;
    const coords=rows.map((row,i)=>[scatter?left+(Number(row[s.encode.x])-xmin)/xspan*(right-left):left+step*(i+.5),value(row[field])]);
    if(s.type==='line')add('polyline',{points:coords.map(p=>p.join(',')).join(' '),fill:'none',stroke:color,'stroke-width':2});
    rows.forEach((row,i)=>{if(!Number.isFinite(Number(row[field])))return;
      if(s.type==='bar'){const group=step*.72/series.length,pos=(horizontal?top:left)+step*i+step*.14+group*j,v=value(row[field]),zero=value(0);const rect=add('rect',horizontal?{x:Math.min(v,zero),y:pos,width:Math.abs(v-zero),height:Math.max(1,group-2),fill:color}:{x:pos,y:Math.min(v,zero),width:Math.max(1,group-2),height:Math.abs(v-zero),fill:color});const tip=document.createElementNS(svg.namespaceURI,'title');tip.textContent=`${row[category]} · ${s.name}: ${fmt(row[field])}`;rect.append(tip)}
      else{const dot=add('circle',{cx:coords[i][0],cy:coords[i][1],r:4,fill:color});const tip=document.createElementNS(svg.namespaceURI,'title');tip.textContent=`${row[category]} · ${s.name}: ${fmt(row[field])}`;dot.append(tip)}
      if(j===0&&!scatter&&i%Math.max(1,Math.ceil(rows.length/12))===0)add('text',horizontal?{x:left-8,y:top+step*(i+.5),'text-anchor':'end','font-size':10}:{x:left+step*(i+.5),y:bottom+22,'text-anchor':'middle','font-size':10},String(row[category]).slice(0,16));
    });
    add('circle',{cx:120+j*160,cy:390,r:5,fill:color});add('text',{x:132+j*160,y:394,'font-size':11},s.name);
  });
  wrap.append(svg,node('p','muted',`展示 ${config.selection.displayed_rows} 行，数据来源 ${config.selection.source_id}`));return wrap;
}
async function loadHistory(){try{const rows=await api('/history');clear($('historyTable'));if(!rows.length){$('historyTable').append(node('p','muted','暂无执行记录'));return}$('historyTable').append(makeTable(rows))}catch(e){$('historyTable').textContent=e.message}}
async function loadUsers(){if(state.user?.role!=='admin')return;try{const rows=await api('/admin/users');clear($('userList'));for(const user of rows){const line=node('div','user-row');line.append(node('strong','',user.username),node('span','role-tag',user.role),node('span','muted',user.active?'启用':'停用'));if(user.active&&user.id!==state.user.id){const stop=node('button','text-button','停用');stop.onclick=async()=>{if(!window.confirm(`停用账号 ${user.username}？`))return;try{await api(`/admin/users/${user.id}/deactivate`,{method:'POST'});loadUsers()}catch(e){window.alert(e.message)}};line.append(stop)}$('userList').append(line)}}catch(e){$('userList').textContent=e.message}}
document.addEventListener('DOMContentLoaded',()=>{
  $('loginForm').addEventListener('submit',async e=>{e.preventDefault();$('loginError').textContent='';try{await signIn()}catch(error){$('loginError').textContent=error.message}});
  $('logout').onclick=async()=>{try{await api('/auth/logout',{method:'POST'})}catch{}signOut()};
  document.querySelectorAll('#nav button').forEach(b=>b.onclick=()=>currentView(b.dataset.view));
  $('newConversation').onclick=newConversation;$('heroAsk').onclick=()=>currentView('ask');$('askForm').addEventListener('submit',askQuestion);
  $('question').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('askForm').requestSubmit()}});
  $('closeDrawer').onclick=closeArtifact;$('drawerBackdrop').onclick=closeArtifact;document.addEventListener('keydown',e=>{if(e.key==='Escape')closeArtifact()});
  $('menuToggle').onclick=()=>document.querySelector('.sidebar').classList.toggle('mobile-open');
  $('userForm').addEventListener('submit',async e=>{e.preventDefault();try{const r=await api('/admin/users',{method:'POST',body:JSON.stringify({username:$('newUsername').value,password:$('newPassword').value,role:$('newRole').value})});$('userFormNote').textContent=`账号 ${$('newUsername').value} 已创建`;$('userForm').reset();loadUsers()}catch(error){$('userFormNote').textContent=error.message}});
  $('openPassword').onclick=()=>$('passwordDialog').showModal();$('cancelPassword').onclick=()=>$('passwordDialog').close();
  $('passwordForm').addEventListener('submit',async e=>{e.preventDefault();try{await api('/auth/password',{method:'POST',body:JSON.stringify({old_password:$('oldPassword').value,new_password:$('newPasswordSelf').value})});$('passwordDialog').close();signOut();$('loginError').textContent='密码已更新，请重新登录。'}catch(error){$('passwordNote').textContent=error.message}});
  if(state.token)loadWorkspace();
});
