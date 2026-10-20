"use strict";
const $ = (id) => document.getElementById(id);
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const pct = (v) => (v == null ? "—" : `${(v * 100).toFixed(2)}%`);
const signed = (v, d = 2) => `${v >= 0 ? "+" : ""}${v.toFixed(d)}`;
const num = (v) => Number(v).toLocaleString("ko-KR");
const timeLabel = (t) =>
  t ? `${t.slice(5, 10).replace("-", "/")} ${t.slice(11, 16)}` : "—";
const cleanName = (s) =>
  String(s?.name || "")
    .replace(/^\s*\d+\.\s*/, "")
    .trim();
const state = {
  overview: null,
  stations: [],
  stationMap: new Map(),
  cases: null,
  boundary: null,
  mode: "replay",
  tab: "explore",
  snapshot: null,
  live: null,
  city: null,
  at: "2025-03-20T07:00:00",
  origin: { latitude: 37.5665, longitude: 126.978 },
  originName: "서울시청 인근",
  radius: 500,
  filter: "all",
  sort: "distance",
  model: "M1",
  activeStation: null,
  detail: null,
  favorites: new Set(),
  mapView: "near",
  mapScale: 1,
  pickOrigin: false,
  request: 0,
  detailRequest: 0,
  playing: false,
  tour: -1,
  caseKind: "helped",
  liveStale: false,
  receiptOld: false,
  liveBusy: false,
  listLimit: 8,
  archiveOrigin: null,
};
let playbackTimer = null,
  liveTimer = null,
  toastTimer = null;
const detailCache = new Map(),
  cityCache = new Map();
const haversine = (a, b, c, d) => {
  const rad = (x) => (x * Math.PI) / 180;
  const v =
    Math.sin(rad(c - a) / 2) ** 2 +
    Math.cos(rad(a)) * Math.cos(rad(c)) * Math.sin(rad(d - b) / 2) ** 2;
  return 6371000 * 2 * Math.asin(Math.sqrt(Math.min(1, v)));
};
function toast(message) {
  $("toast").textContent = message;
  $("toast").classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $("toast").classList.add("hidden"), 4200);
}
function alertMessage(text, error = false) {
  $("alert").textContent = text;
  $("alert").classList.toggle("hidden", !text);
  $("alert").classList.toggle("error", error);
}
function openDialog(id) {
  stopPlayback();
  $(id).showModal();
}
function closeDialog(id) {
  $(id).close();
}
async function api(path) {
  if (window.DEMO_BUNDLE) return offlineApi(path);
  const response = await fetch(path, {
    headers: { Accept: "application/json" },
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw Error("응답을 읽지 못했습니다. 잠시 뒤 다시 조회해 주세요.");
  }
  if (!response.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : "조회 조건을 확인해 주세요.",
    );
  return data;
}
function tab(name) {
  state.tab = name;
  document
    .querySelectorAll(".tab")
    .forEach((el) => el.classList.toggle("active", el.id === name));
  document.querySelectorAll(".nav").forEach((el) => {
    const active = el.dataset.tab === name;
    el.classList.toggle("active", active);
    el.setAttribute("aria-selected", active);
  });
  if (name !== "explore") {
    stopPlayback();
    stopLiveTimer();
  } else if (state.mode === "live") startLiveTimer();
  if (name === "results") renderResearchResults();
}
function currentRows() {
  if (state.mode === "replay") return state.snapshot?.stations || [];
  return (state.live?.rows || []).map((s) => ({
    ...s,
    distance_m: Math.round(
      haversine(
        state.origin.latitude,
        state.origin.longitude,
        s.latitude,
        s.longitude,
      ),
    ),
  }));
}
function score(s) {
  return state.mode === "live" ? s.risk_score : s[`score_${state.model}`];
}
function risk(s) {
  if (state.mode === "replay" && typeof s[`risk_${state.model}`] === "boolean")
    return s[`risk_${state.model}`];
  const v = score(s);
  if (v == null || !Number.isFinite(v)) return null;
  return (
    v >=
    (state.mode === "live"
      ? state.live.threshold
      : state.overview.thresholds[state.model])
  );
}
function status(s) {
  if (s.quantity === 0) return ["현재 0대", "empty"];
  const r = risk(s);
  return r == null
    ? ["예측 자료 없음", "unknown"]
    : r
      ? ["0대 위험 주의", "high"]
      : ["위험 낮게 예상", "low"];
}
function favoriteButton(id) {
  return `<button class="favorite-button ${state.favorites.has(id) ? "saved" : ""}" data-favorite="${id}" aria-label="${state.favorites.has(id) ? "즐겨찾기 해제" : "즐겨찾기 추가"}" aria-pressed="${state.favorites.has(id)}">${state.favorites.has(id) ? "♥" : "♡"}</button>`;
}
function saveFavorites() {
  try {
    localStorage.setItem(
      "seoul-bike-rider-favorites-v2",
      JSON.stringify([...state.favorites]),
    );
  } catch {
    toast("브라우저 저장 공간을 사용할 수 없어 이번 화면에서만 보관합니다.");
  }
  $("favorite-count").textContent = state.favorites.size;
}
function toggleFavorite(id) {
  if (state.favorites.has(id)) state.favorites.delete(id);
  else {
    if (state.favorites.size >= 50) {
      toast("즐겨찾기는 최대 50곳까지 저장할 수 있습니다.");
      return;
    }
    state.favorites.add(id);
  }
  saveFavorites();
  renderList();
  renderDetail();
  renderFavorites();
}
function updateControls() {
  const times = state.overview.times,
    dates = [...new Set(times.map((t) => t.slice(0, 10)))],
    date = state.at.slice(0, 10);
  $("replay-date").innerHTML = dates
    .map((d) => `<option value="${d}">${d.replaceAll("-", ". ")}</option>`)
    .join("");
  $("replay-date").value = date;
  const hours = times.filter((t) => t.startsWith(date));
  $("replay-hour").innerHTML = hours
    .map((t) => `<option value="${t}">${t.slice(11, 16)}</option>`)
    .join("");
  $("replay-hour").value = state.at;
  $("time-slider").max = times.length - 1;
  $("time-slider").value = times.indexOf(state.at);
  $("time-prev").disabled = times.indexOf(state.at) <= 0;
  $("time-next").disabled = times.indexOf(state.at) >= times.length - 1;
  $("origin-name").textContent = state.originName;
  $("origin-name").title =
    `${state.origin.latitude.toFixed(5)}, ${state.origin.longitude.toFixed(5)}`;
  document.querySelectorAll("[data-radius]").forEach((b) => {
    const selected = +b.dataset.radius === state.radius;
    b.classList.toggle("selected", selected);
    b.setAttribute("aria-pressed", selected);
  });
  const replay = state.mode === "replay";
  document.querySelector(".radius-field").disabled = !replay;
  document.querySelector(".radius-field legend span").textContent = replay
    ? "직선거리"
    : "실시간은 샘플 전체";
  $("archive-controls").classList.toggle("hidden", !replay);
  $("live-controls").classList.toggle("hidden", replay);
  $("replay-mode").classList.toggle("selected", replay);
  $("live-mode").classList.toggle("selected", !replay);
  $("replay-mode").setAttribute("aria-pressed", replay);
  $("live-mode").setAttribute("aria-pressed", !replay);
  $("mode-caption").textContent = replay
    ? window.DEMO_BUNDLE
      ? `오프라인 체험 · 포함 시각 ${times.length}개. 모든 수량은 2025년 과거 기록이며 성능표는 전체 시험 결과입니다.`
      : "2025년 3월의 실제 수량으로 대여소 선택을 체험합니다. 예측 대상은 다음 1시간 기록입니다."
    : "공식 API의 현재 수량과 B1S 기본 모형의 참고 신호입니다. 현재 정확도·실시간 M1은 미검증입니다.";
  $("recommendations").classList.toggle("hidden", !replay);
  $("scenarios-open").disabled = !replay;
  $("time-play").disabled = !replay;
  $("map-near").classList.toggle("selected", state.mapView === "near");
  $("map-city").classList.toggle("selected", state.mapView === "city");
  $("map-near").setAttribute("aria-pressed", state.mapView === "near");
  $("map-city").setAttribute("aria-pressed", state.mapView === "city");
  $("map-interaction").textContent = state.pickOrigin
    ? "지도를 누르면 그 지점을 출발지로 지정합니다."
    : state.mapView === "city"
      ? "구 이름 또는 묶음 표시를 눌러 주변을 둘러보세요."
      : "대여소를 눌러 상세 정보를 확인하세요";
  $("set-origin").setAttribute("aria-pressed", state.pickOrigin);
  $("set-origin").textContent = state.pickOrigin
    ? "위치 지정 취소"
    : "지도에서 지정";
}
async function loadReplay({ keepSelection = true } = {}) {
  if (state.mode !== "replay") return;
  const ticket = ++state.request;
  const at = state.at,
    origin = { ...state.origin },
    radius = state.radius;
  updateControls();
  alertMessage("");
  $("station-count").textContent = "대여소 조회 중…";
  try {
    const params = new URLSearchParams({
      at,
      lat: origin.latitude,
      lon: origin.longitude,
      radius,
    });
    const data = await api(`/api/snapshot?${params}`);
    if (ticket !== state.request || state.mode !== "replay") return;
    state.snapshot = { ...data, origin_name: state.originName };
    state.liveStale = false;
    $("target-time").textContent =
      `${timeLabel(data.input_time)} 기준 → ${data.target_time.slice(11, 16)} 기록의 0대 위험`;
    if (
      !keepSelection ||
      !data.stations.some((s) => s.station_id === state.activeStation)
    ) {
      state.activeStation =
        data.recommendations[state.model === "M1" ? "R2" : "R1"]?.station_id ||
        data.stations.find((s) => s.quantity > 0)?.station_id ||
        data.stations[0]?.station_id ||
        null;
    }
    renderReplay();
    await Promise.all([loadDetail(), loadCity()]);
  } catch (error) {
    if (ticket !== state.request) return;
    alertMessage(
      `${error.message} · 직전 조회에 성공한 조건을 표시합니다.`,
      true,
    );
    if (state.snapshot) {
      state.at = state.snapshot.input_time;
      state.origin = { ...state.snapshot.origin };
      state.originName = state.snapshot.origin_name || "이전 출발 위치";
      state.radius = state.snapshot.radius_m;
      updateControls();
    }
    $("station-count").textContent = "조회 실패 · 이전 화면 유지";
    stopPlayback();
  }
}
function renderReplay() {
  if (!state.snapshot || state.mode !== "replay") return;
  renderRecommendations();
  renderList();
  drawMap();
  $("list-caption").textContent =
    `${timeLabel(state.snapshot.input_time)} · ${state.model === "M1" ? "주변 정보" : "자기 정보"} 모형 신호 · 직선 ${num(state.radius)}m 범위`;
}
function renderRecommendations() {
  const data = state.snapshot;
  if (!data) return;
  const truth = $("show-truth").checked;
  const labels = {
    R0: ["가장 가까운 곳", "현재 수량 + 직선거리"],
    R1: ["자기 정보 AI", "대여소의 과거 흐름 참고"],
    R2: ["주변 정보 AI", "500m 이웃 정보도 참고"],
  };
  $("recommendation-cards").innerHTML = Object.entries(labels)
    .map(([key, [title, desc]]) => {
      const r = data.recommendations[key];
      return `<button class="rec-card ${key === (state.model === "M1" ? "R2" : "R1") ? "selected" : ""}" ${r ? `data-select="${r.station_id}"` : "disabled"}><div class="method">${key} · ${title}</div><h3>${r ? esc(cleanName(r)) : "추천할 후보가 없습니다"}</h3><p>${desc}</p>${r ? `<span class="rec-quantity">${r.quantity}대</span> <small>· ${num(r.distance_m)}m</small>` : "<small>위험 조건을 충족한 곳 없음</small>"}${r && truth ? `<div class="truth ${r.actual_available ? "good" : "bad"}">다음 기록 ${r.actual_next_quantity}대 · ${r.actual_available ? "수량 있음" : "0대"}</div>` : ""}</button>`;
    })
    .join("");
  const a = data.recommendations.R0,
    b = data.recommendations[state.model === "M1" ? "R2" : "R1"];
  $("recommendation-context").textContent = !b
    ? "AI 조건에 맞는 현재 양수 후보가 없어 추천을 보류했습니다."
    : a?.station_id === b.station_id
      ? "거리 기준과 선택한 AI가 같은 곳을 골랐습니다. 미래 대여를 보장하는 의미는 아닙니다."
      : `선택한 AI는 거리 기준과 다른 곳을 골랐습니다. 추가 직선거리는 약 ${Math.round(b.distance_m - a.distance_m)}m입니다. 실제 도보 경로는 다를 수 있습니다.`;
}
function filteredRows() {
  let rows = currentRows().filter(
    (s) => state.mode === "live" || s.distance_m <= state.radius,
  );
  if (state.filter === "available") rows = rows.filter((s) => s.quantity > 0);
  if (state.filter === "low")
    rows = rows.filter((s) => s.quantity > 0 && risk(s) === false);
  if (state.filter === "favorite")
    rows = rows.filter((s) => state.favorites.has(s.station_id));
  return rows.sort(
    (a, b) =>
      (state.sort === "quantity"
        ? b.quantity - a.quantity
        : state.sort === "risk"
          ? (score(a) ?? 2) - (score(b) ?? 2)
          : a.distance_m - b.distance_m) ||
      a.distance_m - b.distance_m ||
      a.station_id - b.station_id,
  );
}
function renderList() {
  const rows = filteredRows();
  $("visible-count").textContent = rows.length;
  $("download-csv").disabled = !rows.length;
  document.querySelectorAll("[data-filter]").forEach((b) => {
    const active = b.dataset.filter === state.filter;
    b.classList.toggle("selected", active);
    b.setAttribute("aria-pressed", active);
  });
  $("station-list").innerHTML = rows.length
    ? rows
        .slice(0, state.listLimit)
        .map((s) => {
          const [label, style] = status(s);
          return `<article class="station-row ${state.activeStation === s.station_id ? "active" : ""}"><span class="quantity-bubble ${style}" aria-label="현재 ${s.quantity}대">${s.quantity}</span><button class="station-main" data-select="${s.station_id}"><strong>${esc(cleanName(s))}</strong><small>${s.station_id ?? "번호 미확인"} · 직선 ${num(s.distance_m)}m${state.mode === "replay" && $("show-truth").checked ? ` · 다음 기록 ${s.actual_next_quantity}대` : ""}</small></button><span class="row-status ${style}">${label}</span>${s.station_id != null ? favoriteButton(s.station_id) : ""}</article>`;
        })
        .join("") +
      (rows.length > state.listLimit
        ? `<button class="load-more" data-action="load-more">${Math.min(8, rows.length - state.listLimit)}곳 더 보기 <span>· 전체 ${rows.length}곳</span></button>`
        : "")
    : `<div class="empty-state"><span class="empty-symbol">⌖</span><h3>${state.filter === "favorite" ? "이 주변에 즐겨찾는 대여소가 없습니다" : "조건에 맞는 후보가 없습니다"}</h3><p>${state.mode === "live" ? "실시간 공개 샘플 5곳 중에서 선택한 조건에 맞는 곳이 없습니다." : "필터를 초기화하거나 비교 범위를 넓혀 보세요. 과거 검증 기록이 있는 대여소만 표시합니다."}</p><button data-action="reset-filters">전체 후보 보기</button>${state.mode === "replay" && state.radius < 1500 ? '<button data-action="expand-radius">1.5km로 넓히기</button>' : ""}</div>`;
}
async function selectStation(id, { scroll = false } = {}) {
  const row = currentRows().find((s) => s.station_id === id);
  if (!row) {
    const meta = state.stationMap.get(id);
    if (meta && state.mode === "replay") {
      state.origin = { latitude: meta.latitude, longitude: meta.longitude };
      state.originName = cleanName(meta);
      state.activeStation = id;
      state.mapView = "near";
      await loadReplay();
    } else toast("이 시각·범위에 해당 대여소의 예측 기록이 없습니다.");
    return;
  }
  state.activeStation = id;
  renderList();
  drawMap();
  await loadDetail();
  if (scroll && innerWidth <= 800)
    $("station-detail").scrollIntoView({ behavior: "smooth", block: "start" });
}
async function loadDetail() {
  const id = state.activeStation,
    ticket = ++state.detailRequest;
  if (id == null) {
    state.detail = null;
    renderDetail();
    return;
  }
  if (state.mode === "live") {
    state.detail = null;
    renderDetail();
    return;
  }
  const key = `${state.at}/${id}`;
  if (detailCache.has(key)) {
    state.detail = detailCache.get(key);
    renderDetail();
    return;
  }
  $("station-detail").innerHTML =
    '<div aria-label="대여소 이력 조회 중"><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div></div>';
  try {
    const detail = await api(
      `/api/station/${id}?${new URLSearchParams({ at: state.at })}`,
    );
    if (ticket !== state.detailRequest || state.mode !== "replay") return;
    detailCache.set(key, detail);
    if (detailCache.size > 80)
      detailCache.delete(detailCache.keys().next().value);
    state.detail = detail;
    renderDetail();
  } catch (error) {
    if (ticket === state.detailRequest) {
      state.detail = null;
      $("station-detail").innerHTML =
        `<div class="empty-state"><h3>상세 자료를 불러오지 못했습니다</h3><p>${esc(error.message)}</p><button data-action="retry-detail">다시 조회</button></div>`;
    }
  }
}
function chart(history) {
  if (!history?.length) return "";
  const width = 260,
    height = 115,
    pad = { l: 24, r: 8, t: 13, b: 23 },
    known = history.filter((h) => h.quantity != null),
    max = Math.max(4, ...known.map((h) => h.quantity));
  const x = (i) =>
      pad.l + (i / Math.max(1, history.length - 1)) * (width - pad.l - pad.r),
    y = (q) => height - pad.b - (q / max) * (height - pad.b - pad.t);
  let paths = [],
    run = [];
  history.forEach((h, i) => {
    if (h.quantity == null) {
      if (run.length) paths.push(run);
      run = [];
    } else run.push([x(i), y(h.quantity)]);
  });
  if (run.length) paths.push(run);
  const grids = [0, Math.round(max / 2), max]
    .map(
      (q) =>
        `<line x1="${pad.l}" y1="${y(q)}" x2="${width - pad.r}" y2="${y(q)}" stroke="#e8eedf" stroke-dasharray="3 4"/><text x="${pad.l - 6}" y="${y(q) + 3}" text-anchor="end" fill="#a3b295" font-size="8">${q}</text>`,
    )
    .join("");
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="선택한 기준 시각까지 과거 24시간의 관측 수량. 누락은 선이 끊어집니다.">${grids}${paths.map((points) => `<polyline points="${points.map((p) => p.join(",")).join(" ")}" fill="none" stroke="#759768" stroke-width="2.2" stroke-linejoin="round"/>`).join("")}${history.map((h, i) => (h.quantity == null ? "" : `<circle cx="${x(i)}" cy="${y(h.quantity)}" r="${i === history.length - 1 ? 3.5 : 2}" fill="${i === history.length - 1 ? "#285c47" : "#91ad7f"}"><title>${esc(timeLabel(h.time))} · ${h.quantity}대</title></circle>`)).join("")}<text x="${pad.l}" y="${height - 5}" fill="#a0af92" font-size="8">${history[0].time.slice(11, 16)}</text><text x="${width - pad.r}" y="${height - 5}" text-anchor="end" fill="#a0af92" font-size="8">${history.at(-1).time.slice(11, 16)} 기준</text></svg>`;
}
function renderDetail() {
  const s = currentRows().find((s) => s.station_id === state.activeStation);
  if (!s) {
    $("station-detail").innerHTML =
      '<div class="detail-placeholder"><span>⌖</span><h2>대여소를 골라보세요</h2><p>수량과 과거 변화를<br>한곳에서 확인할 수 있습니다.</p></div>';
    return;
  }
  const d = state.mode === "replay" ? state.detail : null,
    [label, style] = status(s),
    v = score(s),
    st = state.stationMap.get(s.station_id);
  const mapURL = `https://map.kakao.com/link/map/${encodeURIComponent(cleanName(s))},${s.latitude},${s.longitude}`;
  const neighbor = d?.neighbors;
  const known = d?.history?.filter((h) => h.quantity != null) || [];
  const change = d?.change_1h;
  const changeText =
    change == null
      ? "이전 기록 없음"
      : change === 0
        ? "1시간 전과 같음"
        : `1시간 전보다 ${Math.abs(change)}대 ${change > 0 ? "늘었음" : "줄었음"}`;
  $("station-detail").innerHTML =
    `<p class="station-meta">${s.station_id ?? "번호 미확인"} · ${esc(s.district || st?.district || "실시간 샘플")}</p><div class="detail-heading"><h2>${esc(cleanName(s))}</h2>${s.station_id != null ? favoriteButton(s.station_id) : ""}</div><p class="detail-address">${esc(d?.address || st?.address || "")} · 출발지에서 직선 ${num(s.distance_m)}m</p><div class="stock-hero ${state.mode === "live" && (state.liveStale || state.receiptOld) ? "live-stale" : ""}"><div><small>${state.mode === "replay" ? "선택 시각의 수량" : state.liveStale ? "갱신 실패 · 이전 수신 수량" : state.receiptOld ? "이전 수신 수량 · 재조회 권장" : "공식 API 수신 수량"}</small><strong>${s.quantity}<span>대</span></strong></div><span class="row-status ${style}">${label}</span></div>${state.mode === "replay" ? `<div class="model-selector segmented"><button data-model="B1" class="${state.model === "B1" ? "selected" : ""}" aria-pressed="${state.model === "B1"}">자기 정보 B1</button><button data-model="M1" class="${state.model === "M1" ? "selected" : ""}" aria-pressed="${state.model === "M1"}">주변 정보 M1</button></div>` : ""}<div class="risk-line"><span>${state.mode === "replay" ? "다음 기록의 0대 위험" : "기본 모형 B1S 참고 신호"}</span><strong class="${style === "high" ? "risk-high" : ""}">${v == null ? "예측 보류" : label}</strong></div>${v == null ? "" : `<div class="risk-meter"><i style="left:${Math.max(0, Math.min(100, v * 100))}%"></i></div><p class="risk-caption">모형 점수 ${v.toFixed(3)} / 1 · 대여 실패 확률로 보정된 값은 아닙니다.</p>`}${state.mode === "replay" && $("show-truth").checked ? `<div class="finding truth ${s.actual_next_quantity > 0 ? "good" : "bad"}">${state.snapshot.target_time.slice(11, 16)} 실제 다음 기록: <strong>${s.actual_next_quantity}대</strong><br><small>검증용 정답 · 위 예측의 입력에 사용하지 않음</small></div>` : ""}${d ? `<section class="detail-section"><h3>지난 24시간, 어떻게 변했나요?</h3><p class="fine-print">${changeText}</p><div class="history-chart">${chart(d.history)}</div><div class="chart-summary"><span>최저 ${known.length ? Math.min(...known.map((h) => h.quantity)) : "—"}대 / 최고 ${known.length ? Math.max(...known.map((h) => h.quantity)) : "—"}대</span><span>관측 ${known.length}/${d.history.length}시간</span></div></section><section class="detail-section"><h3>주변 500m의 현재 상황</h3><div class="facts-grid"><div class="fact-card"><small>관측된 이웃</small><strong>${neighbor.observed}<small style="display:inline"> / ${neighbor.total}곳</small></strong></div><div class="fact-card"><small>현재 0대인 이웃</small><strong>${neighbor.empty}<small style="display:inline">곳</small></strong></div></div><ul class="neighbor-list">${neighbor.stations.map((n) => `<li><button data-select="${n.station_id}">${esc(n.name)}</button><span>${n.quantity == null ? "미관측" : `${n.quantity}대`} · ${n.distance_m}m</span></li>`).join("")}</ul><p class="detail-note">현재·과거 관측을 보여 줍니다. 이 정보가 위험 예측의 원인이라고 확정할 수는 없습니다.</p></section>` : state.mode === "live" ? `<section class="detail-section"><h3>현재 입력으로 실행한 참고 신호</h3><p class="fine-print">${esc(s.prediction_status)}</p><p class="detail-note">수신: ${esc(state.live.received_kst.replace("T", " ").slice(0, 19))} KST<br>대여소별 원천 관측시각은 응답에 없습니다. 과거 이력·주변 입력을 갖춘 M1의 실시간 결과가 아닙니다.</p></section>` : ""}<a class="map-link" href="${esc(mapURL)}" target="_blank" rel="noreferrer">외부 지도에서 위치 보기 ↗<small>카카오맵 · 실제 도로와 주변 길 확인</small></a>`;
}
async function loadCity() {
  if (state.mode !== "replay") return;
  const at = state.at;
  if (cityCache.has(at)) {
    state.city = cityCache.get(at);
    drawMap();
    return;
  }
  try {
    const data = await api(`/api/city?${new URLSearchParams({ at })}`);
    cityCache.set(at, data);
    if (cityCache.size > 15) cityCache.delete(cityCache.keys().next().value);
    if (state.at === at && state.mode === "replay") {
      state.city = data;
      drawMap();
    }
  } catch {
    if (state.mapView === "city")
      toast("서울 전체 자료를 불러오지 못해 주변 자료만 표시합니다.");
  }
}
function svgElement(tag, attrs, text) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (text != null) el.textContent = text;
  return el;
}
function drawMap() {
  const width = Math.max(280, $("map").clientWidth),
    height = $("map").clientHeight || 385;
  const city = state.mapView === "city";
  const origin =
    state.mode === "live" && state.live?.rows?.length
      ? {
          latitude:
            state.live.rows.reduce((a, r) => a + r.latitude, 0) /
            state.live.rows.length,
          longitude:
            state.live.rows.reduce((a, r) => a + r.longitude, 0) /
            state.live.rows.length,
        }
      : state.origin;
  const center = city ? { latitude: 37.565, longitude: 126.99 } : origin,
    meters = city ? 20500 : state.mode === "live" ? 1600 : state.radius * 1.35,
    cos = Math.cos((center.latitude * Math.PI) / 180),
    scale = (Math.min(width, height) / (2 * meters)) * state.mapScale;
  const project = (lat, lon) => [
    width / 2 + (lon - center.longitude) * 111195 * cos * scale,
    height / 2 + (center.latitude - lat) * 111195 * scale,
  ];
  const svg = svgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": city
      ? "서울 행정구역 참고도와 과거 대여소 수량"
      : "출발지 주변 대여소 위치와 수량",
  });
  svg.classList.toggle("map-select-origin", state.pickOrigin);
  svg.append(svgElement("rect", { width, height, fill: "#f1f4eb" }));
  // Real administrative geometry; no invented streets or walking routes.
  if (state.boundary)
    for (const f of state.boundary.features) {
      const rings =
        f.geometry.type === "Polygon"
          ? f.geometry.coordinates
          : f.geometry.coordinates.flat();
      let path = "";
      for (const ring of rings) {
        path +=
          ring
            .map(
              ([lon, lat], i) =>
                `${i ? "L" : "M"}${project(lat, lon).join(",")}`,
            )
            .join(" ") + "Z";
      }
      const shape = svgElement("path", {
        d: path,
        fill: city ? "#e9eedf" : "#edf2e4",
        stroke: city ? "#c9d4bc" : "#dce5d1",
        "stroke-width": city ? 1.1 : 1.5,
        "fill-rule": "evenodd",
      });
      svg.append(shape);
      if (city) {
        const points = rings[0];
        const lat = points.reduce((a, p) => a + p[1], 0) / points.length,
          lon = points.reduce((a, p) => a + p[0], 0) / points.length,
          [x, y] = project(lat, lon);
        const label = svgElement(
          "text",
          { x, y, class: "district-label", "text-anchor": "middle" },
          f.properties.name,
        );
        label.style.cursor = "pointer";
        label.style.pointerEvents = "all";
        label.addEventListener("click", () =>
          setOrigin(lat, lon, f.properties.name),
        );
        svg.append(label);
      }
    }
  if (!city && state.mode === "replay")
    svg.append(
      svgElement("circle", {
        cx: width / 2,
        cy: height / 2,
        r: state.radius * scale,
        fill: "#f8faf360",
        stroke: "#b5c7a2",
        "stroke-width": 1.3,
        "stroke-dasharray": "5 6",
      }),
    );
  let rows = currentRows();
  if (city && state.mode === "replay" && state.city?.input_time === state.at)
    rows = state.city.rows.map(([id, q, b, m]) => ({
      ...state.stationMap.get(id),
      quantity: q,
      score_B1: b,
      score_M1: m,
    }));
  const visible = rows.filter((s) => {
    const [x, y] = project(s.latitude, s.longitude);
    return x > 8 && x < width - 8 && y > 25 && y < height - 15;
  });
  if (city) {
    const groups = new Map();
    for (const s of visible) {
      const [x, y] = project(s.latitude, s.longitude),
        key = `${Math.floor(x / 24)}/${Math.floor(y / 24)}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(s);
    }
    for (const group of groups.values()) {
      const lat = group.reduce((a, s) => a + s.latitude, 0) / group.length,
        lon = group.reduce((a, s) => a + s.longitude, 0) / group.length,
        [x, y] = project(lat, lon),
        available = group.filter((s) => s.quantity > 0).length;
      const g = svgElement("g", {
        class: "marker",
        role: "button",
        tabindex: 0,
        "aria-label": `${group.length}개 대여소 주변 탐색`,
      });
      g.append(
        svgElement("circle", {
          cx: x,
          cy: y,
          r: group.length === 1 ? 4 : Math.min(14, 7 + Math.log(group.length)),
          fill: available ? "#6d9255" : "#b7bda8",
          opacity: 0.76,
          stroke: "#fff",
          "stroke-width": 1.5,
        }),
      );
      if (group.length > 4)
        g.append(
          svgElement(
            "text",
            {
              x,
              y: y + 3,
              "text-anchor": "middle",
              fill: "white",
              "font-size": 8,
              "font-weight": 700,
            },
            group.length,
          ),
        );
      const action = () =>
        setOrigin(
          lat,
          lon,
          group.length === 1
            ? cleanName(group[0])
            : `${group[0].district || "선택한 지역"} 주변`,
        );
      g.addEventListener("click", (e) => {
        e.stopPropagation();
        action();
      });
      g.addEventListener("keydown", (e) => {
        if (e.key === "Enter") action();
      });
      svg.append(g);
    }
  } else
    for (const s of [...visible].sort(
      (a, b) =>
        (a.station_id === state.activeStation) -
        (b.station_id === state.activeStation),
    )) {
      const [x, y] = project(s.latitude, s.longitude),
        active = s.station_id === state.activeStation,
        [label, style] = status(s),
        fill =
          style === "empty"
            ? "#b0baa3"
            : style === "high"
              ? "#c39a52"
              : style === "unknown"
                ? "#a6b99a"
                : "#658d50",
        rec =
          state.snapshot?.recommendations[state.model === "M1" ? "R2" : "R1"];
      const g = svgElement("g", {
        class: "marker",
        tabindex: 0,
        role: "button",
        "aria-label": `${cleanName(s)}, 현재 ${s.quantity}대, ${label}`,
      });
      if (active)
        g.append(
          svgElement("circle", {
            cx: x,
            cy: y,
            r: 25,
            fill: "#648d511a",
            stroke: "#70965b",
            "stroke-width": 1.5,
          }),
        );
      g.append(
        svgElement("circle", {
          cx: x,
          cy: y,
          r: active ? 19 : 16,
          fill,
          stroke: "white",
          "stroke-width": 3,
          class: "marker-ring",
        }),
      );
      g.append(
        svgElement(
          "text",
          {
            x,
            y: y + 4,
            "text-anchor": "middle",
            fill: "white",
            "font-size": 12,
            "font-weight": 700,
          },
          s.quantity,
        ),
      );
      g.append(
        svgElement("title", {}, `${cleanName(s)} · ${s.quantity}대 · ${label}`),
      );
      if (active || visible.length <= 9 || rec?.station_id === s.station_id) {
        const name = cleanName(s);
        g.append(
          svgElement(
            "text",
            {
              x,
              y: y + 33,
              "text-anchor": "middle",
              class: "map-label",
              "font-size": 10,
            },
            name.length > 14 ? name.slice(0, 14) + "…" : name,
          ),
        );
      }
      g.addEventListener("click", (event) => {
        event.stopPropagation();
        selectStation(s.station_id, { scroll: innerWidth <= 800 });
      });
      g.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          selectStation(s.station_id);
        }
      });
      svg.append(g);
    }
  const [ox, oy] = project(state.origin.latitude, state.origin.longitude);
  if (ox > 0 && ox < width && oy > 0 && oy < height) {
    svg.append(
      svgElement("circle", { cx: ox, cy: oy, r: 13, fill: "#3d638324" }),
    );
    svg.append(
      svgElement("circle", {
        cx: ox,
        cy: oy,
        r: 5,
        fill: "#416885",
        stroke: "white",
        "stroke-width": 2,
      }),
    );
    svg.append(
      svgElement("text", { x: ox + 12, y: oy - 8, class: "map-label" }, "출발"),
    );
  }
  const scaleMeters = city ? 5000 : state.mode === "live" ? 500 : 200,
    scaleWidth = scaleMeters * scale;
  svg.append(
    svgElement("line", {
      x1: 18,
      y1: height - 19,
      x2: 18 + scaleWidth,
      y2: height - 19,
      stroke: "#98ad85",
      "stroke-width": 2,
    }),
  );
  svg.append(
    svgElement(
      "text",
      { x: 18, y: height - 26, fill: "#8fa47b", "font-size": 9 },
      `${num(scaleMeters)}m · 직선`,
    ),
  );
  svg.append(
    svgElement(
      "text",
      {
        x: width - 20,
        y: height - 19,
        fill: "#9eae8c",
        "font-size": 10,
        "text-anchor": "end",
      },
      "N ↑",
    ),
  );
  svg.addEventListener("click", (event) => {
    if (!state.pickOrigin) return;
    const rect = svg.getBoundingClientRect(),
      x = ((event.clientX - rect.left) / rect.width) * width,
      y = ((event.clientY - rect.top) / rect.height) * height;
    const lat = center.latitude + (height / 2 - y) / scale / 111195,
      lon = center.longitude + (x - width / 2) / scale / 111195 / cos;
    if (lat < 37 || lat > 38 || lon < 126 || lon > 128) {
      toast("서울 인근의 위치를 선택해 주세요.");
      return;
    }
    state.pickOrigin = false;
    setOrigin(lat, lon, "지도에서 선택한 위치");
  });
  $("map").replaceChildren(svg);
  $("map-legend").innerHTML = city
    ? '<span><i class="dot origin"></i>출발 위치</span><span><i class="dot low"></i>양수 대여소 포함</span><span><i class="dot empty"></i>모두 0대</span><span>숫자 = 묶인 대여소 수 · 위험 판정 아님</span>'
    : '<span><i class="dot origin"></i>출발 위치</span><span><i class="dot low"></i>위험 낮게 예상</span><span><i class="dot high"></i>위험 주의</span><span><i class="dot empty"></i>현재 0대</span><span><i class="dot unknown"></i>예측 없음</span>';
  $("station-count").textContent = city
    ? `${num(rows.length)}곳 · ${state.mode === "replay" ? "해당 시각 시험 기록" : "실시간 샘플"}`
    : `${currentRows().length}곳 · ${state.mode === "live" ? "실시간 샘플" : num(state.radius) + "m"}`;
  $("map-subtitle").textContent = city
    ? "2013 행정구역 참고 경계 · 묶음 숫자는 대여소 수"
    : "현재 수량을 누르면 과거 변화와 주변 상황을 볼 수 있습니다.";
}
async function setOrigin(latitude, longitude, name) {
  stopPlayback();
  state.origin = { latitude, longitude };
  state.originName = name;
  state.mapView = "near";
  state.mapScale = 1;
  state.activeStation = null;
  state.listLimit = 8;
  updateControls();
  if (state.mode === "replay") await loadReplay({ keepSelection: false });
  else {
    renderLive();
    toast("실시간 샘플 5곳 기준의 거리를 다시 계산했습니다.");
  }
}
function setTime(value) {
  if (!state.overview.times.includes(value)) return;
  state.at = value;
  state.city = null;
  loadReplay();
}
function stepTime(delta) {
  const times = state.overview.times,
    i = times.indexOf(state.at),
    next = i + delta;
  if (next < 0 || next >= times.length) {
    stopPlayback();
    return;
  }
  setTime(times[next]);
}
function stopPlayback() {
  state.playing = false;
  clearTimeout(playbackTimer);
  $("time-play").textContent = "▷ 자동 재생";
  $("time-play").classList.remove("playing");
  $("time-play").setAttribute("aria-pressed", "false");
}
function startPlayback() {
  if (state.mode !== "replay") return;
  state.playing = true;
  $("time-play").textContent = "Ⅱ 재생 멈춤";
  $("time-play").classList.add("playing");
  $("time-play").setAttribute("aria-pressed", "true");
  const tick = async () => {
    if (!state.playing || document.hidden || state.tab !== "explore") {
      stopPlayback();
      return;
    }
    const times = state.overview.times,
      i = times.indexOf(state.at);
    if (i >= times.length - 1) {
      stopPlayback();
      return;
    }
    state.at = times[i + 1];
    state.city = null;
    await loadReplay();
    if (state.playing) playbackTimer = setTimeout(tick, 2200);
  };
  playbackTimer = setTimeout(tick, 1000);
}
async function loadLive() {
  if (state.mode !== "live" || state.liveBusy) return;
  state.liveBusy = true;
  const ticket = ++state.request;
  $("refresh-live").disabled = true;
  $("refresh-live").textContent = "현재 수량 조회 중…";
  alertMessage("");
  try {
    const packet = await api("/api/live");
    if (ticket !== state.request || state.mode !== "live") return;
    state.live = packet;
    state.liveStale = false;
    if (!packet.rows?.length) {
      state.activeStation = null;
      renderLive();
      alertMessage(
        "정상 응답에 유효한 대여소가 없습니다. 예측을 보류합니다.",
        true,
      );
      return;
    }
    if (!packet.rows.some((s) => s.station_id === state.activeStation))
      state.activeStation =
        packet.rows.find((s) => s.quantity > 0)?.station_id ||
        packet.rows[0].station_id;
    renderLive();
  } catch (error) {
    if (ticket !== state.request) return;
    state.liveStale = !!state.live;
    alertMessage(
      `${error.message}${state.live ? " 아래에는 이전 수신 자료가 남아 있습니다. 새로 받은 값이 아닙니다." : ""}`,
      true,
    );
    if (state.live) renderLive();
    else {
      $("station-count").textContent = "실시간 조회 실패";
      $("station-list").innerHTML =
        '<div class="empty-state"><h3>현재 수량을 받지 못했습니다</h3><p>다시 조회하거나 과거 데이터 체험을 이용해 주세요.</p></div>';
      $("station-detail").innerHTML =
        '<div class="detail-placeholder"><h2>실시간 자료 없음</h2><p>실제 응답이 도착하기 전에는 수량·예측을 표시하지 않습니다.</p></div>';
      $("map").replaceChildren();
    }
  } finally {
    if (ticket === state.request) {
      state.liveBusy = false;
      $("refresh-live").disabled = false;
      $("refresh-live").textContent = "↻ 현재 수량 새로고침";
    }
  }
}
function renderLive() {
  if (!state.live || state.mode !== "live") return;
  const d = state.live;
  updateControls();
  state.receiptOld = Date.now() - Date.parse(d.received_at) > 120000;
  $("live-status").innerHTML =
    `<strong>${state.liveStale ? "갱신 실패 · 이전 자료" : state.receiptOld ? "이전 수신 자료 · 재조회 권장" : "수신 완료"}</strong><br>${esc(d.received_kst.replace("T", " ").slice(0, 19))} KST · ${d.count}곳${d.reused_cache ? " · 최근 응답 재사용" : ""}<br><span id="receipt-age"></span> · 원천 관측시각 미제공`;
  $("list-caption").textContent =
    "공식 현재 수량 · B1S 모형 참고 신호 · 전체 M1과 구분";
  renderList();
  renderDetail();
  drawMap();
  updateReceiptAge();
}
function updateReceiptAge() {
  if (state.mode !== "live" || !state.live || document.hidden) return;
  const seconds = Math.max(
    0,
    Math.floor((Date.now() - Date.parse(state.live.received_at)) / 1000),
  );
  const label = $("receipt-age");
  if (label)
    label.textContent = `우리 수신 후 ${seconds < 60 ? seconds + "초" : Math.floor(seconds / 60) + "분"} 경과`;
  const older = seconds > 120;
  if (older && !state.receiptOld) {
    state.receiptOld = true;
    $("live-status").querySelector("strong").textContent = state.liveStale
      ? "갱신 실패 · 이전 자료"
      : "이전 수신 자료 · 재조회 권장";
    const stock = document.querySelector(".stock-hero");
    if (stock) {
      stock.classList.add("live-stale");
      stock.querySelector("small").textContent = "이전 수신 수량 · 재조회 권장";
    }
  }
}
setInterval(updateReceiptAge, 1000);
function stopLiveTimer() {
  clearInterval(liveTimer);
  liveTimer = null;
}
function startLiveTimer() {
  stopLiveTimer();
  if (
    $("auto-live").checked &&
    state.mode === "live" &&
    state.tab === "explore" &&
    !document.hidden
  )
    liveTimer = setInterval(loadLive, 60000);
}
async function changeMode(mode) {
  if (window.DEMO_BUNDLE && mode === "live") {
    toast("실시간 연결은 Python 서버 데모에서 제공합니다.");
    return;
  }
  stopPlayback();
  stopLiveTimer();
  state.request++;
  state.detailRequest++;
  if (mode === "live" && state.mode !== "live") {
    state.archiveOrigin = {
      origin: { ...state.origin },
      name: state.originName,
    };
    state.origin = { latitude: 37.552, longitude: 126.913 };
    state.originName = "망원·합정 샘플 인근";
  } else if (mode === "replay" && state.archiveOrigin) {
    state.origin = state.archiveOrigin.origin;
    state.originName = state.archiveOrigin.name;
    state.archiveOrigin = null;
  }
  state.liveBusy = false;
  state.mode = mode;
  state.activeStation = null;
  state.detail = null;
  state.mapView = "near";
  state.mapScale = 1;
  updateControls();
  if (mode === "replay") await loadReplay();
  else {
    await loadLive();
    startLiveTimer();
  }
}
function renderFavorites() {
  const list = [...state.favorites]
    .map((id) => state.stationMap.get(id))
    .filter(Boolean);
  $("favorites-list").innerHTML = list.length
    ? list
        .map(
          (s) =>
            `<div class="favorite-modal-row"><button data-visit="${s.station_id}"><strong>${esc(cleanName(s))}</strong><small>${esc(s.district)} · ${s.station_id}번 · 선택하면 주변 탐색</small></button>${favoriteButton(s.station_id)}</div>`,
        )
        .join("")
    : '<div class="empty-state"><span class="empty-symbol">♡</span><h3>자주 쓰는 곳을 저장하세요</h3><p>대여소 옆 하트를 누르면 이 브라우저에 저장합니다.</p></div>';
}
function renderScenarios() {
  const kind = state.caseKind;
  document
    .querySelectorAll("[data-case-kind]")
    .forEach((b) =>
      b.classList.toggle("selected", b.dataset.caseKind === kind),
    );
  const items = state.cases?.[kind] || [];
  $("scenario-list").innerHTML = items
    .map(
      (x, i) =>
        `<button class="scenario-row" data-scenario="${i}"><strong>${kind === "helped" ? "도움된" : "불리한"} 선택 ${i + 1} · ${esc(x.origin_near.trim())}</strong><span>${timeLabel(x.input_time.replace(" ", "T"))} → ${x.target_time.slice(11, 16)} · 후보 ${x.eligible_candidates}곳</span><span>거리 기준 다음 ${x.R0_target_quantity ?? "보류"}대 / 주변 AI ${x.R2_recommended ? `다음 ${x.R2_target_quantity}대` : "추천 보류"}</span></button>`,
    )
    .join("");
}
async function chooseCase(kind, index = 0) {
  const x = state.cases?.[kind]?.[index];
  if (!x) return;
  stopPlayback();
  stopLiveTimer();
  state.mode = "replay";
  state.at = x.input_time.replace(" ", "T");
  state.origin = { latitude: x.latitude, longitude: x.longitude };
  state.originName = x.origin_near.trim();
  state.activeStation = x.R2_station_id || x.R0_station_id;
  state.radius = 500;
  state.filter = "all";
  state.mapView = "near";
  state.mapScale = 1;
  $("show-truth").checked = true;
  updateControls();
  tab("explore");
  await loadReplay();
  alertMessage(
    `${kind === "helped" ? "도움된" : "불리한"} 실제 시험 사례 ${index + 1}입니다. 선택된 사례는 전체 평균을 대표하지 않습니다.`,
  );
}
function shareView() {
  const url = new URL(location.href);
  url.hash = "";
  for (const [k, v] of Object.entries({
    mode: state.mode,
    at: state.at,
    lat: state.origin.latitude.toFixed(6),
    lon: state.origin.longitude.toFixed(6),
    radius: state.radius,
    station: state.activeStation ?? "",
    model: state.model,
  }))
    url.searchParams.set(k, String(v));
  $("share-url").value = url.toString();
  openDialog("share-dialog");
}
function exportCSV() {
  const rows = filteredRows(),
    truth = state.mode === "replay" && $("show-truth").checked;
  const cell = (v) => {
    let s = String(v ?? "");
    if (/^[=+@-]/.test(s)) s = "'" + s;
    return '"' + s.replaceAll('"', '""') + '"';
  };
  const header = [
    "자료모드",
    "기준또는수신시각",
    "대여소번호",
    "대여소명",
    "현재수량",
    "직선거리_m",
    "참고모형",
    "위험점수",
    "위험신호",
    ...(truth ? ["실제다음기록수량"] : []),
  ];
  const output = [
    header,
    ...rows.map((s) => [
      state.mode,
      state.mode === "replay" ? state.at : state.live.received_kst,
      s.station_id,
      cleanName(s),
      s.quantity,
      s.distance_m,
      state.mode === "live" ? "B1S" : state.model,
      score(s),
      status(s)[0],
      ...(truth ? [s.actual_next_quantity] : []),
    ]),
  ]
    .map((row) => row.map(cell).join(","))
    .join("\r\n");
  const blob = new Blob(["\uFEFF" + output], {
      type: "text/csv;charset=utf-8",
    }),
    url = URL.createObjectURL(blob),
    a = document.createElement("a");
  a.href = url;
  a.download = `따릉이_후보_${state.mode}_${state.at.slice(0, 10)}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  toast(`${rows.length}곳의 조회 조건과 수량을 CSV로 저장했습니다.`);
}
const tourSteps = [
  [
    "대여소 선택이라는 문제",
    "현재 수량·거리·다음 시간대 위험을 한 화면에서 비교합니다.",
  ],
  [
    "선택 기준 세 가지 비교",
    "거리 기준과 두 AI의 추천이 어떻게 다른지 살펴봅니다.",
  ],
  [
    "선택한 대여소의 근거 확인",
    "과거 24시간 수량과 주변 500m의 상황을 보여 줍니다.",
  ],
  [
    "좋지 않았던 선택도 공개",
    "다음 기록의 실제 결과를 켜고 실패 사례를 함께 봅니다.",
  ],
  [
    "전체 실험 결과로 판단",
    "성공 사례 몇 개가 아니라 전체 시험의 F1·추천 결과로 판단합니다.",
  ],
];
async function showTour(i) {
  state.tour = Math.max(0, Math.min(tourSteps.length - 1, i));
  stopPlayback();
  stopLiveTimer();
  $("tour-bar").classList.remove("hidden");
  $("tour-progress").textContent =
    `DEMO WALKTHROUGH · ${state.tour + 1} / ${tourSteps.length}`;
  $("tour-title").textContent = tourSteps[state.tour][0];
  $("tour-description").textContent = tourSteps[state.tour][1];
  $("tour-prev").disabled = state.tour === 0;
  $("tour-next").textContent =
    state.tour === tourSteps.length - 1 ? "시연 마치기 ✓" : "다음 장면 →";
  if (state.tour === 0 || state.tour === 1) {
    await chooseCase("helped", 0);
    if (state.tour === 0) {
      $("show-truth").checked = false;
      renderReplay();
      renderDetail();
    }
  }
  if (state.tour === 2) {
    tab("explore");
    if (state.snapshot?.recommendations.R2)
      await selectStation(state.snapshot.recommendations.R2.station_id, {
        scroll: true,
      });
  }
  if (state.tour === 3) await chooseCase("hurt", 0);
  if (state.tour === 4) tab("results");
  window.scrollTo({ top: 0, behavior: "smooth" });
}
function endTour() {
  state.tour = -1;
  $("tour-bar").classList.add("hidden");
  if (state.mode === "live") startLiveTimer();
}

async function renderResearchResults() {
  try {
    const packet = await api("/api/research");
    if (packet.status === "not_started") return;
    let panel = $("research-results");
    if (!panel) {
      panel = document.createElement("section");
      panel.id = "research-results";
      panel.className = "panel result-panel";
      $("legacy-result-heading").before(panel);
    }
    if (packet.status !== "evaluated") {
      panel.innerHTML = `<p class="eyebrow">FOLLOW-UP RESEARCH</p><h2>추가 방법론 검증이 진행 중입니다</h2><p>완료한 1차 후보 ${num(packet.completed_screen_models)}개 · 현재 최고 개발 검증 F1 ${pct(packet.best_development_validation_f1)}.</p><p class="fine-print">이 값은 3/1~14의 개발 검증 점수입니다. 새 최종 시험 성능으로 해석하지 않습니다.</p>`;
      return;
    }
    const r = packet.results;
    const modelNames = {
      B0: "B0 · 현재 상태 유지",
      B1: "B1 · 원래 기본 모델",
      M2: "M2 · 후속 제안 모델",
      B1_tuned: "B1+ · 더 조정한 기본 모델",
    };
    const serviceNames = {
      R0: "R0 · 가까운 곳",
      R1: "R1 · 기본 AI",
      R1u: "R1u · 기본 AI와 거리",
      Rq: "Rq · 수량과 거리",
      R2: "R2 · 제안 AI와 거리",
    };
    const service = r.recommendation.metrics;
    const aiBeatsQuantity =
      service.R2.effective_rate > service.Rq.effective_rate;
    const aiBeatsSamePolicyBaseline =
      service.R2.effective_rate > service.R1u.effective_rate;
    const serviceCI =
      r.recommendation.day_bootstrap.comparisons_R2_minus_baseline.R1u;
    panel.innerHTML = `
      <p class="eyebrow">NEW PERIOD · FROZEN TEST</p>
      <h2>후속 모델 · 새로운 기간에서 다시 확인</h2>
      <p>2025년 4월 15일~5월 14일 · ${num(r.test_rows)}건.<br>1~3월로 학습하고, 4월 1~14일에 선택한 뒤 새 기간에서 확인했습니다.</p>
      <h3>① 0대가 될 곳을 더 잘 맞혔나?</h3>
      <div class="table-scroll"><table><thead><tr><th>방법</th><th>F1</th><th>정밀도</th><th>재현율</th></tr></thead><tbody>${Object.entries(
        r.metrics,
      )
        .map(
          ([name, m]) =>
            `<tr><td>${esc(modelNames[name] || name)}</td><td>${pct(m.f1)}</td><td>${pct(m.precision)}</td><td>${pct(m.recall)}</td></tr>`,
        )
        .join("")}</tbody></table></div>
      <div class="finding">M2 − B1 F1: <strong>${signed(r.deltas.f1_pp, 3)}%p</strong> · 초기 +5%p 목표 <strong>${r.goals.f1_5pp ? "충족" : "미달"}</strong>.<br>현재 수량이 있는 곳의 0대 전환 재현율: M2 ${pct(r.subgroups.current_positive.M2.recall)}.</div>
      <h3>② 그래서 더 나은 곳을 추천했나?</h3>
      <p>가상 출발지에서 추천한 곳에 다음 기록도 1대 이상 남았는지 확인했습니다. 추천을 보류한 요청도 분모에 남깁니다.</p>
      <div class="table-scroll"><table><thead><tr><th>선택 방식</th><th>유효추천율</th><th>유효 건수 / 요청</th><th>추가 직선거리</th></tr></thead><tbody>${Object.entries(
        service,
      )
        .map(
          ([name, m]) =>
            `<tr><td>${esc(serviceNames[name] || name)}</td><td>${pct(m.effective_rate)}</td><td>${num(m.valid_recommendations)} / ${num(m.requests)}</td><td>${Number(m.extra_distance_m).toFixed(1)}m</td></tr>`,
        )
        .join("")}</tbody></table></div>
      <div class="finding">AI 추천 R2 − 거리 기준 R0: <strong>${signed(r.deltas.effective_recommendation_pp, 3)}%p</strong>.<br>같은 추천 규칙의 기본 AI R1u 대비: <strong>${signed(100 * (service.R2.effective_rate - service.R1u.effective_rate), 3)}%p</strong>.<br>${aiBeatsQuantity && aiBeatsSamePolicyBaseline ? "이 시험에서 수량 기준 Rq와 같은 추천 규칙의 기본 AI R1u보다 유효추천율이 높았습니다. 실제 시민의 대여 성공 효과는 추가 확인이 필요합니다." : "수량 기준 Rq 또는 같은 추천 규칙의 기본 AI R1u보다 더 높은 유효추천율을 확인하지 못했습니다. 추가 가치는 아직 확인이 필요합니다."}</div>
      <details class="research-details"><summary>팀 목표 판정과 시험 범위 보기</summary><ul>
        <li>F1 +5%p: ${r.goals.f1_5pp ? "충족" : "미달"}</li>
        <li>기본 모델 대비 재현율 비악화: ${r.goals.recall_no_worse ? "충족" : "미충족"}</li>
        <li>거리 기준 대비 유효추천율 +5%p: ${r.goals.effective_recommendation_5pp ? "충족" : "미달"}</li>
        <li>평균 추가 직선거리 100m 이하: ${r.goals.extra_distance_100m ? "충족" : "미충족"}</li>
      </ul><p>+5%p는 초기 팀 목표이며 학교가 정한 합격선이 아닙니다. 새 시험과 아래 기존 3월 시험은 각각 같은 기간의 기본 모델로 비교합니다. 예측 정답은 다음 기록 수량 0이며 실제 대여 실패를 관측한 자료는 아닙니다.</p><p>날짜별 재표집의 탐색적95% 범위: F1 개선 ${signed(r.f1_day_bootstrap.lower_pp, 3)}~${signed(r.f1_day_bootstrap.upper_pp, 3)}%p · 같은 추천 규칙의 기본 AI 대비 추천 효과 ${signed(serviceCI.lower_pp, 3)}~${signed(serviceCI.upper_pp, 3)}%p. 범위에0이 들어가면 차이의 방향을 확정하지 않습니다.</p></details>
      <p class="fine-print">대여소 탐색 화면은 기존 3월 M1 시험을 재생합니다. 이 표의 M2를 3월에 소급 적용하지 않았습니다. 실제 대여 성공·실시간 정확도 검증과도 구분합니다.</p>
      ${r.weather_used_by_proposal ? '<p class="fine-print">M2에는 1·24시간 전 기상청 과거 관측이 포함됩니다. 실시간 게시 지연과 같은 품질의 제공 여부는 아직 확인하지 않았습니다.</p>' : ""}
      ${packet.report_ready && !window.DEMO_BUNDLE ? '<p><a href="/api/research/report">성능 개선 실험 보고서 Word 내려받기 ↗</a></p>' : ""}`;
  } catch {
    /* v1 servers and finite legacy bundles can omit this optional panel. */
  }
}
function renderResults() {
  renderResearchResults();
  const o = state.overview,
    m = o.model_results.metrics,
    delta = o.model_results.deltas.m1_minus_b1_f1_pp,
    r = o.recommendation_results;
  $("result-summary").innerHTML = [
    ["시험 대상", `${num(m.M1.n)}건`, "대여소 × 다음 한 시간"],
    ["제안 모델 전체 F1", pct(m.M1.f1), "0대 위험이 양성인 분류"],
    [
      "현재 양수 → 다음 0대 탐지율",
      pct(o.subgroups.current_positive.M1.recall),
      "대여자 후보에서의 중요한 한계",
    ],
    [
      "유효추천율 변화",
      r ? `${signed(r.r2_minus_r0_effective_pp, 2)}%p` : "—",
      "R2 − R0 · 과거 자료 기준",
    ],
  ]
    .map(
      ([a, b, c]) =>
        `<article class="metric-card"><small>${a}</small><strong>${b}</strong><p>${c}</p></article>`,
    )
    .join("");
  const names = {
    B0: "B0 · 현재 상태 유지",
    B1: "B1 · 자기 정보 AI",
    M1: "M1 · 주변 정보 추가",
    B1S: "B1S · 실시간 참고 모형",
  };
  $("model-metrics").innerHTML = ["B0", "B1", "M1", "B1S"]
    .map(
      (name) =>
        `<tr><td>${names[name]}</td><td><strong>${pct(m[name].f1)}</strong></td><td>${pct(m[name].precision)}</td><td>${pct(m[name].recall)}</td><td>${num(m[name].fn)}건</td></tr>`,
    )
    .join("");
  const ci = o.model_results.day_block_bootstrap_f1_delta_pp;
  $("model-delta").innerHTML =
    `M1 − B1: <strong>${signed(delta, 3)}%p</strong>. F1 +5%p 목표는 <strong>${o.model_results.goal_verdict.f1_5pp ? "달성" : "미달"}</strong>, 재현율 악화 없음은 <strong>${o.model_results.goal_verdict.recall_no_worse ? "충족" : "미충족"}</strong>입니다. 일 단위 재표집의 탐색적 95% 범위는 ${ci.lower_2_5.toFixed(3)}~${ci.upper_97_5.toFixed(3)}%p입니다. 짧은 시험기간의 결과로 연중·현장 효과를 단정하지 않습니다.`;
  if (r) {
    $("recommendation-metrics").innerHTML = ["R0", "R1", "R2"]
      .map((name) => {
        const x = r.metrics[name];
        return `<tr><td>${name} · ${name === "R0" ? "거리 기준" : name === "R1" ? "기본 AI" : "제안 AI"}</td><td><strong>${pct(x.effective_rate)}</strong></td><td>${pct(x.hit_rate)}</td><td>${pct(x.abstention_rate)}</td><td>${x.paired_extra_distance_vs_R0_m.toFixed(1)}m</td></tr>`;
      })
      .join("");
    $("recommendation-note").textContent =
      `공통 평가 요청 ${num(r.common_eligible_queries)}개. R2 − R0 유효추천율 ${signed(r.r2_minus_r0_effective_pp, 2)}%p. +5%p 목표 ${r.goal_verdict.effective_5pp ? "달성" : "미달"}. R2는 기본 AI R1보다 유효 추천이 5건 적었습니다. 실제 대여 성공률이 아니라 목표 기록 수량 기준입니다.`;
  }
  const groupNames = {
    current_positive: "현재 수량이 있는 곳",
    current_1_to_3: "현재 1~3대인 곳",
    commute: "평일 출퇴근 기준 시각",
  };
  $("subgroups").innerHTML = Object.entries(groupNames)
    .map(([key, label]) => {
      const x = o.subgroups[key];
      return `<article class="subgroup-card"><strong>${label}</strong><p>평가 ${num(x.M1.n)}건 · 실제 0대 ${num(x.M1.zero_cases)}건</p><p>B1 F1 ${pct(x.B1.f1)}</p><p>M1 F1 ${pct(x.M1.f1)} · ${signed(100 * (x.M1.f1 - x.B1.f1), 2)}%p</p></article>`;
    })
    .join("");
  const s = o.splits.splits;
  $("evaluation-limits").innerHTML =
    `<p>학습: ${s.train.target_start.slice(0, 10)}~${s.train.target_end.slice(0, 10)} · ${num(s.train.rows)}건</p><p>검증: ${s.validation.target_start.slice(0, 10)}~${s.validation.target_end.slice(0, 10)} · ${num(s.validation.rows)}건</p><p>시험: ${s.test.target_start.slice(0, 10)}~${s.test.target_end.slice(0, 10)} · ${num(s.test.rows)}건</p>${o.limitations.map((x) => `<p>• ${esc(x)}</p>`).join("")}`;
  $("data-explanation").textContent =
    `서울시 2025년 1분기 수량 원본 ${num(o.data_quality.source_rows)}행을 검사했습니다. 학습기간 변동·양수 수량·위치 조건을 충족한 ${num(o.stations)}개 대여소를 사용합니다. 공식 운영 여부 인증 코호트는 아닙니다.`;
}

function offlineApi(path) {
  const b = window.DEMO_BUNDLE,
    // A fixed parsing base avoids a Windows file URI adding a drive prefix.
    url = new URL(path, "https://offline-demo.invalid/");
  if (url.pathname === "/api/overview") return b.overview;
  if (url.pathname === "/api/stations") return b.stations;
  if (url.pathname === "/api/cases") return b.cases;
  if (url.pathname === "/api/research")
    return b.research || { status: "not_started" };
  if (url.pathname === "/static/seoul_districts.geojson") return b.boundary;
  if (url.pathname === "/api/live")
    throw Error("실시간 조회는 Python 서버 데모에서 사용할 수 있습니다.");
  const at = url.searchParams.get("at"),
    key = at?.length === 16 ? `${at}:00` : at;
  if (url.pathname === "/api/city") {
    const rows = b.snapshots[key];
    if (!rows) throw Error("포함된 시각을 선택해 주세요.");
    return {
      input_time: key,
      scope: "historical_test_records_only",
      rows: rows.map((r) => [r[0], r[1], r[3], r[4]]),
    };
  }
  if (url.pathname.startsWith("/api/station/")) {
    const id = Number(url.pathname.split("/").at(-1)),
      s = b.station_map[id],
      h = b.histories[key];
    if (!s || !h) throw Error("포함된 시각·대여소의 이력만 볼 수 있습니다.");
    const col = b.history_station_ids.indexOf(id);
    if (col < 0) throw Error("이력이 없는 대여소입니다.");
    const current = h.rows.at(-1),
      history = h.times.map((t, i) => ({ time: t, quantity: h.rows[i][col] })),
      near = b.history_station_ids
        .map((sid, i) => ({
          ...b.station_map[sid],
          distance_m: haversine(
            s.latitude,
            s.longitude,
            b.station_map[sid].latitude,
            b.station_map[sid].longitude,
          ),
          quantity: current[i],
        }))
        .filter((r) => r.station_id !== id && r.distance_m <= 500)
        .sort(
          (a, c) => a.distance_m - c.distance_m || a.station_id - c.station_id,
        ),
      observed = near.filter((r) => r.quantity != null),
      previous = history.at(-2)?.quantity ?? null;
    return {
      ...s,
      input_time: key,
      quantity: current[col],
      previous_quantity: previous,
      change_1h:
        previous == null || current[col] == null
          ? null
          : current[col] - previous,
      history,
      neighbors: {
        radius_m: 500,
        total: near.length,
        observed: observed.length,
        empty: observed.filter((r) => r.quantity === 0).length,
        mean_quantity: observed.length
          ? observed.reduce((a, r) => a + r.quantity, 0) / observed.length
          : null,
        stations: near
          .slice(0, 5)
          .map((r) => ({ ...r, distance_m: Math.round(r.distance_m) })),
      },
      history_scope: "observations_at_or_before_input_time",
    };
  }
  if (url.pathname === "/api/snapshot") {
    const rows = b.snapshots[key];
    if (!rows) throw Error("오프라인 파일에 포함된 시각을 선택해 주세요.");
    const lat = +url.searchParams.get("lat"),
      lon = +url.searchParams.get("lon"),
      radius = +url.searchParams.get("radius");
    // Filter and rank by exact distance; rounding is only a display operation.
    const stations = rows
      .map((r) => ({
        ...b.station_map[r[0]],
        station_id: r[0],
        quantity: r[1],
        actual_next_quantity: r[2],
        score_B1: r[3],
        score_M1: r[4],
        risk_B1: r[3] >= b.overview.thresholds.B1,
        risk_M1: r[4] >= b.overview.thresholds.M1,
      }))
      .map((r) => ({
        ...r,
        exact_distance: haversine(lat, lon, r.latitude, r.longitude),
      }))
      .filter((r) => r.exact_distance <= radius)
      .sort(
        (a, c) =>
          a.exact_distance - c.exact_distance || a.station_id - c.station_id,
      );
    const recommendations = {};
    for (const [name, field] of [
      ["R0", null],
      ["R1", "risk_B1"],
      ["R2", "risk_M1"],
    ]) {
      const r = stations.find((r) => r.quantity > 0 && (!field || !r[field]));
      recommendations[name] = r
        ? {
            ...r,
            distance_m: Math.round(r.exact_distance),
            actual_available: r.actual_next_quantity >= 1,
          }
        : null;
    }
    return {
      mode: "replay",
      input_time: key,
      target_time: new Date(new Date(`${key}Z`).getTime() + 3600000)
        .toISOString()
        .slice(0, 19),
      origin: { latitude: lat, longitude: lon },
      radius_m: radius,
      stations: stations.map((r) => ({
        ...r,
        distance_m: Math.round(r.exact_distance),
      })),
      recommendations,
      thresholds: b.overview.thresholds,
    };
  }
  throw Error("오프라인에 포함되지 않은 요청입니다.");
}
let searchIndex = -1,
  searchHits = [];
function showSearch() {
  const q = $("station-search").value.trim().toLocaleLowerCase();
  if (!q) {
    $("search-suggestions").classList.add("hidden");
    $("station-search").setAttribute("aria-expanded", "false");
    return;
  }
  searchHits = state.stations
    .filter((s) =>
      `${s.station_id} ${s.name} ${s.district}`.toLocaleLowerCase().includes(q),
    )
    .slice(0, 8);
  searchIndex = -1;
  $("search-suggestions").innerHTML = searchHits.length
    ? searchHits
        .map(
          (s, i) =>
            `<button id="search-option-${i}" class="suggestion" role="option" aria-selected="false" data-search-select="${s.station_id}"><strong>${esc(cleanName(s))}</strong><small>${esc(s.district)} · ${s.station_id}번</small></button>`,
        )
        .join("")
    : '<div class="empty-state"><p>찾는 대여소가 없습니다. 이름·번호·자치구로 검색해 보세요.</p></div>';
  $("search-suggestions").classList.remove("hidden");
  $("station-search").setAttribute("aria-expanded", "true");
}
async function visitStation(id) {
  const s = state.stationMap.get(id);
  if (!s) return;
  $("station-search").value = cleanName(s);
  $("search-suggestions").classList.add("hidden");
  $("station-search").setAttribute("aria-expanded", "false");
  if (state.mode === "live") {
    toast(
      "실시간은 공개 샘플 5곳만 제공됩니다. 전체 위치 검색은 과거 체험으로 이동합니다.",
    );
    await changeMode("replay");
  }
  state.activeStation = id;
  await setOrigin(s.latitude, s.longitude, cleanName(s));
  if (currentRows().some((r) => r.station_id === id)) await selectStation(id);
}
const places = {
  cityhall: [37.5665, 126.978, "서울시청 인근"],
  mangwon: [37.55565, 126.91063, "망원역 인근"],
  yeouido: [37.5219, 126.9245, "여의도 인근"],
  gangnam: [37.4979, 127.0276, "강남역 인근"],
  jamsil: [37.5133, 127.1001, "잠실역 인근"],
};
document.addEventListener("click", async (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  if (button.dataset.tab) {
    tab(button.dataset.tab);
    return;
  }
  if (button.dataset.go) {
    tab(button.dataset.go);
    window.scrollTo({ top: 0, behavior: "smooth" });
    return;
  }
  if (button.dataset.close) {
    closeDialog(button.dataset.close);
    return;
  }
  if (button.dataset.favorite) {
    toggleFavorite(+button.dataset.favorite);
    return;
  }
  if (button.dataset.select) {
    await selectStation(+button.dataset.select, { scroll: innerWidth <= 800 });
    return;
  }
  if (button.dataset.searchSelect) {
    await visitStation(+button.dataset.searchSelect);
    return;
  }
  if (button.dataset.visit) {
    closeDialog("favorites-dialog");
    await visitStation(+button.dataset.visit);
    return;
  }
  if (button.dataset.place) {
    const [lat, lon, name] = places[button.dataset.place];
    await setOrigin(lat, lon, name);
    return;
  }
  if (button.dataset.radius) {
    state.radius = +button.dataset.radius;
    state.mapScale = 1;
    updateControls();
    if (state.mode === "replay") await loadReplay();
    else renderLive();
    return;
  }
  if (button.dataset.filter) {
    state.filter = button.dataset.filter;
    state.listLimit = 8;
    renderList();
    return;
  }
  if (button.dataset.model) {
    state.model = button.dataset.model;
    renderReplay();
    renderDetail();
    return;
  }
  if (button.dataset.caseKind) {
    state.caseKind = button.dataset.caseKind;
    renderScenarios();
    return;
  }
  if (button.dataset.scenario != null) {
    closeDialog("scenarios-dialog");
    await chooseCase(state.caseKind, +button.dataset.scenario);
    return;
  }
  if (button.dataset.action === "load-more") {
    state.listLimit += 8;
    renderList();
    return;
  }
  if (button.dataset.action === "reset-filters") {
    state.filter = "all";
    renderList();
    return;
  }
  if (button.dataset.action === "expand-radius") {
    state.radius = 1500;
    state.mapScale = 1;
    updateControls();
    await loadReplay();
    return;
  }
  if (button.dataset.action === "retry-detail") {
    await loadDetail();
    return;
  }
});
$("station-search").addEventListener("input", showSearch);
$("search-clear").addEventListener("click", () => {
  $("station-search").value = "";
  showSearch();
  $("station-search").focus();
});
$("station-search").addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    $("search-suggestions").classList.add("hidden");
    $("station-search").setAttribute("aria-expanded", "false");
    return;
  }
  if (["ArrowDown", "ArrowUp"].includes(event.key)) {
    event.preventDefault();
    if (!searchHits.length) return;
    searchIndex = Math.max(
      0,
      Math.min(
        searchHits.length - 1,
        searchIndex + (event.key === "ArrowDown" ? 1 : -1),
      ),
    );
    document
      .querySelectorAll(".suggestion")
      .forEach((el, i) => el.setAttribute("aria-selected", i === searchIndex));
    $("station-search").setAttribute(
      "aria-activedescendant",
      `search-option-${searchIndex}`,
    );
    $("search-option-" + searchIndex)?.scrollIntoView({ block: "nearest" });
  }
  if (event.key === "Enter" && searchHits.length) {
    event.preventDefault();
    visitStation(searchHits[Math.max(0, searchIndex)].station_id);
  }
});
document.addEventListener("click", (event) => {
  if (!event.target.closest(".search-box")) {
    $("search-suggestions").classList.add("hidden");
    $("station-search").setAttribute("aria-expanded", "false");
  }
});
$("replay-date").addEventListener("change", () => {
  stopPlayback();
  const h = state.at.slice(11),
    candidates = state.overview.times.filter((t) =>
      t.startsWith($("replay-date").value),
    );
  setTime(candidates.find((t) => t.endsWith(h)) || candidates[0]);
});
$("replay-hour").addEventListener("change", () => {
  stopPlayback();
  setTime($("replay-hour").value);
});
$("time-slider").addEventListener("input", () => {
  stopPlayback();
  setTime(state.overview.times[+$("time-slider").value]);
});
$("time-prev").addEventListener("click", () => {
  stopPlayback();
  stepTime(-1);
});
$("time-next").addEventListener("click", () => {
  stopPlayback();
  stepTime(1);
});
$("time-play").addEventListener("click", () =>
  state.playing ? stopPlayback() : startPlayback(),
);
$("show-truth").addEventListener("change", () => {
  renderReplay();
  renderDetail();
});
$("sort").addEventListener("change", () => {
  state.sort = $("sort").value;
  state.listLimit = 8;
  renderList();
});
$("replay-mode").addEventListener("click", () => changeMode("replay"));
$("live-mode").addEventListener("click", () => changeMode("live"));
$("refresh-live").addEventListener("click", loadLive);
$("auto-live").addEventListener("change", startLiveTimer);
$("set-origin").addEventListener("click", () => {
  state.pickOrigin = !state.pickOrigin;
  tab("explore");
  updateControls();
  drawMap();
  if (state.pickOrigin && innerWidth <= 800)
    $("map").scrollIntoView({ behavior: "smooth", block: "center" });
});
$("map-near").addEventListener("click", () => {
  state.mapView = "near";
  state.mapScale = 1;
  updateControls();
  drawMap();
});
$("map-city").addEventListener("click", () => {
  state.mapView = "city";
  state.mapScale = 1;
  updateControls();
  drawMap();
  if (state.mode === "replay") loadCity();
});
$("zoom-in").addEventListener("click", () => {
  state.mapScale = Math.min(4, state.mapScale * 1.4);
  drawMap();
});
$("zoom-out").addEventListener("click", () => {
  state.mapScale = Math.max(0.5, state.mapScale / 1.4);
  drawMap();
});
$("map-reset").addEventListener("click", () => {
  state.mapView = "near";
  state.mapScale = 1;
  updateControls();
  drawMap();
});
$("geolocate").addEventListener("click", () => {
  if (!navigator.geolocation) {
    toast("이 브라우저에서는 위치 정보를 사용할 수 없습니다.");
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (position) => {
      const lat = position.coords.latitude,
        lon = position.coords.longitude;
      if (lat < 37.4 || lat > 37.75 || lon < 126.7 || lon > 127.2) {
        toast(
          "서울 인근 위치에서 이용해 주세요. 대여소 검색으로 직접 선택할 수도 있습니다.",
        );
        return;
      }
      setOrigin(lat, lon, "브라우저에서 확인한 내 위치");
      toast(
        state.mode === "replay"
          ? "현재 위치에서 과거 수량을 탐색합니다. 수량의 기준 시각은 선택한 2025년 날짜입니다."
          : "현재 위치와 실시간 샘플 사이의 직선거리를 표시합니다.",
      );
    },
    () =>
      toast(
        "위치 권한 또는 연결을 확인해 주세요. 대여소 검색으로도 위치를 정할 수 있습니다.",
      ),
    { enableHighAccuracy: false, timeout: 10000, maximumAge: 60000 },
  );
});
$("favorites-open").addEventListener("click", () => {
  renderFavorites();
  openDialog("favorites-dialog");
});
$("scenarios-open").addEventListener("click", () => {
  renderScenarios();
  openDialog("scenarios-dialog");
});
$("compare-help").addEventListener("click", () => openDialog("method-dialog"));
$("share-view").addEventListener("click", shareView);
$("copy-url").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText($("share-url").value);
    toast("탐색 설정 링크를 복사했습니다.");
  } catch {
    $("share-url").focus();
    $("share-url").select();
    toast("주소를 선택했습니다. 복사 단축키로 복사해 주세요.");
  }
});
$("download-csv").addEventListener("click", exportCSV);
$("tour-start").addEventListener("click", () => showTour(0));
$("tour-next").addEventListener("click", () =>
  state.tour === tourSteps.length - 1 ? endTour() : showTour(state.tour + 1),
);
$("tour-prev").addEventListener("click", () => showTour(state.tour - 1));
$("tour-close").addEventListener("click", endTour);
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopPlayback();
    stopLiveTimer();
  } else startLiveTimer();
});
let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(drawMap, 150);
});
function restoreURL() {
  const q = new URLSearchParams(location.search),
    at = q.get("at");
  if (at && state.overview.times.includes(at.length === 16 ? `${at}:00` : at))
    state.at = at.length === 16 ? `${at}:00` : at;
  const lat = Number(q.get("lat")),
    lon = Number(q.get("lon"));
  if (
    q.has("lat") &&
    q.has("lon") &&
    lat >= 37 &&
    lat <= 38 &&
    lon >= 126 &&
    lon <= 128
  ) {
    state.origin = { latitude: lat, longitude: lon };
    state.originName = "공유받은 출발 위치";
  }
  const radius = +q.get("radius");
  if ([500, 1000, 1500].includes(radius)) state.radius = radius;
  if (["B1", "M1"].includes(q.get("model"))) state.model = q.get("model");
  const id = +q.get("station");
  if (state.stationMap.has(id)) state.activeStation = id;
}
async function initialize() {
  try {
    const [overview, stations, cases, boundary] = await Promise.all([
      api("/api/overview"),
      api("/api/stations"),
      api("/api/cases"),
      api("/static/seoul_districts.geojson").catch(() => null),
    ]);
    Object.assign(state, { overview, stations, cases, boundary });
    state.stationMap = new Map(stations.map((s) => [s.station_id, s]));
    if (!overview.times.includes(state.at)) state.at = overview.times[0];
    try {
      const saved = JSON.parse(
        localStorage.getItem("seoul-bike-rider-favorites-v2") || "[]",
      );
      if (Array.isArray(saved))
        state.favorites = new Set(
          saved
            .filter((id) => Number.isInteger(id) && state.stationMap.has(id))
            .slice(0, 50),
        );
    } catch {}
    $("favorite-count").textContent = state.favorites.size;
    restoreURL();
    if (
      new URLSearchParams(location.search).get("mode") === "live" &&
      !window.DEMO_BUNDLE
    ) {
      state.mode = "live";
    }
    if (window.DEMO_BUNDLE) {
      $("live-mode").disabled = true;
      $("live-mode").title = "실시간 API는 Python 서버 데모에서 제공합니다.";
    }
    updateControls();
    renderResults();
    if (state.mode === "live") await loadLive();
    else await loadReplay();
  } catch (error) {
    alertMessage(`데모를 준비하지 못했습니다. ${error.message}`, true);
    $("station-count").textContent = "자료 준비 필요";
  }
}
initialize();
