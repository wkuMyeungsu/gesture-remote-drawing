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
let handTimer = null;

function drawHand(params) {
  const points = params?.points ?? [];
  clearTimeout(handTimer);
  if (points.length < 21) { handCanvas.hidden = true; return; }
  handTimer = setTimeout(() => { handCanvas.hidden = true; }, 1000);  // 메시지가 끊기면 지운다
  handCanvas.hidden = false;

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
  hctx.strokeStyle = 'rgba(47, 158, 68, .55)';
  hctx.lineWidth = 4;
  hctx.lineCap = 'round';
  hctx.beginPath();
  for (const [a, b] of HAND_LINKS) {
    hctx.moveTo(...px(points[a]));
    hctx.lineTo(...px(points[b]));
  }
  hctx.stroke();
  points.forEach((p, i) => {
    const [x, y] = px(p);
    hctx.fillStyle = i === 8 ? '#f76707' : 'rgba(33, 37, 41, .6)';  // 8: 검지 끝 (그리는 점)
    hctx.beginPath();
    hctx.arc(x, y, i === 8 ? 8 : 5, 0, Math.PI * 2);
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
  if (m.action === 'landmarks') { drawHand(m.params); return; }
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
    handCanvas.hidden = true;
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
