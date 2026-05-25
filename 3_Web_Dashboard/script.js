/**
 * FloodMind-AIoT DSS — Decision Support Dashboard
 * Supports both legacy (v1) and new (v2) MQTT payloads.
 */
(function () {
  'use strict';
  const STATIONS = [
    { id:'THAI_HA', name:'Thái Hà', lat:21.01205707861206, lon:105.82107114719774 },
    { id:'PHAM_NGOC_THACH', name:'Phạm Ngọc Thạch', lat:21.009224497039224, lon:105.8348400357426 },
    { id:'TRUONG_CHINH', name:'Trường Chinh', lat:21.001455046307868, lon:105.8261326019895 },
  ];
  const SC = {
    'THAI_HA':{ border:'#ff6b6b', bgTop:'rgba(255,107,107,0.4)', bgBot:'rgba(255,107,107,0)' },
    'PHAM_NGOC_THACH':{ border:'#3fb950', bgTop:'rgba(63,185,80,0.4)', bgBot:'rgba(63,185,80,0)' },
    'TRUONG_CHINH':{ border:'#58a6ff', bgTop:'rgba(88,166,255,0.4)', bgBot:'rgba(88,166,255,0)' },
  };
  const CFG = {
    MQTT_WS_URL:'wss://broker.emqx.io:8084/mqtt',
    TOPIC_FLOOD:'flood/monitor/+/data', TOPIC_AI:'ai/prediction/+', TOPIC_OVERALL:'ai/prediction/overall',
    TOPIC_CMD:'sensor/command',
    CHART_MAX:60, SAFE:30, WARN:50, CRIT:80, FLOW_MAX:25,
    OPEN_METEO:'https://api.open-meteo.com/v1/forecast',
    STALE_MS:15000, OFFLINE_MS:30000,
  };
  const GR=48, GC=2*Math.PI*GR;
  let gLabels=[], store={}, mqttC=null, map=null, markers={};
  let cLevel=null, cFlow=null, cRain=null;
  const S = {
    sel:STATIONS[0].id, t0:Date.now(), mute:true,
    lastSensor:{}, lastAI:null, mqttOk:false,
    dL:null,dF:null,dE:null,dR:null,
    lastSensorTime:{}, overall:null,
  };
  const el=id=>document.getElementById(id);
  const pad=n=>String(n).padStart(2,'0');
  const pn=(v,fb)=>{const n=parseFloat(v);return Number.isFinite(n)?n:fb;};

  function ensure(sid){
    if(!store[sid]) store[sid]={level:[],flow:[],rain:[],lastSensor:{level:null,flow:null,rain:null},lastAI:null};
    return store[sid];
  }

  // ── Tween ──
  function tween(from,to,ms,fn){
    const t0=performance.now();
    (function f(t){const u=Math.min(1,(t-t0)/ms),e=1-Math.pow(1-u,3);fn(from+(to-from)*e);if(u<1)requestAnimationFrame(f);})(t0);
  }
  function animN(id,from,to,d,cb){tween(pn(from,0),pn(to,0),300,v=>{if(id)el(id).textContent=v.toFixed(d);if(cb)cb(v);});}

  // ── Gauge helpers ──
  function setArc(id,pct){const a=el(id);if(a)a.style.strokeDashoffset=String(GC*(1-Math.min(100,Math.max(0,pct))/100));}
  function initArcs(){['gaugeLevelArc','gaugeFlowArc','gaugeAiArc'].forEach(id=>{const a=el(id);if(a){a.style.strokeDasharray=String(GC);a.style.strokeDashoffset=String(GC);}});}

  // ── Risk color ──
  function riskColor(rs){
    if(rs>=75) return '#ff4444';
    if(rs>=50) return '#f0b429';
    if(rs>=30) return '#58a6ff';
    return '#3fb950';
  }

  function overallRiskColor(rs){
    if(rs>=75) return '#ff4444';
    if(rs>=50) return '#f0b429';
    if(rs>=30) return '#58a6ff';
    return '#3fb950';
  }

  // ── Clock + freshness ──
  function tick(){
    const d=new Date();
    el('clock').textContent=`${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
    if(el('valUptime')){const ms=Date.now()-S.t0;const s=Math.floor(ms/1000);el('valUptime').textContent=`${pad(Math.floor(s/3600))}:${pad(Math.floor(s/60)%60)}:${pad(s%60)}`;}
    const sid=S.sel, lt=S.lastSensorTime[sid];
    if(lt){
      const age=Date.now()-lt;
      const fe=el('valFreshness');
      if(fe){
        if(age>CFG.OFFLINE_MS) fe.textContent='⛔ Mất kết nối trạm';
        else if(age>CFG.STALE_MS) fe.textContent='⚠️ Dữ liệu chậm';
        else fe.textContent='✅ Realtime';
      }
      
      // Update banner dynamically if offline
      if(age>CFG.OFFLINE_MS && S.lastAI) {
          setBanner(S.lastAI);
      }
    }
  }

  function setMqtt(ok){S.mqttOk=ok;const w=el('mqttLed'),l=el('mqttLabel');w.classList.toggle('mqtt-led--ok',ok);w.classList.toggle('mqtt-led--bad',!ok);l.textContent=ok?'MQTT OK':'MQTT OFF';}

  function pubCmd(obj){if(!mqttC||!S.mqttOk)return;mqttC.publish(CFG.TOPIC_CMD,JSON.stringify(obj),{qos:1});}

  // ── Charts ──
  function areaGrad(stops){return ctx=>{const c=ctx.chart,{ctx:cx,chartArea:a}=c;if(!a)return stops.bottom;const g=cx.createLinearGradient(0,a.bottom,0,a.top);g.addColorStop(0,stops.bottom);g.addColorStop(1,stops.top);return g;};}
  function chartOpts(){return{responsive:true,maintainAspectRatio:false,animation:{duration:300},interaction:{mode:'index',intersect:false},scales:{x:{ticks:{maxTicksLimit:8,color:'#8b949e',font:{size:10}},grid:{display:false},border:{display:false}},y:{beginAtZero:true,ticks:{color:'#8b949e',font:{size:10}},grid:{color:'rgba(255,255,255,0.035)'},border:{display:false}}},plugins:{legend:{display:false}},elements:{point:{radius:0,hitRadius:5},line:{borderWidth:2.5,tension:0.4}}};}
  function buildDS(metric){return STATIONS.map(st=>{const c=SC[st.id],s=ensure(st.id),sel=st.id===S.sel;return{label:st.name,data:s[metric],borderColor:sel?c.border:c.border+'33',backgroundColor:areaGrad({bottom:c.bgBot,top:sel?c.bgTop:'rgba(0,0,0,0)'}),fill:true,borderWidth:sel?2.5:1.5};});}
  function updateCharts(){if(cLevel){cLevel.data.datasets=buildDS('level');cLevel.update('none');}if(cFlow){cFlow.data.datasets=buildDS('flow');cFlow.update('none');}if(cRain){cRain.data.datasets=buildDS('rain');cRain.update('none');}}
  function initCharts(){cLevel=new Chart(el('chartLevel'),{type:'line',data:{labels:gLabels,datasets:buildDS('level')},options:chartOpts()});cFlow=new Chart(el('chartFlow'),{type:'line',data:{labels:gLabels,datasets:buildDS('flow')},options:chartOpts()});cRain=new Chart(el('chartRain'),{type:'line',data:{labels:gLabels,datasets:buildDS('rain')},options:chartOpts()});}

  function pushTime(){const d=new Date();gLabels.push(`${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`);if(gLabels.length>CFG.CHART_MAX)gLabels.shift();}
  function pushStore(sid,l,f,r){const s=ensure(sid);s.level.push(l);s.flow.push(f);s.rain.push(r);if(s.level.length>CFG.CHART_MAX){s.level.shift();s.flow.shift();s.rain.shift();}}

  // ── DSS UI updates ──
  function updateRiskScore(rs){
    const e=el('valRiskScore');if(e)e.textContent=Math.round(rs);
    const c=el('riskScoreCircle');if(c)c.style.background=`conic-gradient(${riskColor(rs)} ${rs*3.6}deg, rgba(255,255,255,0.08) 0deg)`;
    const lb=el('riskScoreLabel');
    if(lb){if(rs>=75)lb.textContent='NGUY HIỂM';else if(rs>=50)lb.textContent='CẢNH BÁO';else if(rs>=30)lb.textContent='THEO DÕI';else lb.textContent='AN TOÀN';}
  }
  function updateExplanation(reasons){
    const ul=el('explanationList');if(!ul)return;
    if(!reasons||!reasons.length){ul.innerHTML='<li class="explanation-item">Chưa có phân tích</li>';return;}
    ul.innerHTML=reasons.map(r=>`<li class="explanation-item">💡 ${r}</li>`).join('');
  }
  function updateScenario(ai){
    const se=el('scenarioText');if(!se)return;
    const parts=[];
    const pl=ai&&(ai.forecast?ai.forecast.predicted_level_5min:ai.predicted_level_5min);
    const rs=ai&&(ai.ai?ai.ai.risk_score:ai.risk_score);
    const dq=ai&&ai.data_quality;
    if(pl!=null&&pl>0) parts.push(`Nếu xu hướng hiện tại tiếp tục, mực nước có thể đạt ${pl} cm sau 5 phút.`);
    if(rs!=null&&rs>75) parts.push('⚠️ Kịch bản nguy hiểm: cần bật cảnh báo và kiểm tra khu vực trạm ngay.');
    if(rs!=null&&rs>50&&rs<=75) parts.push('Kịch bản cần theo dõi: mực nước có xu hướng tăng.');
    if(dq==='BAD') parts.push('⛔ Kịch bản không chắc chắn: cần kiểm tra cảm biến.');
    if(!parts.length) parts.push('Tình hình ổn định, tiếp tục giám sát.');
    se.innerHTML=parts.join('<br>');
  }
  function updateDSS(ai){
    // Read from v2 or legacy
    const rs=ai.ai?ai.ai.risk_score:(ai.risk_score||0);
    const pl=ai.forecast?ai.forecast.predicted_level_5min:(ai.predicted_level_5min||null);
    const action=ai.recommended_action||'';
    const explanation=ai.explanation||[];
    const dq=ai.data_quality||'—';
    const dp=ai.data_points||0;
    const mv=ai.model_version||'—';
    const ds=ai.decision_source||'—';

    updateRiskScore(rs);
    if(el('valPredictedLevel'))el('valPredictedLevel').textContent=pl!=null?pl:'—';
    if(el('forecastHint')&&pl!=null) el('forecastHint').textContent=pl>=60?'⚠️ Vượt ngưỡng nguy hiểm!':pl>=40?'Cần theo dõi':'Trong ngưỡng an toàn';
    if(el('valAction'))el('valAction').textContent=action||'Đang chờ…';
    if(el('valDataQuality'))el('valDataQuality').textContent=dq;
    if(el('valAiMemory'))el('valAiMemory').textContent=`${dp}/60`;
    if(el('valModelVer'))el('valModelVer').textContent=mv;
    if(el('valDecisionSrc'))el('valDecisionSrc').textContent=ds;
    updateExplanation(explanation);
    updateScenario(ai);
  }

  function handleOverallPrediction(payload){
    S.overall = payload || null;
    const badge=el('overallBadge');
    const statusText=el('overallStatusText');
    const riskScore=el('overallRiskScore');
    const riskLabel=el('overallRiskLabel');
    const highestStation=el('overallHighestStation');
    const highestRisk=el('overallHighestRisk');
    const highestStatus=el('overallHighestStatus');
    const maxLevel=el('overallMaxLevel');
    const trendingUp=el('overallTrendingUp');
    const readyCount=el('overallReadyCount');
    const action=el('overallAction');
    const explanationList=el('overallExplanationList');

    const notReady = !payload || payload.overall_status === 'CHUA_DU_DU_LIEU' || payload.overall_status === 'DU_LIEU_KHONG_DAY_DU';
    if(notReady){
      statusText.textContent='Đang chờ đủ dữ liệu 3 điểm đo...';
      riskScore.textContent='--';
      riskLabel.textContent='CHỜ DỮ LIỆU';
      highestStation.textContent='—';
      highestRisk.textContent='—';
      highestStatus.textContent='—';
      maxLevel.textContent='—';
      trendingUp.textContent='—';
      readyCount.textContent=`${payload && payload.stations_ready != null ? payload.stations_ready : 0}/${payload && payload.stations_total != null ? payload.stations_total : 3}`;
      action.textContent='Chưa đủ dữ liệu từ 3 điểm đo, tiếp tục thu thập và không kết luận an toàn.';
      if(explanationList) explanationList.innerHTML='<li class="explanation-item">Đang chờ đủ dữ liệu 3 điểm đo.</li>';
      if(badge){badge.style.background='linear-gradient(135deg, rgba(139,148,158,0.18), rgba(255,255,255,0.03))';badge.style.borderColor='rgba(139,148,158,0.35)';}
      return;
    }

    const rs=Number(payload.overall_risk_score || 0);
    const color=overallRiskColor(rs);
    statusText.textContent=payload.overall_status.replace(/_/g,' ');
    riskScore.textContent=rs.toFixed(1);
    riskLabel.textContent=rs>=75?'NGẬP':rs>=50?'CẢNH BÁO':rs>=30?'THEO DÕI':'AN TOÀN';
    const station=payload.highest_risk_station || {};
    highestStation.textContent=station.name || station.station_id || '—';
    highestRisk.textContent=station.risk_score != null ? `${Number(station.risk_score).toFixed(1)}` : '—';
    highestStatus.textContent=station.status || '—';
    maxLevel.textContent=payload.forecast && payload.forecast.max_predicted_level_5min != null ? `${Number(payload.forecast.max_predicted_level_5min).toFixed(1)} cm` : '—';
    trendingUp.textContent=payload.forecast ? `${payload.forecast.stations_trending_up}/${payload.stations_total || 3}` : '—';
    readyCount.textContent=`${payload.stations_ready || 0}/${payload.stations_total || 3}`;
    action.textContent=payload.recommended_action || 'Theo dõi sát toàn khu vực.';
    if(explanationList){
      const items=(payload.explanation && payload.explanation.length)?payload.explanation:['Đang chờ phân tích tổng thể.'];
      explanationList.innerHTML=items.map(item=>`<li class="explanation-item">💡 ${item}</li>`).join('');
    }
    if(badge){badge.style.background=`linear-gradient(135deg, ${color}22, rgba(255,255,255,0.03))`;badge.style.borderColor=`${color}66`;}
  }

  // ── Sensor card updates ──
  function updateSensor(data){
    const lv=Number(data.level??0),fl=Number(data.flow??0),rn=Number(data.rain??data.rain_local??0);
    const eff=Math.min(100,Math.max(0,(fl/(lv+1))*100));
    animN('valLevel',S.dL,lv,1,v=>{S.dL=v;setArc('gaugeLevelArc',(v/CFG.CRIT)*100);});
    animN('valFlow',S.dF,fl,3,v=>{S.dF=v;setArc('gaugeFlowArc',(v/CFG.FLOW_MAX)*100);});
    animN('valEfficiency',S.dE,eff,1,v=>{S.dE=v;const f=el('efficiencyFill');if(f){f.style.width=Math.min(100,v)+'%';f.style.background=v>70?'linear-gradient(90deg,#3fb950,#2ea043)':v>=30?'linear-gradient(90deg,#f0b429,#d99a1c)':'linear-gradient(90deg,#ff6b6b,#d93838)';}});
  }

  // ── Alert banner ──
  function setBanner(ai){
    const b=el('alertBanner'),st=el('alertStatus'),cf=el('valAiConf');
    b.classList.remove('alert-banner--safe','alert-banner--watch','alert-banner--warn','alert-banner--flood');
    
    const sid = S.sel;
    const lt = S.lastSensorTime[sid];
    const isOffline = lt && (Date.now() - lt > CFG.OFFLINE_MS);

    if(!ai||!ai.status||isOffline){
      st.textContent=isOffline ? 'Dữ liệu cũ / mất kết nối' : 'Chưa có dự đoán AI';
      if(cf)cf.textContent='—';
      b.classList.add('alert-banner--safe');
      setArc('gaugeAiArc',0);
      const a=el('gaugeAiArc');if(a){a.style.stroke='#8b949e';}
      return;
    }
    // Detect v2 status format
    let status=ai.status;
    if(ai.ai&&ai.ai.status) status=ai.ai.status;
    // Normalize
    const sn=status.replace(/_/g,' ');
    st.textContent=sn;
    const conf=ai.ai?ai.ai.confidence:(ai.confidence||0);
    if(cf)cf.textContent=conf;
    setArc('gaugeAiArc',conf);
    const a=el('gaugeAiArc');if(a){a.style.stroke=conf>70?'#ff6b6b':conf>40?'#f0b429':'#3fb950';}
    el('alertConfidence').textContent=`Độ tin cậy AI: ${conf}%`;
    if(sn.includes('NGAP')||sn.includes('FLOOD')) b.classList.add('alert-banner--flood');
    else if(sn.includes('CANH')||sn.includes('WARN')) b.classList.add('alert-banner--warn');
    else if(sn.includes('THEO')||sn.includes('WATCH')) b.classList.add('alert-banner--watch');
    else b.classList.add('alert-banner--safe');
  }

  // ── Overview ──
  function updateOverview(){
    let nS=0,nW=0,nC=0,nF=0,nU=0,worst='',worstRS=0;
    STATIONS.forEach(st=>{
      const ai=ensure(st.id).lastAI;
      if(!ai||!ai.status){nU++;return;}
      const s=(ai.ai?ai.ai.status:ai.status)||'';
      const rs=ai.ai?ai.ai.risk_score:(ai.risk_score||0);
      if(s.includes('NGAP')||s.includes('FLOOD'))nF++;
      else if(s.includes('CANH')||s.includes('WARN'))nC++;
      else if(s.includes('THEO')||s.includes('WATCH'))nW++;
      else nS++;
      if(rs>worstRS){worstRS=rs;worst=st.name;}
    });
    const ov=el('systemOverview');
    if(ov) ov.innerHTML=`<b>${STATIONS.length}</b> trạm · <span style="color:#3fb950">✅ ${nS} An toàn</span> · <span style="color:#58a6ff">👁 ${nW} Theo dõi</span> · <span style="color:#f0b429">⚠️ ${nC} Cảnh báo</span> · <span style="color:#ff6b6b">🌊 ${nF} Ngập</span>${nU?' · ❓ '+nU+' Chưa rõ':''}${worst?' · Nguy hiểm nhất: <b>'+worst+'</b> ('+worstRS+')':''}`;
  }

  // ── Ingest sensor data ──
  function ingestSensor(sid,data){
    const lv=Number(data.level_cm??data.level??0),fl=Number(data.flow_lpm??data.flow??0),rn=Number(data.rain_mm??data.rain??data.rain_local??0);
    const s=ensure(sid);s.lastSensor={level:lv,flow:fl,rain:rn};
    S.lastSensorTime[sid]=Date.now();
    pushStore(sid,lv,fl,rn);
    if(sid==='THAI_HA'||!gLabels.length){pushTime();updateCharts();}
    if(sid===S.sel) updateSensor({level:lv,flow:fl,rain:rn});
    updateOverview();
  }

  // ── Ingest AI prediction ──
  function ingestAi(sid,data){
    const s=ensure(sid);
    // Speak alert on transition to flood
    const prevStatus=s.lastAI?((s.lastAI.ai?s.lastAI.ai.status:s.lastAI.status)||''):'';
    const newStatus=(data.ai?data.ai.status:data.status)||'';
    if((newStatus.includes('NGAP')||newStatus.includes('FLOOD'))&&!prevStatus.includes('NGAP')&&!prevStatus.includes('FLOOD')){
      const st=STATIONS.find(x=>x.id===sid);if(st&&!S.mute&&'speechSynthesis' in window){const m=new SpeechSynthesisUtterance(`Cảnh báo, trạm ${st.name} nguy hiểm!`);m.lang='vi-VN';window.speechSynthesis.speak(m);}
    }
    s.lastAI=data;
    if(sid===S.sel){S.lastAI=data;setBanner(data);updateDSS(data);
      if(data.current){updateSensor({level:data.current.level_cm,flow:data.current.flow_lpm,rain:data.current.rain_mm});}
      else if(data.level!=null){updateSensor(data);}
    }
    updateOverview();renderStations();
  }

  function selectStation(sid){if(!STATIONS.some(x=>x.id===sid))return;S.sel=sid;refreshUI();}

  function refreshUI(){
    const s=ensure(S.sel),ls=s.lastSensor||{};
    if(ls.level!=null){S.dL=ls.level;S.dF=ls.flow;el('valLevel').textContent=ls.level.toFixed(1);el('valFlow').textContent=ls.flow.toFixed(3);setArc('gaugeLevelArc',(ls.level/CFG.CRIT)*100);setArc('gaugeFlowArc',(ls.flow/CFG.FLOW_MAX)*100);}
    else{S.dL=null;S.dF=null;el('valLevel').textContent='—';el('valFlow').textContent='—';}
    S.lastAI=s.lastAI;setBanner(s.lastAI);if(s.lastAI)updateDSS(s.lastAI);
    renderStations();updateCharts();
  }

  function renderStations(){
    const ul=el('stationList');ul.innerHTML='';
    STATIONS.forEach(st=>{
      const s=ensure(st.id),ai=s.lastAI;
      let dc='station-btn__dot';
      const status=ai?((ai.ai?ai.ai.status:ai.status)||''):'';
      if(status.includes('NGAP')||status.includes('FLOOD'))dc+=' station-btn__dot--flood';
      else if(status.includes('CANH')||status.includes('WARN'))dc+=' station-btn__dot--warn';
      else if(status.includes('AN')||status.includes('SAFE'))dc+=' station-btn__dot--safe';
      else if(status.includes('THEO')||status.includes('WATCH'))dc+=' station-btn__dot--watch';
      const li=document.createElement('li'),btn=document.createElement('button');
      btn.type='button';btn.className='station-btn'+(st.id===S.sel?' station-btn--active':'');
      btn.innerHTML=`<span class="${dc}"></span><span class="station-btn__id">${st.id}</span><br>${st.name}`;
      btn.addEventListener('click',()=>selectStation(st.id));
      li.appendChild(btn);ul.appendChild(li);
    });
  }

  // ── Map ──
  function initMap(){
    const b=L.latLngBounds(STATIONS.map(s=>[s.lat,s.lon]));
    map=L.map('map').fitBounds(b,{padding:[36,36],maxZoom:15});
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{attribution:'&copy; OSM &copy; CARTO',subdomains:'abcd',maxZoom:20}).addTo(map);
    const rh='<div class="map-ripple-marker" style="--ripple-color:#8b949e"><span class="map-ripple"></span><span class="map-ripple map-ripple--d2"></span><span class="map-ripple-core"></span></div>';
    STATIONS.forEach(st=>{
      const icon=L.divIcon({html:rh,className:'map-ripple-wrap',iconSize:[56,56],iconAnchor:[28,28]});
      const mk=L.marker([st.lat,st.lon],{icon,zIndexOffset:800}).addTo(map);
      mk.on('click',()=>{selectStation(st.id);mk.openPopup();});
      markers[st.id]=mk;
    });
  }

  // ── Weather ──
  async function fetchWeather(){
    try{
      const u=new URL(CFG.OPEN_METEO);u.searchParams.set('latitude',String(STATIONS[0].lat));u.searchParams.set('longitude',String(STATIONS[0].lon));u.searchParams.set('hourly','precipitation,temperature_2m,relative_humidity_2m');u.searchParams.set('current_weather','true');u.searchParams.set('forecast_hours','2');u.searchParams.set('timezone','auto');
      const r=await fetch(u.toString());if(!r.ok)throw new Error(`HTTP ${r.status}`);const j=await r.json();
      const p=j.hourly&&j.hourly.precipitation;if(p&&p.length){const v=Number(p.length>1?p[1]:p[0]);animN('valRainForecast',S.dR,v,2,v2=>{S.dR=v2;});}
      if(j.current_weather&&el('valTemp'))el('valTemp').textContent=j.current_weather.temperature+'°C';
      if(j.hourly&&j.hourly.relative_humidity_2m&&j.hourly.relative_humidity_2m.length>0&&el('valHum'))el('valHum').textContent='💧 '+j.hourly.relative_humidity_2m[0]+'%';
    }catch(e){console.error('Weather:',e.message);}
  }

  // ── CSV Export ──
  function exportCSV(){
    const sid=S.sel,s=ensure(sid);
    let csv='Thời gian,Mực nước (cm),Lưu lượng,Lượng mưa (mm)\n';
    for(let i=0;i<gLabels.length;i++){csv+=`${gLabels[i]},${s.level[i]??''},${s.flow[i]??''},${s.rain[i]??''}\n`;}
    const blob=new Blob(['\ufeff'+csv],{type:'text/csv;charset=utf-8;'});
    const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`FloodMind_${sid}.csv`;document.body.appendChild(a);a.click();document.body.removeChild(a);
  }

  // ── MQTT ──
  function connectMqtt(){
    const lib=typeof mqtt!=='undefined'?mqtt:window.mqtt;
    mqttC=lib.connect(CFG.MQTT_WS_URL,{clientId:'fmdss-'+Math.random().toString(36).slice(2,10),reconnectPeriod:4000,connectTimeout:20000,protocolVersion:4});
    mqttC.on('connect',()=>{setMqtt(true);mqttC.subscribe([CFG.TOPIC_FLOOD,CFG.TOPIC_AI,CFG.TOPIC_OVERALL],{qos:0});});
    mqttC.on('error',()=>setMqtt(false));
    mqttC.on('close',()=>setMqtt(false));
    mqttC.on('offline',()=>setMqtt(false));
    mqttC.on('message',(topic,msg)=>{
      const txt=msg.toString();
      try {
        if(topic === CFG.TOPIC_OVERALL || topic === 'ai/prediction/overall') {
          handleOverallPrediction(JSON.parse(txt));
          return;
        }
        const mAi=/^ai\/prediction\/([^/]+)$/.exec(topic);
        if(mAi){ingestAi(mAi[1],JSON.parse(txt));return;}
        const mD=/^flood\/monitor\/([^/]+)\/data$/.exec(topic);
        if(mD){ingestSensor(mD[1],JSON.parse(txt));return;}
      } catch(e) {
        console.error('Lỗi xử lý MQTT:', topic, e);
      }
    });
  }

  // ── Controls ──
  function wireControls(){
    el('btnBuzzerOn')?.addEventListener('click',()=>pubCmd({action:'toggle_buzzer',value:'ON'}));
    el('btnBuzzerOff')?.addEventListener('click',()=>pubCmd({action:'toggle_buzzer',value:'OFF'}));
    el('btnResetAi')?.addEventListener('click',()=>pubCmd({action:'reset_ai',station_id:S.sel}));
    const bm=el('btnToggleMute');if(bm)bm.addEventListener('click',()=>{S.mute=!S.mute;bm.textContent=S.mute?'🔇 BẬT ÂM THANH':'🔊 TẮT ÂM THANH';});
    el('btnExportCsv')?.addEventListener('click',exportCSV);
  }

  // ── Init ──
  function init(){
    initArcs();tick();setInterval(tick,1000);setMqtt(false);wireControls();initCharts();initMap();renderStations();refreshUI();fetchWeather();setInterval(fetchWeather,5*60*1000);
    try{connectMqtt();}catch(e){console.error(e);}
    handleOverallPrediction(null);
    updateOverview();
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',init);else init();
})();
