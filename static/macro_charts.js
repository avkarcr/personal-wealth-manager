/* M8: exact payload values, approximate SVG coordinates; no chart dependency. */
(() => {
  'use strict';
  const t = s => (window.UI_MESSAGES || {})[s] || s;
  const NS = 'http://www.w3.org/2000/svg';
  const phaseColors = {RECOVERY:'--c1',EARLY_EXPANSION:'--c3',LATE_EXPANSION:'--c2',SLOWDOWN:'--c4',RECESSION:'--neg',INSUFFICIENT_DATA:'--muted'};
  function number(value) {
    if (value === null || value === undefined || typeof value === 'boolean' || String(value).trim() === '') return null;
    const n = Number(value); return Number.isFinite(n) ? n : null;
  }
  function bounds(values, includeZero=false) {
    const valid=values.map(number).filter(v=>v!==null);if(!valid.length)return null;
    let low=valid[0],high=valid[0];for(const v of valid){low=Math.min(low,v);high=Math.max(high,v);}
    if(includeZero){low=Math.min(0,low);high=Math.max(0,high);}
    const pad=(high-low)*.1 || Math.max(1,Math.abs(high)*.1);
    return [low-pad,high+pad];
  }
  function segments(points) {
    const result=[];let part=[];
    for(const p of points){
      if(p.break_before || number(p.value)===null){if(part.length)result.push(part);part=[];}
      if(number(p.value)!==null)part.push(p);
    }
    if(part.length)result.push(part);return result;
  }
  function nearest(values,target) {
    if(!values.length)return -1;
    let lo=0,hi=values.length-1;
    while(lo<hi){const mid=Math.floor((lo+hi)/2);if(values[mid]<target)lo=mid+1;else hi=mid;}
    return lo>0 && Math.abs(values[lo-1]-target)<=Math.abs(values[lo]-target)?lo-1:lo;
  }
  function step(index,key,length) {
    if(!length)return -1;
    if(key==='Home')return 0;if(key==='End')return length-1;
    if(key==='ArrowLeft')return Math.max(0,index-1);if(key==='ArrowRight')return Math.min(length-1,index+1);
    return index;
  }
  function svg(tag,attrs={},text) {
    const el=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))el.setAttribute(k,String(v));
    if(text!==undefined)el.textContent=text;return el;
  }
  function mount(box,readout,data) {
    const points=data.points,locale=document.documentElement.lang==='en'?'en-GB':'ru-RU';
    const format=new Intl.NumberFormat(locale,{maximumFractionDigits:3,notation:'compact'});
    const indexFormat=new Intl.NumberFormat(locale,{maximumFractionDigits:2});
    const score=value=>number(value)===null?'—':indexFormat.format(number(value));
    const time=new Intl.DateTimeFormat(locale,{month:'short',year:'2-digit',timeZone:'UTC'});
    const isPhase=data.axis==='phase',isTenor=data.axis==='tenor';
    const values=points.map(p=>isTenor?Number(p.x):Date.parse(p.date+'T00:00:00Z'));
    let index=points.length-1,cursor,marker,root,plotLeft,plotRight,xmin,xmax,x;
    function describe(p) {
      const pieces=[isTenor?`${p.x} ${t('лет')} · ${p.date || '—'}`:p.date];
      if(isPhase){pieces.push(data.phase_labels[p.phase],`${t('Индекс уверенности')}: ${score(p.confidence)}`,`${t('Покрытие активных весов')}: ${score(p.coverage)}%`);}
      else{pieces.push(number(p.value)===null?t('Нет данных'):`${p.value} ${data.unit_label}`);if(p.revisions.length)pieces.push(`${t('Версии наблюдений')}: ${p.revisions.join(', ')}`);}
      return pieces.join(' · ');
    }
    function select(at,announce=true) {
      if(at<0 || !points.length)return;
      index=at;const p=points[index],px=x(values[index]);
      cursor.setAttribute('x1',px);cursor.setAttribute('x2',px);
      const visible=number(p.value)!==null;
      marker.setAttribute('visibility',visible?'visible':'hidden');marker.setAttribute('cx',px);
      marker.setAttribute('cy',isPhase?120:marker.y(number(p.value)));
      marker.setAttribute('fill',isPhase?`var(${phaseColors[p.phase]})`:'var(--accent)');
      if(announce)readout.textContent=describe(p);
    }
    function draw() {
      const width=Math.max(260,box.clientWidth || 700),height=280;
      box.replaceChildren();root=svg('svg',{viewBox:`0 0 ${width} ${height}`,role:'img','aria-label':box.getAttribute('aria-label')});
      root.appendChild(svg('title',{},t('История показателей')));box.appendChild(root);
      if(!points.length || (!isPhase && !points.some(p=>number(p.value)!==null))){root.appendChild(svg('text',{x:width/2,y:130,'text-anchor':'middle',class:'macro-axis-label'},t('Нет данных')));return;}
      plotLeft=isPhase?16:65;plotRight=width-16;
      xmin=isTenor?1:Date.parse(data.start+'T00:00:00Z');xmax=isTenor?10:Date.parse(data.end+'T00:00:00Z');
      if(xmin===xmax){xmin-=43200000;xmax+=43200000;}
      x=v=>plotLeft+(v-xmin)/(xmax-xmin)*(plotRight-plotLeft);
      const limits=isPhase?[0,1]:bounds(points.map(p=>p.value),['gap','spread'].includes(data.kind));
      const y=v=>220-(v-limits[0])/(limits[1]-limits[0])*195;
      if(!isPhase){
        for(let i=0;i<=4;i++){const n=limits[0]+(limits[1]-limits[0])*i/4,py=y(n);
          root.appendChild(svg('line',{x1:plotLeft,x2:plotRight,y1:py,y2:py,class:'macro-grid-line'}));
          root.appendChild(svg('text',{x:plotLeft-8,y:py+4,'text-anchor':'end',class:'macro-axis-label'},format.format(n)));}
        if(limits[0]<0 && limits[1]>0)root.appendChild(svg('line',{x1:plotLeft,x2:plotRight,y1:y(0),y2:y(0),class:'macro-zero-line'}));
      }
      const ticks=isTenor?[1,2,10]:[0,.5,1].map(f=>xmin+(xmax-xmin)*f);
      for(let i=0;i<ticks.length;i++)root.appendChild(svg('text',{x:x(ticks[i]),y:252,'text-anchor':i===0?'start':i===ticks.length-1?'end':'middle',class:'macro-axis-label'},isTenor?String(ticks[i]):time.format(new Date(ticks[i]))));
      if(isTenor)root.appendChild(svg('text',{x:(plotLeft+plotRight)/2,y:275,'text-anchor':'middle',class:'macro-axis-label'},t('Срок, лет')));
      if(isPhase){
        const markerWidth=Math.max(3,Math.min(24,(plotRight-plotLeft)/Math.max(1,points.length)*.65));
        points.forEach((p,i)=>root.appendChild(svg('rect',{x:x(values[i])-markerWidth/2,y:82,width:markerWidth,height:76,rx:3,fill:`var(${phaseColors[p.phase]})`,opacity:p.phase==='INSUFFICIENT_DATA'?.35:.8})));
      }else{
        for(const part of segments(points)){
          const path=part.map((p,i)=>`${i?'L':'M'}${x(isTenor?Number(p.x):Date.parse(p.date+'T00:00:00Z'))},${y(number(p.value))}`).join(' ');
          root.appendChild(svg('path',{d:path,fill:'none',stroke:'var(--accent)','stroke-width':2.4,'stroke-linejoin':'round'}));
          if(part.length===1 || points.length<25)for(const p of part)root.appendChild(svg('circle',{cx:x(isTenor?Number(p.x):Date.parse(p.date+'T00:00:00Z')),cy:y(number(p.value)),r:3,fill:'var(--accent)'}));
        }
      }
      cursor=svg('line',{y1:25,y2:220,class:'macro-cursor'});root.appendChild(cursor);
      marker=svg('circle',{r:5,stroke:'var(--surface)','stroke-width':2});marker.y=y;root.appendChild(marker);
      select(index);
    }
    function pointer(event){
      if(!cursor || !points.length)return;
      const px=event.clientX-box.getBoundingClientRect().left,target=xmin+(Math.min(plotRight,Math.max(plotLeft,px))-plotLeft)/(plotRight-plotLeft)*(xmax-xmin);
      select(nearest(values,target));
    }
    box.addEventListener('pointermove',pointer);
    box.addEventListener('pointerdown',event=>{box.focus();pointer(event);});
    box.addEventListener('keydown',event=>{if(!cursor || !['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();select(step(index,event.key,points.length));});
    draw();if(typeof ResizeObserver!=='undefined')new ResizeObserver(draw).observe(box);
    return {draw,select};
  }
  window.MacroCharts={number,bounds,segments,nearest,step,mount};
  const payload=document.getElementById('macro-chart-data'),box=document.getElementById('macro-plot'),readout=document.getElementById('macro-readout');
  if(payload && box && readout)mount(box,readout,JSON.parse(payload.textContent));
})();
