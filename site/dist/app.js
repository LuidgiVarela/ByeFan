'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const EXAMPLE = 'exemplo.byefan';
  const people = Array.from({length:60}, (_, i) => ({name:`Perfil fictício ${String(i+1).padStart(2,'0')}`, handle:`exemplo_${String(i+1).padStart(2,'0')}`, mutual:i < 49}));
  let generation = 0, busy = false, filter = 'unseen', limit = 10, local = false, ticket = null;
  const localReady = ['127.0.0.1','localhost'].includes(location.hostname)
    ? fetch('/demo/config').then(r=>r.ok?r.json():null).then(d=>{local=d?.mode==='local_queue'&&d?.simulation===true;}).catch(()=>{})
    : Promise.resolve();
  const sleep = ms => new Promise(resolve=>setTimeout(resolve,ms));
  function error(message) { $('form-error').textContent=message; $('form-error').hidden=false; }
  function setBusy(value) { busy=value; $('submit-button').disabled=value; $('username').disabled=value; $('use-example').disabled=value; }
  function progress(value, message) {
    const percent=Math.min(100,Math.max(0,Math.round(value)));
    $('progress-fill').style.width=`${percent}%`; $('progress-number').textContent=`${percent}%`;
    $('progress-status').textContent=message;
  }
  async function cancelRemote(current) {
    if (!current) return;
    const response=await fetch(`/demo/jobs/${current.ticket_id}`, {method:'DELETE',headers:{Authorization:`Bearer ${current.access_token}`}});
    if (!response.ok) throw new Error('cancel_failed');
  }
  function render() {
    const query=$('search').value.trim().replace(/^@/,'').toLocaleLowerCase('pt-BR');
    const selected=people.filter(p=>(filter==='all'||(filter==='mutual'?p.mutual:!p.mutual))&&`${p.name} ${p.handle}`.toLocaleLowerCase('pt-BR').includes(query));
    $('people-list').replaceChildren();
    for (const person of selected.slice(0,limit)) {
      const row=document.createElement('li'); row.className='person';
      const avatar=document.createElement('span'); avatar.className='avatar';avatar.textContent=person.handle.slice(-2);avatar.setAttribute('aria-hidden','true');
      const text=document.createElement('div');text.className='person-text';
      const name=document.createElement('span');name.className='person-name';name.textContent=`@${person.handle}`;
      const handle=document.createElement('span');handle.className='person-handle';handle.textContent=`${person.name} · Você segue ✓`;
      text.append(name,handle);
      const relation=document.createElement('span');relation.className=`relationship ${person.mutual?'':'unknown'}`;
      relation.textContent=person.mutual?'Segue você ✓':'Segue você: não encontrado';
      row.append(avatar,text,relation);$('people-list').append(row);
    }
    $('empty-state').hidden=selected.length!==0;
    $('more-button').hidden=selected.length<=limit;
    $('list-count').textContent=`${Math.min(limit,selected.length)} de ${selected.length} perfis fictícios`;
    $('list-explanation').textContent=filter==='unseen'?'Você segue estes perfis, mas eles não aparecem entre os seguidores recuperados. São candidatos: a coleta deste exemplo é parcial.':filter==='mutual'?'Você segue estes perfis e eles seguem você nas amostras fictícias.':'Todas as contas que você segue neste exemplo fictício.';
  }
  function showResults() {
    setBusy(false);$('progress-section').hidden=true;$('results-section').hidden=false;
    $('result-date').textContent=`Exemplo aberto às ${new Date().toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}`;
    filter='unseen';limit=10;$('search').value='';
    document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.filter==='unseen')));
    render();$('results-title').focus({preventScroll:true});$('results-section').scrollIntoView({behavior:'smooth',block:'start'});
  }
  async function run() {
    if (busy) return;
    const username=$('username').value.trim().replace(/^@/,'').toLowerCase();
    $('form-error').hidden=true;
    if (!username) {error('Digite o perfil de exemplo ou use o botão abaixo.');$('username').focus();return;}
    if (!/^[a-z0-9_.]{1,30}$/.test(username)) {error('Use um @ com até 30 caracteres: letras, números, pontos ou sublinhados.');return;}
    if (username!==EXAMPLE) {error('As consultas reais ainda não estão disponíveis. Use @exemplo.byefan para experimentar, sem enviar seu perfil.');return;}
    const current=++generation;setBusy(true);ticket=null;
    $('results-section').hidden=true;$('progress-section').hidden=false;progress(0,'Preparando a demonstração…');
    $('progress-title').focus({preventScroll:true});$('progress-section').scrollIntoView({behavior:'smooth',block:'start'});
    try {
      await localReady;
      if(current!==generation)return;
      if(local) {
        const response=await fetch('/demo/jobs',{method:'POST',headers:{'X-ByeFan-Demo':'1'}});
        if(!response.ok)throw new Error(response.status===503?'paused':'unavailable');
        const created=await response.json();
        if(current!==generation){await cancelRemote(created);return;}
        ticket=created;
        const deadline=Date.now()+60000;
        while(current===generation&&Date.now()<deadline){
          const res=await fetch(`/demo/jobs/${created.ticket_id}`,{headers:{Authorization:`Bearer ${created.access_token}`}});
          if(!res.ok)throw new Error('status_failed');
          const data=await res.json();
          if(current!==generation)return;
          if(data.simulation!==true)throw new Error('unexpected_real_result');
          if(data.state==='completed'){ticket=null;showResults();return;}
          if(data.queue_paused||!['queued','running'].includes(data.state))throw new Error('interrupted');
          progress(data.progress_percent||0,data.state==='queued'?'Na fila da demonstração local…':'Preparando o exemplo na fila local…');
          await sleep(500);
        }
        if(current===generation)throw new Error('timeout');
      } else {
        for(const [value,message] of [[15,'Iniciando o exemplo no navegador…'],[42,'Organizando os dados fictícios…'],[74,'Preparando a indicação de dados parciais…'],[100,'Exemplo pronto.']]) {
          await sleep(650);if(current!==generation)return;progress(value,message);
        }
        if(current===generation)showResults();
      }
    } catch (e) {
      if(current!==generation)return;
      setBusy(false);$('progress-section').hidden=true;
      error('A demonstração foi interrompida. Nenhuma consulta real foi feita. Você pode tentar novamente quando o serviço local estiver disponível.');
      $('username').focus();
      const pending=ticket;ticket=null;
      try {await cancelRemote(pending);}catch{error('Não foi possível confirmar o encerramento da simulação local. Nenhuma consulta real foi feita.');}
    }
  }
  $('analysis-form').addEventListener('submit',e=>{e.preventDefault();run();});
  $('use-example').addEventListener('click',()=>{$('username').value=EXAMPLE;$('form-error').hidden=true;$('username').focus();});
  $('cancel-button').addEventListener('click',async()=>{
    ++generation;const pending=ticket;ticket=null;setBusy(false);$('progress-section').hidden=true;
    error('Demonstração cancelada. Nenhuma consulta real foi feita.');$('username').focus();
    try{await cancelRemote(pending);}catch{error('A interface foi interrompida, mas não conseguimos confirmar o cancelamento da simulação local.');}
  });
  $('reset-button').addEventListener('click',()=>{$('results-section').hidden=true;$('username').focus();$('experimentar').scrollIntoView({behavior:'smooth'});});
  document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{
    filter=button.dataset.filter;limit=10;document.querySelectorAll('[data-filter]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));render();
  }));
  $('search').addEventListener('input',()=>{limit=10;render();});
  $('more-button').addEventListener('click',()=>{limit+=10;render();});
  $('privacy-button').addEventListener('click',()=>$('privacy-dialog').showModal());
  for(const id of ['close-privacy','privacy-done'])$(id).addEventListener('click',()=>$('privacy-dialog').close());
})();
