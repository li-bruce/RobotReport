/* Venue labels describe the space; scheduling and admission remain separate. */
function venueAttributeList(attributes) {
  if (!Array.isArray(attributes) || !attributes.length) return '';
  return `<dl class="venue-attributes">${attributes.map(item=>`<div><dt>${e(item.label)}</dt><dd>${e(item.value)}</dd></div>`).join('')}</dl>`;
}

async function renderVenueDirectory(epoch) {
  const result = await api('/api/venues');
  if (!isCurrent(epoch)) return;
  state.venues = asList(result, 'venues');
  $('#main').innerHTML = head('场地规划', '管理场地信息、属性与活动安排。', btn(`${icon('plus')}新增场地`, 'venue-new', 'primary'), 'VENUE DIRECTORY') +
    `<div class="grid-3 venue-directory">${state.venues.map(v=>`<article class="tournament-card" data-venue-card="${attr(v.id)}"><div class="card-body"><div class="card-top"><span class="badge blue">场地 ${e(v.id)}</span>${badge(!isActive(v.active)?'archived':'active')}</div><h3>${e(v.name)}</h3>${v.description?`<p class="venue-description muted">${e(v.description)}</p>`:''}${venueAttributeList(v.attributes)}</div><div class="card-bottom">${btn('编辑场地','venue-edit','small',`data-id="${attr(v.id)}"`)}${btn('安排时段','venue-slots','small primary',`data-id="${attr(v.id)}"`)}</div></article>`).join('')}</div>` +
    (!state.venues.length ? empty('添加第一块活动场地', '例如：体育馆 1 号场。', btn('新增场地','venue-new','primary')) : '');
}

function editVenueDetails(id) {
  const venue = state.venues.find(x=>String(x.id)===String(id)) || {};
  openModal(id?'编辑场地':'新增场地', `<form id="venue-form">${field('场地名称','name',venue.name||'','text','required maxlength="80" placeholder="例如：体育馆 1 号场"')}${textfield('位置与说明','description',venue.description||'','maxlength="1000"')}<section class="venue-attribute-editor" aria-labelledby="venue-attribute-heading"><div class="venue-attribute-heading"><h3 id="venue-attribute-heading">场地属性</h3><button type="button" class="btn small" data-attribute-add>添加属性</button></div><p class="caption">例如：运动类型、室内外、地面材质、配套设施。</p><datalist id="venue-attribute-suggestions">${['运动类型','室内外','地面材质','配套设施','面积','可容纳人数'].map(x=>`<option value="${x}"></option>`).join('')}</datalist><div data-attribute-rows></div><p class="venue-attribute-empty muted">暂无属性</p></section>${id?`<label class="check-label"><input type="checkbox" name="active"${isActive(venue.active)?' checked':''}>启用此场地</label><p class="caption">停用后保留历史记录。</p>`:''}${formFooter(id?'保存场地':'创建场地')}</form>`);
  const form = $('#venue-form'), rows = $('[data-attribute-rows]', form), add = $('[data-attribute-add]', form);
  form.addEventListener('input',()=>{ $('.inline-error',form).textContent=''; });
  let next = 0;
  const refresh = () => {
    const items = [...rows.children];
    add.disabled = items.length >= 20;
    $('.venue-attribute-empty',form).hidden = items.length > 0;
    items.forEach((row,index)=> {
      $('[data-attribute-number]',row).textContent = String(index+1).padStart(2,'0');
      $('[data-attribute-remove]',row).setAttribute('aria-label',`移除第 ${index+1} 项属性`);
    });
  };
  const append = (item={}, focus=false) => {
    if(rows.children.length>=20) return;
    const uid = ++next;
    const row = document.createElement('div');
    row.className = 'venue-attribute-row';
    row.innerHTML = `<span data-attribute-number aria-hidden="true"></span><div class="field"><label for="venue-attribute-label-${uid}">属性名称</label><input id="venue-attribute-label-${uid}" data-attribute-label list="venue-attribute-suggestions" value="${attr(item.label||'')}" maxlength="24" placeholder="例如：运动类型"></div><div class="field"><label for="venue-attribute-value-${uid}">属性内容</label><input id="venue-attribute-value-${uid}" data-attribute-value value="${attr(item.value||'')}" maxlength="120" placeholder="例如：羽毛球、篮球"></div><button type="button" class="btn small ghost" data-attribute-remove>移除</button>`;
    row.querySelector('[data-attribute-remove]').addEventListener('click',()=> {
      const target = row.nextElementSibling?.querySelector('input') || row.previousElementSibling?.querySelector('input') || add;
      row.remove();refresh();target.focus();
    });
    rows.append(row);refresh();
    if(focus) $('[data-attribute-label]',row).focus();
  };
  (venue.attributes||[]).forEach(item=>append(item));
  refresh();add.addEventListener('click',()=>append({},true));
  bindForm('venue-form',async(data,element,epoch)=> {
    const attributes=[];
    for(const row of rows.children) {
      const label=$('[data-attribute-label]',row).value.trim(), value=$('[data-attribute-value]',row).value.trim();
      if(!label&&!value) continue;
      if(!label||!value) {
        $(!label?'[data-attribute-label]':'[data-attribute-value]',row).focus();
        throw new Error('请补全属性名称和内容，或移除该项。');
      }
      attributes.push({label,value});
    }
    await api(id?`/api/venues/${encodeURIComponent(id)}`:'/api/venues',{method:id?'PATCH':'POST',body:{name:data.get('name').trim(),description:data.get('description').trim(),active:id?data.has('active'):true,attributes}});
    if(epoch!==state.epoch)return;
    closeModal();activityChanged();toast('场地已保存。');await loadContext();renderShell();await renderRoute();
  });
}
