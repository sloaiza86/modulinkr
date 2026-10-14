(() => {
  const box = document.getElementById('identidad-incidencias');
  let busy = false;
  const text = (tag, value) => { const e=document.createElement(tag); e.textContent=value; return e; };
  function render(state) {
    if (!box || busy) return;
    const records=(state?.identity?.incidents || []).filter(item=>['node_conflict','gateway_conflict'].includes(item.kind));
    box.replaceChildren();
    box.hidden=!records.length;
    for (const item of records) {
      const card=text('section',''); card.className='identidad-incidencia aviso mal';
      card.append(text('h3',item.origin===255 ? 'Conflicto entre gateways' : `Identidad del nodo ${item.origin}`));
      card.append(text('p',item.detail));
      if (item.kind==='node_conflict' || item.kind==='gateway_conflict') {
        const button=text('button','Resolver conflicto'); button.type='button';
        button.addEventListener('click',()=>resolve(item,(state.identity.sightings || []).filter(s=>item.origin!==255 || s.device_uid===state.deployment_id)));
        card.append(button);

      }
      box.append(card);
    }
  }
  function resolve(item,sightings) {
    const dialog=text('dialog',''); dialog.className='identidad-dialogo';
    dialog.setAttribute('aria-labelledby','identidad-titulo');
    const title=text('h2',item.origin===255 ? 'Resolver conflicto entre gateways' : `Resolver identidad del nodo ${item.origin}`); title.id='identidad-titulo';
    const form=document.createElement('form'); form.method='dialog';
    const label=text('label','Equipo que conservará este identificador');
    const select=document.createElement('select'); select.required=true;
    const empty=text('option','Selecciona el equipo'); empty.value=''; select.append(empty);
    for (const sighting of sightings.filter(s=>s.origin===item.origin)) {
      const option=text('option',`Equipo ${sighting.device_uid} · visto ${new Date(sighting.last_seen*1000).toLocaleString()}`);
      option.value=sighting.device_uid; select.append(option);
    }
    label.append(select);
    const confirmed=document.createElement('input'); confirmed.type='checkbox'; confirmed.required=true;
    const check=text('label',''); check.append(confirmed,document.createTextNode(' He desconectado el otro equipo o le he asignado otro identificador.'));
    const reasonLabel=text('label','Motivo de la resolución');
    const reason=document.createElement('textarea'); reason.required=true; reason.maxLength=500; reasonLabel.append(reason);
    const error=text('p',''); error.className='aviso mal'; error.setAttribute('role','alert');
    const cancel=text('button','Cancelar'); cancel.type='button'; cancel.addEventListener('click',()=>dialog.close());
    const submit=text('button','Confirmar resolución'); submit.type='submit';
    form.append(label,check,reasonLabel,error,cancel,submit);
    form.addEventListener('submit',async event=>{
      event.preventDefault();
      if(!form.reportValidity()) return;
      submit.disabled=true; busy=true;
      try {
        const response=await fetch('/api/identidad/resolver',{method:'POST',headers:{'Content-Type':'application/json','X-Modulinkr-Identity':'1'},body:JSON.stringify({origin:item.origin,device_uid:select.value,reason:reason.value,disconnected_other_equipment:confirmed.checked})});
        const result=await response.json();
        if(!response.ok) throw new Error(result.detail || 'No se pudo resolver la incidencia.');
        dialog.close();
      } catch(e) { error.textContent=e.message; }
      finally { busy=false; submit.disabled=false; }
    });
    dialog.append(title,text('p',item.origin===255 ? 'Conserva este gateway y desconecta el otro. Después reinicia los nodos que hayan detectado el conflicto.' : 'La resolución queda registrada. El equipo deberá volver a anunciar su catálogo antes de recibir operaciones.'),form);
    document.body.append(dialog);
    dialog.addEventListener('close',()=>{dialog.remove();busy=false;});
    dialog.showModal();
  }
  window.ModulinkrIdentity={render};
})();
