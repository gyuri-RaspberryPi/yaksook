/* 약속 - 화면 스크립트
 * 서버 이벤트(SSE)를 받아 스테이션 화면을 실시간으로 바꾸고,
 * 보호자·처방·리포트 화면은 API를 불러 그립니다.
 * <body data-page="..."> 값에 따라 해당 화면의 init 함수만 실행됩니다.
 */

// ============================================================ 공통 도우미
async function api(method, url, body) {
  const opt = { method, headers: {} };
  if (body !== undefined) {
    opt.headers['Content-Type'] = 'application/json';
    opt.body = JSON.stringify(body);
  }
  const res = await fetch(url, opt);
  let data = null;
  try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
  return { ok: res.ok, status: res.status, data };
}

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

// 한국 시간 기준 오늘 날짜 'YYYY-MM-DD' (toISOString은 UTC라 아침에 하루 전이 나옴)
function localDate(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function koTime(hhmm) {
  const [h, m] = hhmm.split(':').map(Number);
  return `${h < 12 ? '오전' : '오후'} ${h % 12 || 12}시${m ? ` ${m}분` : ''}`;
}

const STATUS = {
  upcoming: '예정', due: '복용 시간', taken: '복용', takenNoWater: '확인 필요',
  outside: '외출 복용', missed: '미복용',
};
const LEVEL = { notice: '안내', warning: '경고', urgent: '긴급' };

// ============================================================ 스테이션 화면 (어르신용)
function initStation() {
  const name = document.body.dataset.name;
  let voiceOn = false;
  let rate = 0.85;
  let resetTimer = null;
  let nextText = '';

  api('GET', '/api/settings').then((r) => { if (r.ok) rate = r.data.voiceRate; });

  // 시계와 날짜
  const days = ['일', '월', '화', '수', '목', '금', '토'];
  function tick() {
    const d = new Date();
    const h = d.getHours();
    $('clock').textContent = `${h < 12 ? '오전' : '오후'} ${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')}`;
    $('today').textContent = `${d.getMonth() + 1}월 ${d.getDate()}일 ${days[d.getDay()]}요일`;
  }
  setInterval(tick, 1000); tick();

  // 음성 (한 번 눌러야 켜짐)
  function speak(text) {
    if (!voiceOn || !('speechSynthesis' in window)) return;
    speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text.replace(/[()]/g, ''));
    u.lang = 'ko-KR';
    u.rate = rate;
    speechSynthesis.speak(u);
  }
  $('voiceOn').onclick = () => {
    voiceOn = true;
    $('voiceGate').remove();
    $('chime').play().catch(() => {});
    speak('음성 안내를 시작합니다.');
  };

  function idle() {
    document.body.className = 'station';
    $('msg').textContent = nextText ? `${name}, 안녕하세요. ${nextText}` : `${name}, 안녕하세요.`;
  }

  // 오늘 시간대 상태
  async function loadToday() {
    const r = await api('GET', '/api/records/today');
    if (!r.ok) {
      $('slots').innerHTML = '';
      nextText = '';
      if (r.status === 404 && !resetTimer) $('msg').textContent = '처방을 먼저 등록해 주세요.';
      return;
    }
    $('slots').innerHTML = r.data.slots.map((s) => `
      <div class="st-slot">
        <div class="nm">${esc(s.slot)}</div>
        <div class="tm">${koTime(s.time)}</div>
        <div class="pill st-${s.status}">${STATUS[s.status]}</div>
      </div>`).join('');
    const next = r.data.slots.find((s) => s.status === 'upcoming' || s.status === 'due');
    nextText = next ? `다음 약은 ${koTime(next.time)} ${next.slot} 약이에요.` : '오늘 약은 모두 끝났어요.';
    if (!resetTimer) idle();
  }
  loadToday();
  setInterval(loadToday, 30000);

  // 서버에서 오는 안내 (SSE)
  const es = new EventSource('/events');
  es.onmessage = (e) => {
    const g = JSON.parse(e.data);
    $('msg').textContent = g.text;
    document.body.className = `station ${g.level === 'info' ? '' : g.level}`;
    if (voiceOn) {
      const sound = g.level === 'alert' ? $('alarm') : $('chime');
      sound.currentTime = 0;
      sound.play().catch(() => {});
      setTimeout(() => speak(g.text), 700);  // 알림음 뒤에 말하기
    }
    loadToday();
    clearTimeout(resetTimer);
    resetTimer = setTimeout(() => { resetTimer = null; idle(); }, 60000);  // 1분 뒤 기본 화면
  };
}

// ============================================================ 보호자 화면
function initGuardian() {
  async function loadToday() {
    const r = await api('GET', '/api/records/today');
    if (!r.ok) {
      $('todayList').innerHTML = r.status === 404
        ? '처방이 없어요. <a href="/register">처방 등록</a>을 먼저 해 주세요.' : '불러오지 못했어요.';
      $('todaySummary').textContent = '';
      return;
    }
    const d = r.data;
    $('todaySummary').textContent = `${d.summary.taken}/${d.summary.total} 복용`;
    if (!d.slots.length) { $('todayList').textContent = '오늘은 처방 기간이 아니에요.'; return; }
    $('todayList').innerHTML = d.slots.map((s) => {
      const rec = s.record;
      const detail = rec
        ? `${rec.takenAt} · ${rec.count}알 · ${rec.water ? '물 마심' : '물 기록 없음'}${s.opensAfter ? ` · 추가로 ${s.opensAfter}번 엶` : ''}`
        : `${s.pills}알 예정`;
      const del = rec ? `<button class="btn danger small" data-del="${rec.recordId}">기록 삭제</button>` : '';
      return `<div class="slot"><span class="name">${esc(s.slot)}</span><span class="time">${koTime(s.time)}</span>
        <span class="pill st-${s.status}">${STATUS[s.status]}</span><span class="detail">${esc(detail)}</span>${del}</div>`;
    }).join('');
  }

  async function loadAlerts() {
    const r = await api('GET', '/api/alerts?limit=30');
    if (!r.ok) return;
    const u = $('unread');
    u.hidden = r.data.unreadCount === 0;
    u.textContent = r.data.unreadCount;
    if (!r.data.alerts.length) { $('alertList').textContent = '아직 알림이 없어요.'; return; }
    $('alertList').innerHTML = r.data.alerts.map((a) => `
      <div class="alert-item ${a.read ? 'read' : ''}">
        <span class="pill lv-${a.level}">${LEVEL[a.level] || a.level}</span>
        <div class="msg">${esc(a.message)}<div class="when">${a.createdAt.replace('T', ' ').slice(5, 16)}</div></div>
        ${a.read ? '' : `<button class="btn ghost small" data-read="${a.alertId}">확인</button>`}
      </div>`).join('');
  }

  async function loadInventory() {
    const r = await api('GET', '/api/inventory');
    if (!r.ok) {
      $('inventory').textContent = r.status === 409 ? '약통 무게 보정 전이에요.' : '처방 등록 후 표시돼요.';
      return;
    }
    const v = r.data;
    $('inventory').innerHTML = `<div class="stat"><div class="num">${v.pillsLeft}알</div>
      <div class="lbl">${v.daysLeft ?? '-'}일분 남음</div></div>
      ${v.low ? '<p class="pill lv-warning" style="margin-top:8px">곧 떨어져요. 병원 예약이 필요해요.</p>' : ''}
      ${v.reliable ? '' : '<p class="muted">알약이 가벼워 개수가 틀릴 수 있어요.</p>'}`;
  }

  async function loadHealth() {
    const r = await api('GET', '/api/device/health');
    if (!r.ok) { $('health').textContent = '기기에 연결할 수 없어요. 전원과 와이파이를 확인하세요.'; return; }
    const names = { ultrasonic: '초음파 (사람)', gyro: '자이로 (뚜껑)', loadcell: '로드셀 (무게)', cup: '압력 (물컵)' };
    const sim = r.data.mode === 'simulation';
    $('health').innerHTML = (sim ? '<p class="muted">센서 없이 실행 중 (시연 모드)</p>' : '') +
      Object.entries(r.data.sensors).map(([k, v]) =>
        `<div class="sensor"><span class="dot ${sim ? '' : v.ok ? 'ok' : 'bad'}"></span>${names[k]}</div>`).join('');
  }

  async function loadActivity() {
    const r = await api('GET', '/api/activity');
    if (!r.ok) return;
    const max = Math.max(1, ...r.data.hourly);
    $('bars').innerHTML = r.data.hourly.map((c, h) =>
      `<div class="bar ${c ? '' : 'zero'}" style="height:${c ? Math.max(6, c / max * 100) : 2}%" title="${h}시 ${c}회"></div>`).join('');
    $('lastSeen').textContent = r.data.lastSeenAt ? `마지막 움직임 ${r.data.lastSeenAt.slice(11, 16)}` : '오늘 움직임 기록 없음';
  }

  function refresh() { loadToday(); loadAlerts(); loadInventory(); loadHealth(); loadActivity(); }
  refresh();
  setInterval(refresh, 10000);

  document.addEventListener('click', async (e) => {
    const t = e.target;
    if (t.dataset.read) { await api('PUT', `/api/alerts/${t.dataset.read}/read`); loadAlerts(); }
    if (t.dataset.del && confirm('이 복약 기록을 삭제할까요? 그 시간대는 다시 "안 먹음"이 됩니다.')) {
      await api('DELETE', `/api/records/${t.dataset.del}`); loadToday();
    }
    if (t.dataset.sim) {
      const r = await api('POST', `/api/sim/${t.dataset.sim}`);
      $('simLog').textContent = r.data ? `${r.data.text}${r.data.guardian ? `  →  보호자: ${r.data.guardian}` : ''}` : '오류';
      setTimeout(refresh, 300);
    }
  });
}

// ============================================================ 처방·설정 화면
function initRegister() {
  // ---------- 처방
  function slotRow(s = { slot: '', time: '', pills: 1 }) {
    const div = document.createElement('div');
    div.className = 'slot-row';
    div.innerHTML = `<input type="text" class="sName" placeholder="아침" maxlength="10" value="${esc(s.slot)}">
      <input type="time" class="sTime" value="${esc(s.time)}">
      <input type="number" class="sPills" min="1" max="10" value="${s.pills}">
      <button type="button" class="btn danger small">삭제</button>`;
    div.querySelector('button').onclick = () => div.remove();
    $('slotRows').appendChild(div);
  }
  $('addSlot').onclick = () => slotRow();
  $('prnOn').onchange = () => { $('prnBox').hidden = !$('prnOn').checked; };

  api('GET', '/api/schedule/1').then((r) => {
    if (r.ok) {
      const s = r.data;
      $('medicine').value = s.medicine; $('startDate').value = s.startDate;
      $('days').value = s.days; $('windowMin').value = s.windowMin;
      s.slots.forEach(slotRow);
      if (s.prn) {
        $('prnOn').checked = true; $('prnBox').hidden = false;
        $('prnName').value = s.prn.name; $('prnInterval').value = s.prn.minIntervalHours; $('prnMax').value = s.prn.maxPerDay;
      }
      $('scheduleUpdated').textContent = `${s.startDate} ~ ${s.endDate}`;
    } else {
      $('startDate').value = localDate();
      [{ slot: '아침', time: '08:00', pills: 1 }, { slot: '점심', time: '12:30', pills: 1 }, { slot: '저녁', time: '18:30', pills: 1 }].forEach(slotRow);
    }
  });

  $('scheduleForm').onsubmit = async (e) => {
    e.preventDefault();
    const body = {
      medicine: $('medicine').value, startDate: $('startDate').value,
      days: Number($('days').value), windowMin: Number($('windowMin').value),
      slots: [...document.querySelectorAll('.slot-row')].map((row) => ({
        slot: row.querySelector('.sName').value, time: row.querySelector('.sTime').value,
        pills: Number(row.querySelector('.sPills').value),
      })),
      prn: $('prnOn').checked ? {
        name: $('prnName').value, minIntervalHours: Number($('prnInterval').value), maxPerDay: Number($('prnMax').value),
      } : null,
    };
    const r = await api('PUT', '/api/schedule/1', body);
    const m = $('scheduleMsg');
    m.className = `formmsg ${r.ok ? 'ok' : 'bad'}`;
    m.textContent = r.ok ? `저장했어요. (${r.data.startDate} ~ ${r.data.endDate})` : r.data.message;
    if (r.ok) $('scheduleUpdated').textContent = `${r.data.startDate} ~ ${r.data.endDate}`;
  };

  // ---------- 로드셀 보정
  document.querySelectorAll('[data-step]').forEach((b) => {
    b.onclick = async () => {
      const step = b.dataset.step;
      const body = { step };
      if (step === 'scale') body.grams = Number($('calGrams').value);
      if (step === 'pill') body.count = Number($('calCount').value);
      b.disabled = true;
      const r = await api('POST', '/api/calibration', body);
      b.disabled = false;
      const m = $('calMsg');
      m.className = `formmsg ${r.ok ? 'ok' : 'bad'}`;
      if (!r.ok) { m.textContent = r.data.message; return; }
      const d = r.data;
      m.textContent = {
        tare: '영점을 잡았어요. 2단계로 가세요.',
        scale: `보정값 ${d.scale}. 3단계로 가세요.`,
        box: `빈 약통 ${d.boxG}g. 4단계로 가세요.`,
        pill: `1알 ${d.onePillG}g, 흔들림 ${d.noiseG}g. ${d.reliable ? '보정 완료!' : '알약이 가벼워 개수가 틀릴 수 있어요.'}`,
      }[step];
    };
  });

  // ---------- 설정
  const fields = ['callName', 'voiceRate', 'approachWaitSec', 'repeatOpenAlert', 'missedRemindMin',
    'missedGuardianMin', 'wakeCheckTime', 'inactiveHours'];
  api('GET', '/api/settings').then((r) => {
    if (!r.ok) return;
    fields.forEach((f) => { $(f).value = r.data[f]; });
    $('telegramEnabled').checked = r.data.telegramEnabled;
  });
  $('settingsForm').onsubmit = async (e) => {
    e.preventDefault();
    const body = {};
    fields.forEach((f) => {
      const el = $(f);
      body[f] = el.type === 'number' ? Number(el.value) : el.value;
    });
    body.telegramEnabled = $('telegramEnabled').checked;
    const r = await api('PUT', '/api/settings', body);
    const m = $('settingsMsg');
    m.className = `formmsg ${r.ok ? 'ok' : 'bad'}`;
    m.textContent = r.ok ? '저장했어요. 바로 적용돼요.' : r.data.message;
  };
}

// ============================================================ 주간 리포트 화면
function initReport() {
  const input = $('endDate');
  input.value = localDate();
  input.max = input.value;

  async function load() {
    const r = await api('GET', `/api/report/weekly?endDate=${input.value}`);
    if (!r.ok) {
      $('daily').textContent = r.status === 404 ? '처방을 먼저 등록해 주세요.' : r.data.message;
      return;
    }
    const d = r.data;
    $('range').textContent = `${d.from} ~ ${d.to}`;
    $('adherence').textContent = d.adherencePct === null ? '-' : `${d.adherencePct}%`;
    $('delay').textContent = `${d.avgDelayMin}분`;
    $('dup').textContent = `${d.duplicateAttempts}회`;
    $('over').textContent = `${d.overDoseAttempts}회`;
    $('nowater').textContent = `${d.noWaterCount}회`;
    $('reopen').textContent = `${d.repeatOpens}회`;
    $('daily').innerHTML = d.daily.map((x) => {
      const pct = x.planned ? Math.round(x.taken / x.planned * 100) : 0;
      return `<div class="slot"><span class="time">${x.date.slice(5)}</span>
        <div style="flex:1;background:var(--line);border-radius:6px;height:14px">
          <div style="width:${pct}%;background:var(--brand);height:14px;border-radius:6px"></div></div>
        <span class="muted" style="width:60px;text-align:right">${x.taken}/${x.planned}</span></div>`;
    }).join('');
    $('missed').innerHTML = Object.entries(d.missedBySlot).map(([slot, n]) =>
      `<div class="slot"><span class="name">${esc(slot)}</span><span class="pill ${n ? 'lv-urgent' : 'st-taken'}">${n}회</span></div>`).join('');
  }
  input.onchange = load;
  load();
}

// ============================================================ 시작
({ station: initStation, guardian: initGuardian, register: initRegister, report: initReport })[document.body.dataset.page]?.();
