// PC 그림판: 세션 발급 → QR 표시 → /ws/board로 기능(action)을 받아 캔버스에 실행.
// 메시지 형식은 docs/contracts/README.md ②⑦ 참고.

const COLORS = ['#212529', '#e03131', '#1971c2', '#2f9e44', '#f08c00', '#9c36b5'];
const SIZES = [2, 4, 8, 14, 22];
const ERASER = { color: '#fff', size: 30 };  // 지우개는 배경색으로 덮는 획 (undo로 되돌릴 수 있다)

const $ = (id) => document.getElementById(id);
const canvas = $('board');
const ctx = canvas.getContext('2d');
const stage = $('stage');
const cursor = $('cursor');

// ---------------------------------------------------------------- 그림 상태
// 좌표는 0~1로 저장하고 그릴 때 캔버스 크기에 맞춘다 (창 크기가 바뀌어도 그대로).
const pen = { color: 0, size: 1 };
let strokes = [];     // { color, size, erase, points: [[x, y], ...] }
let current = null;   // 펜이 내려가 있으면 그리는 중인 획

function toPx(x, y) {
  return [x * canvas.clientWidth, y * canvas.clientHeight];
}

function drawStroke(s, from = 0) {
  ctx.strokeStyle = ctx.fillStyle = s.color;
  ctx.lineWidth = s.size;
  ctx.lineCap = ctx.lineJoin = 'round';
  if (s.points.length === 1) {
    const [px, py] = toPx(...s.points[0]);
    ctx.beginPath();
    ctx.arc(px, py, s.size / 2, 0, Math.PI * 2);
    ctx.fill();
    return;
  }
  ctx.beginPath();
  ctx.moveTo(...toPx(...s.points[Math.max(0, from - 1)]));
  for (let i = Math.max(1, from); i < s.points.length; i++) ctx.lineTo(...toPx(...s.points[i]));
  ctx.stroke();
}

function redraw() {
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, canvas.clientWidth, canvas.clientHeight);
  strokes.forEach((s) => drawStroke(s));
}

function resize() {
  const dpr = window.devicePixelRatio || 1;
  canvas.width = Math.round(canvas.clientWidth * dpr);
  canvas.height = Math.round(canvas.clientHeight * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  redraw();
}
new ResizeObserver(resize).observe(stage);

function penDown(x, y, erase = false) {
  if (current && current.erase !== erase) current = null;  // 펜 ↔ 지우개가 바뀌면 새 획
  if (!current) {
    const style = erase ? ERASER : { color: COLORS[pen.color], size: SIZES[pen.size] };
    current = { ...style, erase, points: [[x, y]] };
    strokes.push(current);
    drawStroke(current);
  } else {
    current.points.push([x, y]);
    drawStroke(current, current.points.length - 1);
  }
}

function penUp() {
  current = null;
}

function showCursor(x, y, drawing, erasing = false) {
  const [px, py] = toPx(x, y);
  const d = erasing ? ERASER.size : Math.max(10, SIZES[pen.size]);
  const color = erasing ? '#868e96' : COLORS[pen.color];
  Object.assign(cursor.style, {
    left: `${px}px`, top: `${py}px`, width: `${d}px`, height: `${d}px`,
    color, borderColor: color,
  });
  cursor.classList.toggle('drawing', drawing);
  cursor.classList.toggle('erasing', erasing);
  cursor.hidden = false;
}

function updateToolbar() {
  $('pen-color').style.background = COLORS[pen.color];
  $('pen-size').textContent = SIZES[pen.size];
}

function savePng() {
  const stamp = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, '');
  const a = document.createElement('a');
  a.href = canvas.toDataURL('image/png');
  a.download = `drawing-${stamp}.png`;
  a.click();
}

// ---------------------------------------------------------------- 손 좌표 표시
// gesture가 프레임마다 보내는 action "landmarks"(params.points 21개, params.zone)를 그림판 위에 겹쳐 그린다.
// 커서와 같게 zone을 캔버스 전체로 늘리므로 검지 끝이 커서 위치와 겹친다.
// 손을 검지 끝 기준으로 줄여 그리면 검지만 움직여도 손바닥이 따라 들썩여서, 실제 크기로 그린다.
// 표시 전용이라 그림·디버그 표시에는 영향을 주지 않는다.
const handCanvas = $('hand');
const hctx = handCanvas.getContext('2d');
const HAND_LINKS = [
  [0, 1], [1, 2], [2, 3], [3, 4],          // 엄지
  [0, 5], [5, 6], [6, 7], [7, 8],          // 검지
  [5, 9], [9, 10], [10, 11], [11, 12],     // 중지
  [9, 13], [13, 14], [14, 15], [15, 16],   // 약지
  [13, 17], [0, 17], [17, 18], [18, 19], [19, 20],  // 새끼·손바닥
];
const HAND_TIP = 8;  // 검지 끝 (그리는 점)
let handTimer = null;

// 떨림 보정: 점마다 One Euro 필터 (https://gery.casiez.net/1euro/).
// 느리게 움직일 때는 강하게, 빠르게 움직일 때는 약하게 보정해 떨림은 줄이고 따라오는 속도는 지킨다.
// minCutoff를 낮추면 더 부드럽고(멈췄을 때 덜 떨림), beta를 높이면 빠른 움직임에 더 빨리 따라간다.
const HAND_FILTER = { minCutoff: 0.5, beta: 10, dCutoff: 1.0 };
const HAND_RESET_S = 0.5;  // 손이 이만큼 끊겼다 다시 보이면 보정을 처음부터 (이전 위치에서 미끄러져 오지 않게)
let handFilters = null;    // 점 21개 × [x 필터, y 필터]
let handLastT = 0;

function oneEuro() {
  let prev = null, dPrev = 0, tPrev = 0;
  const alpha = (cutoff, dt) => 1 / (1 + 1 / (2 * Math.PI * cutoff * dt));
  return (x, t) => {
    if (prev === null) { prev = x; tPrev = t; return x; }
    const dt = Math.max(t - tPrev, 1e-3);
    dPrev += alpha(HAND_FILTER.dCutoff, dt) * ((x - prev) / dt - dPrev);
    prev += alpha(HAND_FILTER.minCutoff + HAND_FILTER.beta * Math.abs(dPrev), dt) * (x - prev);
    tPrev = t;
    return prev;
  };
}

function smoothHand(points, t) {
  if (!handFilters || t - handLastT > HAND_RESET_S) handFilters = points.map(() => [oneEuro(), oneEuro()]);
  handLastT = t;
  return points.map(([x, y], i) => [handFilters[i][0](x, t), handFilters[i][1](y, t)]);
}

// 거리 안내: 손바닥 길이(손목 0 → 중지 뿌리 9, 카메라 세로 대비)로 휴대폰과 손 사이 거리를 본다.
// 손가락을 접어도 길이가 그대로라 주먹일 때도 같은 기준이다. 기준값은 추정치라 표시줄의 "손 크기"를 보며 고친다.
const FRAME = { w: 320, h: 240 };             // stream이 보내는 프레임 크기 (점의 x는 가로, y는 세로 기준 0~1)
const HAND_SIZE = { near: 0.25, far: 0.08 };  // near보다 크면 너무 가까움, far보다 작으면 너무 멂
// gesture의 at_edge와 같은 기준: 판별에 쓰는 점(손목, 손가락 마디·끝)이 테두리 2% 안이면
// gesture가 그 프레임을 판별하지 않으므로 같은 때 안내를 띄운다
const HAND_EDGE = { points: [0, 6, 8, 10, 12, 14, 16, 18, 20], margin: 0.02 };
const GUIDE_HOLD = 5;                         // 같은 상태가 이만큼(프레임) 이어져야 안내를 바꾼다 (경계에서 깜빡이지 않게)
const GUIDE_TEXT = {
  near: '조금 뒤로 가 주세요',
  far: '조금 가까이 와 주세요',
  edge: '손을 화면 안쪽으로 옮겨 주세요',
};
const guide = { state: null, candidate: null, count: 0 };

function handGuide(raw, points) {
  const size = Math.hypot((points[9][0] - points[0][0]) * FRAME.w, (points[9][1] - points[0][1]) * FRAME.h) / FRAME.h;
  $('dbg-hand').textContent = size.toFixed(2);
  // gesture처럼 보정 전 좌표로 본다
  const { points: edgePoints, margin } = HAND_EDGE;
  const edge = edgePoints.some((i) => raw[i].slice(0, 2).some((v) => v < margin || v > 1 - margin));
  const state = size > HAND_SIZE.near ? 'near' : edge ? 'edge' : size < HAND_SIZE.far ? 'far' : null;
  if (state === guide.state) { guide.count = 0; return; }
  guide.count = state === guide.candidate ? guide.count + 1 : 1;
  guide.candidate = state;
  if (guide.count >= GUIDE_HOLD) setGuide(state);
}

function setGuide(state) {
  Object.assign(guide, { state, candidate: null, count: 0 });
  $('guide').hidden = !state;
  $('guide').textContent = state ? GUIDE_TEXT[state] : '';
}

function hideHand() {
  handCanvas.hidden = true;
  handFilters = null;
  setGuide(null);
  $('dbg-hand').textContent = '-';
}

function drawHand(params, ts) {
  const raw = params?.points ?? [];
  clearTimeout(handTimer);
  if (raw.length < 21) { hideHand(); return; }
  handTimer = setTimeout(hideHand, 1000);  // 메시지가 끊기면 지운다
  handCanvas.hidden = false;
  // 시간은 stream이 프레임을 받은 시각(ts)을 쓴다. 네트워크 도착 간격이 흔들려도 보정이 일정하다
  const points = smoothHand(raw, (ts ?? Date.now()) / 1000);
  handGuide(raw, points);

  const w = handCanvas.clientWidth, h = handCanvas.clientHeight;
  const dpr = window.devicePixelRatio || 1;
  if (handCanvas.width !== Math.round(w * dpr) || handCanvas.height !== Math.round(h * dpr)) {
    handCanvas.width = Math.round(w * dpr);
    handCanvas.height = Math.round(h * dpr);
    hctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  hctx.clearRect(0, 0, w, h);

  const [lo, hi] = params.zone ?? [0, 1];
  const px = ([x, y]) => [(x - lo) / (hi - lo) * w, (y - lo) / (hi - lo) * h];
  hctx.globalAlpha = guide.state === 'edge' ? 0.35 : 1;  // 가장자리에 걸린 손은 판별하지 않고 점도 찌그러지므로 흐리게
  hctx.strokeStyle = 'rgba(47, 158, 68, .55)';
  hctx.lineWidth = 2.5;
  hctx.lineCap = 'round';
  hctx.beginPath();
  for (const [a, b] of HAND_LINKS) {
    hctx.moveTo(...px(points[a]));
    hctx.lineTo(...px(points[b]));
  }
  hctx.stroke();
  points.forEach((p, i) => {
    const [x, y] = px(p);
    hctx.fillStyle = i === HAND_TIP ? '#f76707' : 'rgba(33, 37, 41, .6)';
    hctx.beginPath();
    hctx.arc(x, y, i === HAND_TIP ? 5 : 3, 0, Math.PI * 2);
    hctx.fill();
  });
}

// ---------------------------------------------------------------- 기능(action) 실행
// contracts의 action 표와 1:1. 표에 없는 action은 무시한다.
const ACTIONS = {
  draw: (m) => { penDown(m.x, m.y); showCursor(m.x, m.y, true); },
  erase: (m) => { penDown(m.x, m.y, true); showCursor(m.x, m.y, false, true); },
  hover: (m) => { penUp(); showCursor(m.x, m.y, false); },
  up: () => { penUp(); cursor.hidden = true; },
  clear: () => { penUp(); strokes = []; redraw(); },
  undo: () => { penUp(); strokes.pop(); redraw(); },
  next_color: () => { pen.color = (pen.color + 1) % COLORS.length; updateToolbar(); },
  size_up: () => { pen.size = Math.min(pen.size + 1, SIZES.length - 1); updateToolbar(); },
  size_down: () => { pen.size = Math.max(pen.size - 1, 0); updateToolbar(); },
  save: () => savePng(),
};

function onAction(m) {
  if (m.action === 'landmarks') { drawHand(m.params, m.ts); return; }
  $('dbg-gesture').textContent = m.gesture ?? '-';
  $('dbg-conf').textContent = m.confidence != null ? m.confidence.toFixed(2) : '-';
  $('dbg-action').textContent = m.action;
  if (m.ts != null) $('dbg-latency').textContent = Date.now() - m.ts;
  closePair();  // connected 이벤트를 놓쳤어도 명령이 오면 그림판을 보인다
  ACTIONS[m.action]?.(m);
}

function onEvent(m) {
  if (m.event === 'connected') {
    setPill('phone-state', true, '휴대폰 연결됨');
    closePair();
  } else if (m.event === 'disconnected') {
    setPill('phone-state', false, '휴대폰 끊김');
    penUp();
    cursor.hidden = true;
    hideHand();
  }
}

// ---------------------------------------------------------------- 세션 · WebSocket
let sid = null;
let ws = null;
let expireTimer = null;

function setPill(id, on, text) {
  const el = $(id);
  el.classList.toggle('on', on);
  el.classList.toggle('off', !on);
  el.textContent = text;
}

function randomId() {
  if (crypto.randomUUID) return crypto.randomUUID();
  // http://<IP>처럼 보안 컨텍스트가 아니면 randomUUID가 없다
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40;
  b[8] = (b[8] & 0x3f) | 0x80;
  const h = [...b].map((v) => v.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function connect() {
  if (ws) { ws.onclose = null; ws.close(); }
  const mySid = sid;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws/board?sid=${encodeURIComponent(mySid)}`);
  setPill('ws-state', false, '서버 연결 중');
  ws.onopen = () => setPill('ws-state', true, '서버 연결됨');
  ws.onmessage = (e) => {
    const m = JSON.parse(e.data);
    if (m.type === 'action') onAction(m);
    else if (m.type === 'event') onEvent(m);
  };
  ws.onclose = () => {
    setPill('ws-state', false, '서버 끊김 · 재연결 중');
    setTimeout(() => { if (sid === mySid) connect(); }, 1000);
  };
}

function openPair() { $('pair').hidden = false; }
function closePair() { $('pair').hidden = true; }

function showQr(pairUrl, expiresIn) {
  $('pair-qr').src = `/qr.svg?data=${encodeURIComponent(pairUrl)}`;
  $('pair-qr').hidden = false;
  let left = expiresIn;
  const tick = () => {
    $('pair-msg').textContent = left > 0 ? `${left}초 안에 스캔하세요` : 'QR이 만료되었습니다. 새 QR을 받으세요.';
    if (left-- <= 0) clearInterval(expireTimer);
  };
  tick();
  expireTimer = setInterval(tick, 1000);
}

async function startSession() {
  clearInterval(expireTimer);
  $('pair-qr').hidden = true;
  setPill('phone-state', false, '휴대폰 대기');

  // ?sid=… 로 열면 세션 발급 없이 그 sid를 쓴다 (다른 파트와 연동 테스트용)
  const fixed = new URLSearchParams(location.search).get('sid');
  if (fixed) {
    sid = fixed;
    $('pair-sid').textContent = sid;
    $('pair-msg').textContent = '주소에 지정한 세션을 사용합니다.';
    closePair();
    connect();
    return;
  }

  try {
    const res = await fetch('/api/session', { method: 'POST' });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const s = await res.json();
    sid = s.sid;
    showQr(s.pair_url, s.expires_in);
  } catch (err) {
    // security(세션 API)가 없을 때: web만 띄워 개발하는 경우
    sid = randomId();
    $('pair-msg').textContent = `세션 API에 연결하지 못해 개발용 세션을 만들었습니다 (${err.message}).`;
  }
  $('pair-sid').textContent = sid;
  openPair();
  connect();
}

$('btn-pair').onclick = openPair;
$('btn-close').onclick = closePair;
$('btn-renew').onclick = startSession;

updateToolbar();
startSession();
