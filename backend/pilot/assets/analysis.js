'use strict';
// Loaded only by the loopback server; never part of the hosted static bundle.
(() => {
  const make=(tag,text,className)=>{const e=document.createElement(tag);if(text)e.textContent=text;if(className)e.className=className;return e;};
  async function start(){
    const config=await fetch('/demo/config').then(r=>r.json());
    const target=config.target_username||'example_target';
    const observer=config.observer_username||'example_observer';
    if(!config.saved_analysis_available&&!config.live_collection_configured)return;
    const section=make('section',null,'wrap saved-analysis');section.id='saved-analysis';
    section.setAttribute('aria-labelledby','saved-title');
    const title=make('h2','Quem pode não seguir você de volta?');title.id='saved-title';title.tabIndex=-1;
    const open=make('button','Ver minha lista salva ↗','primary');open.id='open-saved';
    open.hidden=!config.saved_analysis_available;
    const intro=make('p',config.live_collection_configured?`Piloto local para @${target}. A consulta usa a sessão já existente de @${observer}, sem pedir sua senha.`:'Disponível somente neste computador. Nenhuma consulta nova será feita. A data e as limitações da coleta aparecem no relatório.','muted');
    const state=make('p','','saved-status');state.id='saved-status';state.setAttribute('role','status');
    const content=make('div');content.id='saved-content';
    section.append(make('p','DADOS REAIS · SOMENTE NESTE COMPUTADOR','eyebrow'),title,intro,open,state,content);
    document.querySelector('.hero').after(section);
    const shortcut=make('a',config.live_collection_configured?'Abrir piloto real local ↓':'Ver minha coleta salva ↓','example-button');shortcut.href='#saved-analysis';
    document.querySelector('#analysis-form').after(shortcut);
    document.querySelector('.preview-bar').textContent=config.live_collection_configured?'PILOTO LOCAL · Uma tentativa para o perfil autorizado.':'PRÉVIA LOCAL · Demonstração e leitura de coleta salva. Nenhuma consulta nova.';
    const privacy=make('p',config.live_collection_configured?`O operador habilitou uma tentativa real para @${target}. Ela começa somente pelo botão do piloto local e pode ser cancelada. A sessão do navegador e os resultados permanecem neste computador.`:'Nesta versão local também é possível abrir uma coleta real já salva pelo proprietário. Esses dados ficam neste computador e não são enviados à hospedagem da prévia.');
    document.querySelector('#privacy-done').before(privacy);
    let data=null,filter='following_not_observed_followers',limit=20;
    if(config.live_collection_configured){
      const controls=make('div',null,'saved-live-controls');
      const collect=make('button',`Consultar @${target} ↗`,'primary');collect.id='start-live';
      collect.disabled=!config.live_collection_available;
      const cancel=make('button','Cancelar consulta','text-button');cancel.id='cancel-live';cancel.hidden=true;
      const policy=make('p',config.live_collection_available?'Uma tentativa, até 710 s de trabalho e 300 rolagens por lista. Meta de cobertura: 98%, com resultado parcial. Interrompe diante de restrições; não repete automaticamente.':'Esta autorização já foi utilizada. Uma nova tentativa exige nova autorização do proprietário.','muted');
      controls.append(policy,collect,cancel);open.before(controls);
      let ticket=null;
      cancel.addEventListener('click',async()=>{
        if(!ticket)return;
        cancel.disabled=true;state.textContent='Cancelamento solicitado. Aguardando o navegador encerrar a operação atual…';
        try{
          const response=await fetch(`/local/collection/${ticket.ticket_id}`,{method:'DELETE',headers:{'X-ByeFan-Local':'1',Authorization:`Bearer ${ticket.access_token}`}});
          if(!response.ok)throw new Error('cancel_failed');
        }catch{cancel.disabled=false;state.textContent='Não foi possível confirmar o cancelamento. Encerre o servidor local com Ctrl+C para solicitar a parada.';}
      });
      collect.addEventListener('click',async()=>{
        collect.disabled=true;open.disabled=true;content.replaceChildren();
        state.textContent='Iniciando a tentativa autorizada…';
        try{
          const response=await fetch('/local/collection',{method:'POST',headers:{'X-ByeFan-Local':'1'}});
          if(!response.ok)throw new Error('start_failed');
          ticket=await response.json();cancel.hidden=false;
          while(true){
            const current=await fetch(`/local/collection/${ticket.ticket_id}`,{headers:{Authorization:`Bearer ${ticket.access_token}`}});
            if(!current.ok)throw new Error('status_failed');
            const status=await current.json();
            if(status.simulation!==false||status.target!==target)throw new Error('wrong_mode');
            if(status.state==='completed'){
              data=status.result;
              if(!data||data.mode!=='live_observation'||data.simulation!==false||data.target!==target)throw new Error('invalid_result');
              filter='following_not_observed_followers';limit=20;
              state.textContent='Tentativa encerrada. Confira a data e as limitações do resultado abaixo.';
              show();break;
            }
            if(status.state!=='running'){
              const messages={cancelled:'Consulta cancelada. Não há resultado definitivo.',restricted:'Não foi possível confirmar a sessão ou manter o acesso. A execução foi interrompida; verifique a conta manualmente na interface oficial.',interrupted:'A coleta foi interrompida. Nenhum resultado definitivo foi liberado.'};
              const errors={existing_browser_profile_missing:'A sessão local não foi encontrada.',worker_already_active:'A sessão já está em uso por outro coletor.',insufficient_or_inconsistent_data:'Os dados não foram suficientes para uma comparação segura.',global_time_limit:'O limite de tempo foi atingido.',target_profile_unavailable:'O perfil autorizado não ficou disponível.'};
              state.textContent=`${messages[status.state]||'Tentativa indisponível.'} ${errors[status.error]||''} Não haverá nova tentativa automática.`;
              break;
            }
            const progress=status.progress;
            state.textContent=status.cancel_requested?'Cancelamento solicitado. Aguardando encerrar a operação atual…':`Coletando · ${Math.round(progress.elapsed_seconds)} s · ${progress.followers} seguidores e ${progress.following} contas seguidas recuperados. Contagens parciais.`;
            await new Promise(resolve=>setTimeout(resolve,1500));
          }
          cancel.hidden=true;ticket=null;open.disabled=false;
        }catch{
          state.textContent='Não foi possível acompanhar a tentativa. Ela pode ter sido iniciada; não vamos repeti-la. Use Cancelar, se disponível, ou Ctrl+C no servidor local.';
        }
      });
    }
    open.addEventListener('click',async()=>{
      open.disabled=true;state.textContent='Lendo a análise local…';
      try{
        const response=await fetch('/local/analysis',{method:'POST',headers:{'X-ByeFan-Local':'1'}});
        if(!response.ok)throw new Error('unavailable');
        data=await response.json();
        if(data.simulation!==false||data.mode!=='saved_observation'||data.target!==target)throw new Error('invalid_source');
        open.hidden=true;state.textContent='Arquivo analisado. Este relatório não representa uma consulta em tempo real.';
        show();title.focus({preventScroll:true});section.scrollIntoView({behavior:'smooth',block:'start'});
      }catch{state.textContent='Não foi possível abrir a coleta salva. Nenhuma consulta ao Instagram foi feita.';open.disabled=false;}
    });
    function show(){
      content.replaceChildren();
      const verified=data.non_followers!==null;
      title.textContent=verified?'Quem não segue você de volta.':'Quem pode não seguir você de volta?';
      const primaryRows=verified?data.non_followers:data.following_not_observed_followers;
      const date=new Date(data.observed_at).toLocaleString('pt-BR',{dateStyle:'long',timeStyle:'short'});
      content.append(make('p',`@${data.target} · Coleta de ${date}`,'muted'));
      const highlight=make('div',null,'saved-highlight');
      const total=make('strong',String(primaryRows.length));total.id='saved-primary-count';
      highlight.append(total,make('p',verified?'perfis que você segue e não seguem você de volta na coleta validada.':'perfis que você segue e não foram encontrados entre os seguidores recuperados.'));
      content.append(highlight);
      content.append(make('p',verified?'Validado para a data da coleta com referência independente e repetição consistente. Isso não identifica quem deixou de seguir no passado.':'Lista parcial · Estes são candidatos a não seguir de volta. Um seguidor ausente na coleta pode aparecer aqui por engano. A relação pode ter mudado desde a coleta.','saved-notice'));
      const quality=make('details',null,'saved-quality');
      quality.append(make('summary','Sobre os dados desta lista'));
      const metrics=make('div',null,'saved-metrics');
      for(const [direction,label] of [['followers','Seguidores'],['following','Seguindo']]){
        const c=data.coverage.lists[direction];metrics.append(make('div',`${label}: ${c.unique_structured_ids} / ${c.reported_count??'desconhecido'} · cobertura ${c.observed_coverage_percent??'desconhecida'}%`));
      }
      quality.append(metrics,make('p',verified?'As duas listas passaram pelo validador de integridade.':'Falta validar a integridade das listas com uma referência independente. Cobertura não mede a precisão da classificação.'));
      content.append(quality);
      const tabs=make('div',null,'saved-tabs');tabs.setAttribute('role','group');tabs.setAttribute('aria-label','Filtrar observações reais');
      const labels={following_not_observed_followers:verified?'Não seguem de volta':'Possíveis não seguidores',observed_mutual:'Vocês se seguem',followers_not_observed_following:'Seguem você · demais perfis'};
      const descriptions={
        following_not_observed_followers:verified?'Você segue estes perfis. Eles não seguem você na coleta validada.':'Você segue estes perfis. Eles não aparecem entre os seguidores recuperados.',
        observed_mutual:'Estes perfis aparecem nas duas listas: você segue e eles seguem você.',
        followers_not_observed_following:verified?'Estes perfis seguem você, mas você não os segue na coleta validada.':'Estes perfis seguem você, mas não foram encontrados entre as contas que você segue.'
      };
      const description=make('p',null,'muted');description.id='saved-list-description';
      const searchLabel=make('label','Buscar nesta lista','saved-search');
      const search=make('input');search.type='search';search.id='saved-search';search.maxLength=60;searchLabel.append(search);
      search.placeholder='Nome de usuário ou @';search.autocapitalize='none';search.spellcheck=false;
      const list=make('ul',null,'people-list');list.id='saved-people';
      list.setAttribute('aria-describedby','saved-list-description');
      const count=make('p','','helper');count.id='saved-count';count.setAttribute('aria-live','polite');
      const more=make('button','Mostrar mais ↓','text-button');more.id='saved-more';
      function render(){
        const q=search.value.trim().replace(/^@/,'').toLowerCase();
        const source=filter==='following_not_observed_followers'?primaryRows:data[filter];
        const rows=source.filter(p=>p.username.toLowerCase().includes(q));list.replaceChildren();
        description.textContent=descriptions[filter];list.setAttribute('aria-label',labels[filter]);
        for(const row of rows.slice(0,limit)){
          const item=make('li',null,'person');
          const avatar=make('span',row.username.slice(0,2).toUpperCase(),'avatar');avatar.setAttribute('aria-hidden','true');
          const person=make('div',null,'saved-person-text');
          person.append(make('strong',`@${row.username}`,'person-name'));
          const relation=make('div',null,'saved-directions');
          const follows=filter!=='followers_not_observed_following';
          const followed=filter!=='following_not_observed_followers';
          relation.append(make('span',follows?'Você segue ✓':verified?'Você não segue':'Você segue: não encontrado'));
          relation.append(make('span',followed?'Segue você ✓':verified?'Não segue você':'Segue você: não encontrado',followed?'':'saved-unconfirmed'));
          person.append(relation);
          const link=make('a','Abrir perfil ↗','saved-profile-link');link.href=`https://www.instagram.com/${encodeURIComponent(row.username)}/`;
          link.target='_blank';link.rel='noopener noreferrer';link.referrerPolicy='no-referrer';
          link.setAttribute('aria-label',`Abrir @${row.username} no Instagram em nova aba`);
          item.append(avatar,person,link);
          list.append(item);
        }
        count.textContent=rows.length?`Mostrando ${Math.min(limit,rows.length)} de ${rows.length} perfis${q?' nesta busca':''}`:q?'Nenhum resultado nesta busca.':'Nenhum perfil nesta categoria.';
        more.hidden=rows.length<=limit;
        for(const button of tabs.children)button.setAttribute('aria-pressed',String(button.dataset.key===filter));
      }
      for(const [key,label] of Object.entries(labels)){
        const button=make('button',`${label} (${key==='following_not_observed_followers'?primaryRows.length:data.counts[key]})`);button.dataset.key=key;
        button.addEventListener('click',()=>{filter=key;limit=20;render();});tabs.append(button);
      }
      search.addEventListener('input',()=>{limit=20;render();});more.addEventListener('click',()=>{limit+=20;render();});
      content.append(tabs,description,searchLabel,list,count,more,make('p','Os perfis só são abertos no Instagram se você clicar em “Abrir perfil”. O ByeFan não altera quem você segue.','helper'));
      const history=make('details',null,'saved-history');
      history.append(make('summary','Isso mostra quem parou de me seguir?'));
      if(data.history.no_longer_observed_followers===null){
        history.append(make('p','Ainda não há histórico suficiente para responder. É necessário comparar registros anteriores confiáveis com uma coleta posterior.'));
      }else{
        history.append(make('p',`${data.history.no_longer_observed_followers.length} perfis deixaram de aparecer entre as amostras. Essa diferença não confirma unfollow: faltas na coleta, bloqueio ou desativação também podem explicar a ausência.`));
        history.append(make('p',`${data.history.no_longer_observed_followers_still_followed.length} deles aparecem na lista de contas que você segue na coleta posterior.`));
      }
      content.append(history);render();
    }
  }
  start().catch(()=>{});
})();
