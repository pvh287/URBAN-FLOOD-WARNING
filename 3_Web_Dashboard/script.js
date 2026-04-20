/**
 * Flood Command Center — Multi-station MQTT (EMQX WSS)
 */
(function () {
  'use strict';

  const STATIONS = [
    { id: 'THAI_HA', name: 'Thái Hà', lat: 21.01205707861206, lon: 105.82107114719774 },
    { id: 'PHAM_NGOC_THACH', name: 'Phạm Ngọc Thạch', lat: 21.009224497039224, lon: 105.8348400357426 },
    { id: 'TRUONG_CHINH', name: 'Trường Chinh', lat: 21.001455046307868, lon: 105.8261326019895 },
  ];

  const CONFIG = {
    MQTT_WS_URL: 'wss://broker.emqx.io:8084/mqtt',
    TOPIC_FLOOD_WILDCARD: 'flood/monitor/+/data',
    TOPIC_AI_WILDCARD: 'ai/prediction/+',
    TOPIC_SENSOR_COMMAND: 'sensor/command',
    MAP_ZOOM: 14,
    CHART_WINDOW_MS: 10 * 60 * 1000,
    CHART_MAX_POINTS: 120,
    OPEN_METEO_URL: 'https://api.open-meteo.com/v1/forecast',
    SAFE_LEVEL: 30,
    WARNING_LEVEL: 50,
    CRITICAL_LEVEL: 80,
    FLOW_GAUGE_MAX: 25,
  };

  const GAUGE_R = 48;
  const GAUGE_C = 2 * Math.PI * GAUGE_R;

  const chartTimestamps = [];
  const chartLabels = [];
  const levelSeries = [];
  const flowSeries = [];
  const rainSeries = [];

  const stationStore = {};

  let predictionSeq = 0;
  let mqttClient = null;
  let map = null;
  const markers = {};
  let chartLevel = null;
  let chartFlow = null;
  let chartRain = null;

  const state = {
    selectedStationId: STATIONS[0].id,
    lastSensor: { level: null, flow: null, rain: null },
    lastAI: null,
    mqttConnected: false,
    displayLevel: null,
    displayFlow: null,
    displayEff: null,
    displayRainF: null,
  };

  const el = (id) => document.getElementById(id);

  function pad(n) {
    return String(n).padStart(2, '0');
  }

  function parseNum(v, fallback) {
    const n = parseFloat(v);
    return Number.isFinite(n) ? n : fallback;
  }

  function parseFloodTopic(topic) {
    const m = /^flood\/monitor\/([^/]+)\/data$/.exec(topic);
    return m ? m[1] : null;
  }

  function parseAiTopic(topic) {
    const m = /^ai\/prediction\/([^/]+)$/.exec(topic);
    return m ? m[1] : null;
  }

  function ensureStation(sid) {
    if (!stationStore[sid]) {
      stationStore[sid] = {
        timestamps: [],
        labels: [],
        level: [],
        flow: [],
        rain: [],
        lastSensor: { level: null, flow: null, rain: null },
        lastAI: null,
        displayLevel: null,
        displayFlow: null,
        displayEff: null,
      };
    }
    return stationStore[sid];
  }

  function tweenNumber(from, to, durationMs, onFrame) {
    const t0 = performance.now();
    function frame(t) {
      const u = Math.min(1, (t - t0) / durationMs);
      const eased = 1 - Math.pow(1 - u, 3);
      onFrame(from + (to - from) * eased);
      if (u < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
  }

  function tickClock() {
    const d = new Date();
    el('clock').textContent =
      `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  }

  function setMqttLed(ok) {
    const wrap = el('mqttLed');
    const label = el('mqttLabel');
    state.mqttConnected = ok;
    wrap.classList.toggle('mqtt-led--ok', ok);
    wrap.classList.toggle('mqtt-led--bad', !ok);
    label.textContent = ok ? 'MQTT OK' : 'MQTT mất kết nối';
  }

  function appendLog(text, kind) {
    const panel = el('logPanel');
    const line = document.createElement('div');
    line.className =
      'log-line' + (kind === 'err' ? ' log-line--err' : kind === 'ok' ? ' log-line--ok' : '');
    const ts = new Date().toISOString();
    line.textContent = `> [${ts}] ${text}`;
    panel.appendChild(line);
    panel.scrollTop = panel.scrollHeight;
  }

  function mqttLib() {
    if (typeof mqtt !== 'undefined') return mqtt;
    if (typeof window !== 'undefined' && window.mqtt) return window.mqtt;
    throw new Error('mqtt.js không tải được từ CDN');
  }

  function publishCommand(obj) {
    if (!mqttClient || !state.mqttConnected) {
      appendLog('Lỗi: chưa kết nối MQTT.', 'err');
      return;
    }
    const payload = JSON.stringify(obj);
    mqttClient.publish(CONFIG.TOPIC_SENSOR_COMMAND, payload, { qos: 1 }, (err) => {
      if (err) appendLog(`Publish lỗi: ${err.message}`, 'err');
      else appendLog(`→ ${CONFIG.TOPIC_SENSOR_COMMAND}: ${payload}`, 'ok');
    });
  }

  function trimBufferArrays(tsArr, lbArr, a1, a2, a3) {
    const now = Date.now();
    while (
      tsArr.length &&
      (now - tsArr[0] > CONFIG.CHART_WINDOW_MS || tsArr.length > CONFIG.CHART_MAX_POINTS)
    ) {
      tsArr.shift();
      lbArr.shift();
      a1.shift();
      a2.shift();
      a3.shift();
    }
  }

  function mirrorGlobalsToActiveStore() {
    const s = ensureStation(state.selectedStationId);
    s.timestamps = [...chartTimestamps];
    s.labels = [...chartLabels];
    s.level = [...levelSeries];
    s.flow = [...flowSeries];
    s.rain = [...rainSeries];
    s.lastSensor = { ...state.lastSensor };
    s.lastAI = state.lastAI;
    s.displayLevel = state.displayLevel;
    s.displayFlow = state.displayFlow;
    s.displayEff = state.displayEff;
  }

  function loadStoreIntoGlobals(sid) {
    const s = ensureStation(sid);
    chartTimestamps.splice(0, chartTimestamps.length, ...s.timestamps);
    chartLabels.splice(0, chartLabels.length, ...s.labels);
    levelSeries.splice(0, levelSeries.length, ...s.level);
    flowSeries.splice(0, flowSeries.length, ...s.flow);
    rainSeries.splice(0, rainSeries.length, ...s.rain);
    state.lastSensor = { ...s.lastSensor };
    state.lastAI = s.lastAI;
    state.displayLevel = s.displayLevel;
    state.displayFlow = s.displayFlow;
    state.displayEff = s.displayEff;
  }

  function setLevelGaugeVisual(level) {
    const pct = Math.min(100, Math.max(0, (level / CONFIG.CRITICAL_LEVEL) * 100));
    const arc = el('gaugeLevelArc');
    if (arc) arc.style.strokeDashoffset = String(GAUGE_C * (1 - pct / 100));
    const bar = el('levelBar');
    if (bar) {
      bar.style.width = `${pct}%`;
      bar.classList.remove('mini-bar__fill--mid', 'mini-bar__fill--high');
      if (level >= CONFIG.WARNING_LEVEL) bar.classList.add('mini-bar__fill--high');
      else if (level >= CONFIG.SAFE_LEVEL) bar.classList.add('mini-bar__fill--mid');
    }
  }

  function setFlowGaugeVisual(flow) {
    const pct = Math.min(100, Math.max(0, (flow / CONFIG.FLOW_GAUGE_MAX) * 100));
    const arc = el('gaugeFlowArc');
    if (arc) arc.style.strokeDashoffset = String(GAUGE_C * (1 - pct / 100));
  }

  function initGaugeArcs() {
    ['gaugeLevelArc', 'gaugeFlowArc'].forEach((id) => {
      const a = el(id);
      if (a) {
        a.style.strokeDasharray = String(GAUGE_C);
        a.style.strokeDashoffset = String(GAUGE_C);
      }
    });
  }

  function animateLevel(from, to) {
    tweenNumber(parseNum(from, 0), parseNum(to, 0), 420, (v) => {
      state.displayLevel = v;
      el('valLevel').textContent = v.toFixed(1);
      setLevelGaugeVisual(v);
    });
  }

  function animateFlow(from, to) {
    tweenNumber(parseNum(from, 0), parseNum(to, 0), 420, (v) => {
      state.displayFlow = v;
      el('valFlow').textContent = v.toFixed(3);
      setFlowGaugeVisual(v);
    });
  }

  function animateEfficiency(from, to) {
    tweenNumber(parseNum(from, 0), parseNum(to, 0), 400, (v) => {
      state.displayEff = v;
      el('valEfficiency').textContent = v.toFixed(1);
      const fill = el('efficiencyFill');
      if (fill) fill.style.width = `${Math.min(100, Math.max(0, v))}%`;
    });
  }

  function animateRainForecast(from, to) {
    tweenNumber(parseNum(from, 0), parseNum(to, 0), 500, (v) => {
      state.displayRainF = v;
      el('valRainForecast').textContent = v.toFixed(2);
    });
  }

  function updateChartsDatasets() {
    if (chartLevel) {
      chartLevel.data.labels = chartLabels.slice();
      chartLevel.data.datasets[0].data = levelSeries.slice();
      chartLevel.update('none');
    }
    if (chartFlow) {
      chartFlow.data.labels = chartLabels.slice();
      chartFlow.data.datasets[0].data = flowSeries.slice();
      chartFlow.update('none');
    }
    if (chartRain) {
      chartRain.data.labels = chartLabels.slice();
      chartRain.data.datasets[0].data = rainSeries.slice();
      chartRain.update('none');
    }
  }

  function pushChartPoint(level, flow, rain) {
    const now = Date.now();
    const t = new Date();
    const label = `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
    chartTimestamps.push(now);
    chartLabels.push(label);
    levelSeries.push(level);
    flowSeries.push(flow);
    rainSeries.push(rain);
    trimBufferArrays(chartTimestamps, chartLabels, levelSeries, flowSeries, rainSeries);
    mirrorGlobalsToActiveStore();
    updateChartsDatasets();
  }

  function pushStoreOnly(sid, level, flow, rain) {
    const s = ensureStation(sid);
    const now = Date.now();
    const t = new Date();
    const label = `${pad(t.getHours())}:${pad(t.getMinutes())}:${pad(t.getSeconds())}`;
    s.timestamps.push(now);
    s.labels.push(label);
    s.level.push(level);
    s.flow.push(flow);
    s.rain.push(rain);
    trimBufferArrays(s.timestamps, s.labels, s.level, s.flow, s.rain);
  }

  function drainageEfficiencyPct(level, flow) {
    const l = Number(level) || 0;
    const f = Number(flow) || 0;
    return Math.min(100, Math.max(0, (f / (l + 1)) * 100));
  }

  function updateSensorCards(data) {
    const level = Number(data.level ?? 0);
    const flow = Number(data.flow ?? 0);
    const rain = Number(data.rain ?? data.rain_local ?? 0);
    state.lastSensor = { level, flow, rain };

    const fromL = state.displayLevel != null ? state.displayLevel : level;
    const fromF = state.displayFlow != null ? state.displayFlow : flow;
    const eff = drainageEfficiencyPct(level, flow);
    const fromE = state.displayEff != null ? state.displayEff : eff;

    animateLevel(fromL, level);
    animateFlow(fromF, flow);
    animateEfficiency(fromE, eff);
    pushChartPoint(level, flow, rain);
  }

  function refreshUIFromSelection() {
    loadStoreIntoGlobals(state.selectedStationId);
    updateChartsDatasets();
    const s = ensureStation(state.selectedStationId);
    const ls = s.lastSensor;
    if (ls.level != null && ls.flow != null) {
      const eff = drainageEfficiencyPct(ls.level, ls.flow);
      state.displayLevel = ls.level;
      state.displayFlow = ls.flow;
      state.displayEff = eff;
      el('valLevel').textContent = ls.level.toFixed(1);
      el('valFlow').textContent = ls.flow.toFixed(3);
      el('valEfficiency').textContent = eff.toFixed(1);
      el('efficiencyFill').style.width = `${eff}%`;
      setLevelGaugeVisual(ls.level);
      setFlowGaugeVisual(ls.flow);
    } else {
      state.displayLevel = null;
      state.displayFlow = null;
      state.displayEff = null;
      el('valLevel').textContent = '—';
      el('valFlow').textContent = '—';
      el('valEfficiency').textContent = '—';
      setLevelGaugeVisual(0);
      setFlowGaugeVisual(0);
      el('efficiencyFill').style.width = '0%';
    }
    state.lastSensor = { ...ls };
    state.lastAI = s.lastAI;
    setAlertBanner(s.lastAI);
    renderStationList();
  }

  function setAlertBanner(ai) {
    const banner = el('alertBanner');
    const statusEl = el('alertStatus');
    const confEl = el('alertConfidence');
    banner.classList.remove('alert-banner--safe', 'alert-banner--warn', 'alert-banner--flood');
    if (!ai || !ai.status) {
      statusEl.textContent = 'Chưa có dự đoán AI (trạm đang chọn)';
      confEl.textContent = 'Độ tin cậy AI: —%';
      banner.classList.add('alert-banner--safe');
      return;
    }
    statusEl.textContent = ai.status;
    confEl.textContent = `Độ tin cậy AI: ${ai.confidence}%`;
    if (ai.status === 'NGAP LUT') banner.classList.add('alert-banner--flood');
    else if (ai.status === 'CANH BAO') banner.classList.add('alert-banner--warn');
    else banner.classList.add('alert-banner--safe');
  }

  function updateOverview() {
    let nSafe = 0;
    let nWarn = 0;
    let nFlood = 0;
    let nUnknown = 0;
    STATIONS.forEach((st) => {
      const ai = ensureStation(st.id).lastAI;
      if (!ai || !ai.status) nUnknown += 1;
      else if (ai.status === 'NGAP LUT') nFlood += 1;
      else if (ai.status === 'CANH BAO') nWarn += 1;
      else nSafe += 1;
    });
    const parts = [];
    if (nSafe) parts.push(`${nSafe} trạm An toàn`);
    if (nWarn) parts.push(`${nWarn} trạm Cảnh báo`);
    if (nFlood) parts.push(`${nFlood} trạm Đang ngập / nguy cơ cao`);
    if (nUnknown) parts.push(`${nUnknown} trạm chưa có AI`);
    el('systemOverview').textContent =
      parts.length > 0 ? `Tổng quát hệ thống: ${parts.join(' · ')}.` : 'Tổng quát: chưa có dữ liệu AI.';
  }

  function rippleColorFromAi(ai) {
    if (!ai || !ai.status) return '#8b949e';
    if (ai.status === 'NGAP LUT') return '#ff6b6b';
    if (ai.status === 'CANH BAO') return '#f0b429';
    return '#3fb950';
  }

  function setMarkerRippleColor(sid, color) {
    const mk = markers[sid];
    if (!mk) return;
    const root = mk.getElement && mk.getElement();
    if (!root) return;
    const inner = root.querySelector('.map-ripple-marker');
    if (inner) inner.style.setProperty('--ripple-color', color);
  }

  function updateMarkerPopup(sid) {
    const mk = markers[sid];
    if (!mk) return;
    const st = STATIONS.find((x) => x.id === sid);
    const s = ensureStation(sid);
    const a = s.lastAI;
    const ls = s.lastSensor;
    const html = `
      <div style="min-width:200px;font-family:Inter,system-ui,sans-serif;font-size:13px;color:#0d1117;">
        <strong>${st ? st.name : sid}</strong><br/>
        <span style="color:#57606a;">Mực nước:</span> ${ls.level != null ? ls.level.toFixed(1) + ' cm' : '—'}<br/>
        <span style="color:#57606a;">Lưu lượng:</span> ${ls.flow != null ? ls.flow.toFixed(3) + ' m³/s' : '—'}<br/>
        <span style="color:#57606a;">Mưa:</span> ${ls.rain != null ? ls.rain.toFixed(2) : '—'}<br/>
        <hr style="border:none;border-top:1px solid #d0d7de;margin:6px 0;"/>
        <span style="color:#57606a;">AI:</span> ${a ? a.status : '—'}<br/>
        <span style="color:#57606a;">Tin cậy:</span> ${a ? a.confidence + '%' : '—'}
      </div>`;
    mk.bindPopup(html);
  }

  function ingestSensor(sid, data) {
    const level = Number(data.level ?? 0);
    const flow = Number(data.flow ?? 0);
    const rain = Number(data.rain ?? data.rain_local ?? 0);
    const s = ensureStation(sid);
    s.lastSensor = { level, flow, rain };

    if (sid === state.selectedStationId) {
      updateSensorCards({ level, flow, rain });
    } else {
      pushStoreOnly(sid, level, flow, rain);
    }
    updateMarkerPopup(sid);
    updateOverview();
  }

  function ingestAi(sid, data) {
    const s = ensureStation(sid);
    s.lastAI = data;
    setMarkerRippleColor(sid, rippleColorFromAi(data));
    updateMarkerPopup(sid);

    if (sid === state.selectedStationId) {
      state.lastAI = data;
      predictionSeq += 1;
      appendLog(
        `[${sid}] Prediction #${predictionSeq} ${data.status} ${data.confidence}% @ ${data.timestamp}`,
        'ok'
      );
      setAlertBanner(data);
      if (data.level != null && data.flow != null) {
        updateSensorCards({
          level: data.level,
          flow: data.flow,
          rain: data.rain != null ? data.rain : state.lastSensor.rain ?? 0,
        });
      }
    }
    updateOverview();
    renderStationList();
  }

  function selectStation(sid) {
    if (!STATIONS.some((x) => x.id === sid)) return;
    mirrorGlobalsToActiveStore();
    state.selectedStationId = sid;
    refreshUIFromSelection();
    fetchOpenMeteoRain();
  }

  function renderStationList() {
    const ul = el('stationList');
    ul.innerHTML = '';
    STATIONS.forEach((st) => {
      const s = ensureStation(st.id);
      const ai = s.lastAI;
      let dotClass = 'station-btn__dot';
      if (ai && ai.status === 'NGAP LUT') dotClass += ' station-btn__dot--flood';
      else if (ai && ai.status === 'CANH BAO') dotClass += ' station-btn__dot--warn';
      else if (ai && ai.status === 'AN TOAN') dotClass += ' station-btn__dot--safe';

      const li = document.createElement('li');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className =
        'station-btn' + (st.id === state.selectedStationId ? ' station-btn--active' : '');
      btn.innerHTML = `<span class="${dotClass}"></span><span class="station-btn__id">${st.id}</span><br/>${st.name}`;
      btn.addEventListener('click', () => selectStation(st.id));
      li.appendChild(btn);
      ul.appendChild(li);
    });
  }

  function initMap() {
    const bounds = L.latLngBounds(STATIONS.map((s) => [s.lat, s.lon]));
    map = L.map('map').fitBounds(bounds, { padding: [36, 36], maxZoom: 15 });
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
      attribution: '&copy; OSM &copy; CARTO',
      subdomains: 'abcd',
      maxZoom: 20,
    }).addTo(map);

    const rippleHtml =
      '<div class="map-ripple-marker" style="--ripple-color:#8b949e">' +
      '<span class="map-ripple"></span>' +
      '<span class="map-ripple map-ripple--d2"></span>' +
      '<span class="map-ripple-core"></span></div>';

    STATIONS.forEach((st) => {
      const icon = L.divIcon({
        html: rippleHtml,
        className: 'map-ripple-wrap',
        iconSize: [56, 56],
        iconAnchor: [28, 28],
      });
      const mk = L.marker([st.lat, st.lon], { icon, zIndexOffset: 800 }).addTo(map);
      mk.on('click', () => {
        selectStation(st.id);
        updateMarkerPopup(st.id);
        mk.openPopup();
      });
      markers[st.id] = mk;
      updateMarkerPopup(st.id);
    });
  }

  function areaGradient(stops) {
    return function (context) {
      const chart = context.chart;
      const { ctx, chartArea } = chart;
      if (!chartArea) return stops.bottom;
      const g = ctx.createLinearGradient(0, chartArea.bottom, 0, chartArea.top);
      g.addColorStop(0, stops.bottom);
      g.addColorStop(1, stops.top);
      return g;
    };
  }

  function baseChartOptions() {
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 220 },
      interaction: { mode: 'index', intersect: false },
      scales: {
        x: {
          ticks: { maxTicksLimit: 8, color: '#8b949e', font: { size: 10, family: 'Inter' } },
          grid: { display: false, drawBorder: false },
          border: { display: false },
        },
        y: {
          beginAtZero: true,
          ticks: { color: '#8b949e', font: { size: 10, family: 'Inter' } },
          grid: { color: 'rgba(255,255,255,0.035)', drawBorder: false },
          border: { display: false },
        },
      },
      plugins: { legend: { display: false } },
      elements: {
        point: { radius: 0, hitRadius: 5 },
        line: { borderWidth: 2.5, tension: 0.32 },
      },
    };
  }

  function initCharts() {
    const commonLabels = chartLabels;
    chartLevel = new Chart(el('chartLevel'), {
      type: 'line',
      data: {
        labels: commonLabels,
        datasets: [
          {
            label: 'Level',
            data: levelSeries,
            borderColor: 'rgb(88, 166, 255)',
            backgroundColor: areaGradient({
              bottom: 'rgba(88,166,255,0)',
              top: 'rgba(88,166,255,0.42)',
            }),
            fill: true,
          },
        ],
      },
      options: baseChartOptions(),
    });
    chartFlow = new Chart(el('chartFlow'), {
      type: 'line',
      data: {
        labels: commonLabels,
        datasets: [
          {
            label: 'Flow',
            data: flowSeries,
            borderColor: 'rgb(56, 189, 248)',
            backgroundColor: areaGradient({
              bottom: 'rgba(56,189,248,0)',
              top: 'rgba(56,189,248,0.4)',
            }),
            fill: true,
          },
        ],
      },
      options: baseChartOptions(),
    });
    chartRain = new Chart(el('chartRain'), {
      type: 'line',
      data: {
        labels: commonLabels,
        datasets: [
          {
            label: 'Rain',
            data: rainSeries,
            borderColor: 'rgb(163, 113, 247)',
            backgroundColor: areaGradient({
              bottom: 'rgba(163,113,247,0)',
              top: 'rgba(163,113,247,0.4)',
            }),
            fill: true,
          },
        ],
      },
      options: baseChartOptions(),
    });
  }

  async function fetchOpenMeteoRain() {
    const st = STATIONS.find((x) => x.id === state.selectedStationId) || STATIONS[0];
    try {
      const u = new URL(CONFIG.OPEN_METEO_URL);
      u.searchParams.set('latitude', String(st.lat));
      u.searchParams.set('longitude', String(st.lon));
      u.searchParams.set('hourly', 'precipitation');
      u.searchParams.set('forecast_hours', '2');
      u.searchParams.set('timezone', 'auto');
      const res = await fetch(u.toString());
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const j = await res.json();
      const p = j.hourly && j.hourly.precipitation;
      if (!p || !p.length) throw new Error('Thiếu precipitation');
      const next = p.length > 1 ? p[1] : p[0];
      const val = Number(next);
      const from = state.displayRainF != null ? state.displayRainF : val;
      animateRainForecast(from, val);
    } catch (e) {
      el('valRainForecast').textContent = '—';
      appendLog(`Open-Meteo: ${e.message}`, 'err');
    }
  }

  function connectMqtt() {
    const lib = mqttLib();
    const clientId = 'multi-cmd-' + Math.random().toString(36).slice(2, 10);
    mqttClient = lib.connect(CONFIG.MQTT_WS_URL, {
      clientId,
      reconnectPeriod: 4000,
      connectTimeout: 20000,
      protocolVersion: 4,
    });

    mqttClient.on('connect', () => {
      setMqttLed(true);
      appendLog(`MQTT kết nối (${CONFIG.MQTT_WS_URL})`, 'ok');
      mqttClient.subscribe(
        [CONFIG.TOPIC_FLOOD_WILDCARD, CONFIG.TOPIC_AI_WILDCARD],
        { qos: 0 },
        (err) => {
          if (err) appendLog(`Subscribe lỗi: ${err.message}`, 'err');
          else appendLog(`Subscribed: ${CONFIG.TOPIC_FLOOD_WILDCARD}, ${CONFIG.TOPIC_AI_WILDCARD}`, 'ok');
        }
      );
    });

    mqttClient.on('error', (err) => {
      setMqttLed(false);
      appendLog(`MQTT error: ${err && err.message}`, 'err');
    });
    mqttClient.on('close', () => setMqttLed(false));
    mqttClient.on('offline', () => setMqttLed(false));

    mqttClient.on('message', (topic, message) => {
      const text = message.toString();
      const sidAi = parseAiTopic(topic);
      if (sidAi) {
        try {
          ingestAi(sidAi, JSON.parse(text));
        } catch (e) {
          appendLog(`JSON AI (${sidAi}): ${e.message}`, 'err');
        }
        return;
      }
      const sidData = parseFloodTopic(topic);
      if (sidData) {
        try {
          ingestSensor(sidData, JSON.parse(text));
        } catch (e) {
          appendLog(`JSON sensor (${sidData}): ${e.message}`, 'err');
        }
      }
    });
  }

  function wireControls() {
    el('btnBuzzerOn').addEventListener('click', () =>
      publishCommand({ action: 'toggle_buzzer', value: 'ON' })
    );
    el('btnBuzzerOff').addEventListener('click', () =>
      publishCommand({ action: 'toggle_buzzer', value: 'OFF' })
    );
    el('btnResetAi').addEventListener('click', () =>
      publishCommand({ action: 'reset_ai', station_id: state.selectedStationId })
    );
  }

  function init() {
    STATIONS.forEach((s) => ensureStation(s.id));
    initGaugeArcs();
    tickClock();
    setInterval(tickClock, 1000);
    setMqttLed(false);
    wireControls();
    initCharts();
    initMap();
    renderStationList();
    refreshUIFromSelection();
    fetchOpenMeteoRain();
    setInterval(fetchOpenMeteoRain, 5 * 60 * 1000);
    try {
      connectMqtt();
    } catch (e) {
      appendLog(String(e.message || e), 'err');
    }
    updateOverview();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
