"""복용 판정 (약속의 핵심)

센서가 만든 이벤트를 받아서 스테이션 안내와 보호자 알림을 만듭니다.
센서 값이 아니라 이벤트만 받으므로, 가짜 센서(/api/sim/...)로도 똑같이 시험할 수 있습니다.

이벤트                       센서
  approach(now)              초음파: 약통 앞에 사람이 옴
  lid_open(now)              자이로: 뚜껑이 열림
  measured(now, pills)       자이로+로드셀: 뚜껑이 닫히고 멈춘 뒤 꺼낸 알 수 (음수면 보충)
  cup(now, held_sec)         압력: 물컵을 들었다 놓음
  tick(now)                  1초마다: 다가옴 15초 대기, 물 마심 확인, 미복용 단계 알림, 안부 확인
"""

import functools
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta

import config
from .database import iso, parse_iso
from .schedule import at, korean_time


@dataclass
class Guidance:
    text: str
    level: str = "info"      # 스테이션 화면 색: info 초록 / warn 노랑 / alert 빨강
    type: str = "info"


QUIET = {"text": "(안내 없음)", "level": "info", "type": "none", "guardian": ""}


def locked(fn):
    """센서 스레드, 1초 타이머, 웹 요청이 동시에 부르므로 한 번에 하나씩만 실행"""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self.lock:
            return fn(self, *args, **kwargs)
    return wrapper


class Detector:
    def __init__(self, db, alerts, publish, get_schedule, get_settings, started_at: datetime):
        self.db = db
        self.alerts = alerts
        self.publish = publish            # 스테이션 화면으로 보내는 함수 (SSE)
        self.get_schedule = get_schedule
        self.get_settings = get_settings
        self.started_at = started_at
        self.lock = threading.RLock()

        self.pending_approach = None      # 다가온 시각 (15초 기다리는 중)
        self.last_approach_msg = None
        self.last_take = None             # {"recordId", "at", "done"} 물 마심 확인용
        self.stages_done = set()          # 미복용 단계 알림을 이미 보낸 (날짜, 시간대, 단계)
        self.last_wellness_minute = None

    # ============================================================ 공통 도우미
    def _say(self, g: Guidance, now, guardian=""):
        self.publish({"text": g.text, "level": g.level, "type": g.type, "at": iso(now)})
        return {"text": g.text, "level": g.level, "type": g.type, "guardian": guardian}

    def _guardian(self, type_, level, message, now, key=None):
        alert = self.alerts.raise_alert(type_, level, message, now, key)
        return message if alert else ""

    def _name(self):
        return self.get_settings()["callName"]

    def _record(self, day, slot):
        return self.db.one("SELECT * FROM records WHERE date = ? AND slot = ?", (day, slot))

    def _event(self, now, type_, slot=None, value=None):
        self.db.execute("INSERT INTO events(ts, date, type, slot, value) VALUES(?, ?, ?, ?, ?)",
                        (iso(now), now.strftime("%Y-%m-%d"), type_, slot, value))

    def _count_events(self, now, type_, slot=None):
        sql = "SELECT COUNT(*) AS n FROM events WHERE date = ? AND type = ?"
        params = [now.strftime("%Y-%m-%d"), type_]
        if slot is not None:
            sql += " AND slot = ?"
            params.append(slot)
        return self.db.one(sql, params)["n"]

    def _next_text(self, now, schedule):
        slot, t = schedule.next_slot(now)
        day = "내일 " if t.date() > now.date() else ""
        return f"다음 약은 {day}{korean_time(t)} {slot['slot']} 약이에요."

    # ============================================================ 초음파: 다가옴
    @locked
    def approach(self, now: datetime):
        self.alerts.record_presence(now)
        s = self.get_schedule()
        if not s:
            return QUIET
        slot = s.current_slot(now)
        if not slot or self._record(now.strftime("%Y-%m-%d"), slot["slot"]):
            return QUIET  # 약 먹을 때가 아니거나 이미 먹었으면 조용히 (움직임만 기록)
        cooldown = timedelta(minutes=self.get_settings()["approachCooldownMin"])
        if self.pending_approach or (self.last_approach_msg and now - self.last_approach_msg < cooldown):
            return QUIET
        self.pending_approach = now  # 바로 말하지 않고, 뚜껑을 안 열면 tick()에서 안내
        wait = self.get_settings()["approachWaitSec"]
        return {"text": f"({wait}초 안에 뚜껑을 열지 않으면 안내합니다)", "level": "info",
                "type": "approachPending", "guardian": ""}

    # ============================================================ 자이로: 뚜껑 열림
    @locked
    def lid_open(self, now: datetime):
        self.pending_approach = None  # 먹으러 온 것이므로 다가옴 안내는 취소
        s = self.get_schedule()
        if not s:
            return self._say(Guidance("처방이 아직 등록되지 않았어요. 보호자에게 알려 주세요.", "warn", "noSchedule"), now)
        day = now.strftime("%Y-%m-%d")
        slot = s.current_slot(now)
        name = self._name()

        if slot:
            rec = self._record(day, slot["slot"])
            if not rec or rec["count"] < slot["pills"]:
                need = slot["pills"] - (rec["count"] if rec else 0)
                return self._say(Guidance(
                    f"{slot['slot']} 약 {need}알을 꺼내 주세요. 다 꺼내면 뚜껑을 닫고 물을 드세요.",
                    "info", "doseTime"), now)

            # 이미 먹은 시간대에 또 열었음: 매번 같은 말로 안심시키기
            self._event(now, "open_after", slot["slot"])
            n = self._count_events(now, "open_after", slot["slot"])
            taken_at = korean_time(parse_iso(rec["taken_at"]))
            guardian = ""
            if n == self.get_settings()["repeatOpenAlert"]:
                guardian = self._guardian(
                    "REPEAT_OPEN", "notice",
                    f"{slot['slot']} 약을 드신 뒤 약통을 {n}번 더 여셨어요. 드신 걸 기억하지 못하실 수 있어요. "
                    f"전화로 안심시켜 드리세요.", now, f"repeat:{day}:{slot['slot']}")
            return self._say(Guidance(
                f"{name}, {slot['slot']} 약은 {taken_at}에 이미 드셨어요. 뚜껑을 닫아 주세요. {self._next_text(now, s)}",
                "warn", "alreadyTaken"), now, guardian)

        # 약 먹을 시간이 아님
        self._event(now, "off_open")
        n = self._count_events(now, "off_open")
        guardian = ""
        if n == self.get_settings()["repeatOpenAlert"]:
            guardian = self._guardian("REPEAT_OPEN", "notice",
                                      f"오늘 복약 시간이 아닐 때 약통을 {n}번 여셨어요.", now, f"offopen:{day}")
        return self._say(Guidance(
            f"{name}, 지금은 약 드실 시간이 아니에요. 뚜껑을 닫아 주세요. {self._next_text(now, s)}",
            "warn", "offSchedule"), now, guardian)

    # ============================================================ 로드셀: 꺼낸 알 수
    @locked
    def measured(self, now: datetime, pills: int, pills_left=None):
        guardian_low = self._update_inventory(now, pills, pills_left)
        if pills < 0:
            return self._say(Guidance(f"약 {-pills}알을 채우셨어요.", "info", "refill"), now, guardian_low)
        if pills == 0:
            return {**QUIET, "text": "(꺼낸 약 없음)"}

        s = self.get_schedule()
        if not s:
            return self._say(Guidance("처방이 아직 등록되지 않았어요. 꺼낸 약은 다시 넣어 주세요.", "warn", "noSchedule"), now)
        day = now.strftime("%Y-%m-%d")
        slot = s.current_slot(now)
        name = self._name()

        if slot:
            need = slot["pills"]
            rec = self._record(day, slot["slot"])
            if rec and rec["count"] >= need:
                # 이미 다 먹었는데 또 꺼냄 = 중복 복용 위험
                self._event(now, "duplicate", slot["slot"], pills)
                guardian = self._guardian(
                    "DUPLICATE_DOSE", "urgent",
                    f"중복 복용 위험: {slot['slot']} 약을 {rec['taken_at'][11:16]}에 드신 뒤 {pills}알을 또 꺼내셨어요. "
                    f"바로 확인해 주세요.", now)
                return self._say(Guidance(
                    f"{name}, {slot['slot']} 약은 이미 드셨어요. 방금 꺼낸 약은 드시지 말고 약통에 다시 넣어 주세요.",
                    "alert", "duplicate"), now, guardian)

            if rec:  # 덜 꺼냈다가 더 꺼낸 경우: 합쳐서 판단
                total = rec["count"] + pills
                self.db.execute("UPDATE records SET count = ?, taken_at = ? WHERE id = ?",
                                (total, iso(now), rec["id"]))
                record_id = rec["id"]
            else:
                total = pills
                record_id, _ = self.db.execute(
                    "INSERT INTO records(date, slot, taken_at, count, water, source, created_at) "
                    "VALUES(?, ?, ?, ?, 0, 'station', ?)", (day, slot["slot"], iso(now), pills, iso(now)))
            self.last_take = {"recordId": record_id, "at": now, "done": False}

            if total == need:
                return self._say(Guidance("잘하셨어요. 물과 함께 꼭 드세요.", "info", "ok"), now, guardian_low)
            if total < need:
                return self._say(Guidance(
                    f"{need - total}알 더 꺼내 주세요. {slot['slot']} 약은 {need}알이에요.", "warn", "tooFew"), now)
            self._event(now, "over_dose", slot["slot"], total - need)
            guardian = self._guardian("OVER_DOSE", "warning",
                                      f"{slot['slot']} 약을 처방보다 {total - need}알 많이 꺼내셨어요 ({total}알).", now)
            return self._say(Guidance(
                f"{need}알만 드셔야 해요. 더 꺼낸 {total - need}알은 약통에 다시 넣어 주세요.",
                "alert", "tooMany"), now, guardian)

        # 약 먹을 시간이 아닌데 꺼냄
        self._event(now, "off_taken", None, pills)
        guardian = self._guardian("OFF_SCHEDULE_DOSE", "warning",
                                  f"복약 시간이 아닌 {korean_time(now)}에 약 {pills}알을 꺼내셨어요.", now)
        return self._say(Guidance("지금은 약 드실 시간이 아니에요. 꺼낸 약은 약통에 다시 넣어 주세요.",
                                  "alert", "offScheduleTaken"), now, guardian)

    def _update_inventory(self, now, pills, pills_left):
        """남은 알 수 갱신. 3일분 이하면 하루 한 번 보호자 알림"""
        inv = self.db.get_kv("inventory")
        if pills_left is not None:
            left = max(0, int(pills_left))
        elif inv is not None:
            left = max(0, inv["pillsLeft"] - pills)
        else:
            return ""  # 보정 전이라 남은 양을 모름
        self.db.set_kv("inventory", {"pillsLeft": left, "measuredAt": iso(now)})
        s = self.get_schedule()
        if s and s.per_day and pills > 0:
            days = left // s.per_day
            if days <= config.LOW_INVENTORY_DAYS:
                return self._guardian("LOW_INVENTORY", "notice",
                                      f"남은 약이 {left}알({days}일분)이에요. 병원 예약이 필요해요.",
                                      now, f"low:{now.strftime('%Y-%m-%d')}")
        return ""

    # ============================================================ 압력: 물컵
    @locked
    def cup(self, now: datetime, held_sec: float):
        self._event(now, "water", None, held_sec)
        lt = self.last_take
        if lt and not lt["done"] and (now - lt["at"]).total_seconds() <= config.WATER_WINDOW_SEC:
            self.db.execute("UPDATE records SET water = 1 WHERE id = ?", (lt["recordId"],))
            lt["done"] = True
            return self._say(Guidance("물과 함께 잘 드셨어요.", "info", "water"), now)
        return {**QUIET, "text": "(물 마심만 기록)"}

    # ============================================================ 1초마다
    @locked
    def tick(self, now: datetime):
        s = self.get_schedule()
        settings = self.get_settings()

        # 1) 다가온 뒤 15초 동안 뚜껑을 안 열었으면 안내
        if self.pending_approach and (now - self.pending_approach).total_seconds() >= settings["approachWaitSec"]:
            self.pending_approach = None
            slot = s.current_slot(now) if s else None
            if slot and not self._record(now.strftime("%Y-%m-%d"), slot["slot"]):
                self.last_approach_msg = now
                self._say(Guidance(
                    f"{self._name()}, {slot['slot']} 약 드실 시간이에요. 약통을 열고 {slot['pills']}알 꺼내 드세요.",
                    "info", "approach"), now)

        # 2) 약을 꺼내고 2분 동안 물을 안 마셨으면 안내
        lt = self.last_take
        if lt and not lt["done"] and (now - lt["at"]).total_seconds() > config.WATER_WINDOW_SEC:
            lt["done"] = True
            rec = self.db.one("SELECT * FROM records WHERE id = ?", (lt["recordId"],))
            if rec and not rec["water"]:
                guardian = self._guardian("NO_WATER", "notice",
                                          f"{rec['slot']} 약은 꺼내셨지만 물을 마신 기록이 없어요. 확인 전화를 해 보세요.",
                                          now, f"nowater:{rec['id']}")
                self._say(Guidance("약을 드실 때 물도 꼭 함께 드세요.", "warn", "noWater"), now, guardian)

        # 3) 미복용 단계 알림
        if s:
            self._check_missed(now, s, settings)

        # 4) 안부 확인 (1분에 한 번)
        minute = now.replace(second=0, microsecond=0)
        if minute != self.last_wellness_minute:
            self.last_wellness_minute = minute
            self.alerts.check_wellness(now, self.started_at)

    def _check_missed(self, now, s, settings):
        """정시 안내 -> 15분 뒤 다시 안내 -> 30분 뒤 보호자 알림"""
        if not s.active_on(now.date()):
            return
        day = now.strftime("%Y-%m-%d")
        for slot in s.slots:
            t = at(now, slot["time"])
            if now < t or now > t + s.window:
                continue
            if self._record(day, slot["slot"]):
                continue
            stages = [(t, 0), (t + timedelta(minutes=settings["missedRemindMin"]), 1),
                      (t + timedelta(minutes=settings["missedGuardianMin"]), 2)]
            due = [i for st, i in stages if now >= st]
            if not due:
                continue
            top = max(due)
            fresh = (day, slot["slot"], top) not in self.stages_done
            for i in due:  # 늦게 켜진 경우 지난 단계는 건너뛰고 가장 최근 단계만 실행
                self.stages_done.add((day, slot["slot"], i))
            if not fresh:
                continue
            text = f"{self._name()}, {slot['slot']} 약 드실 시간이에요. 약통을 열고 {slot['pills']}알 꺼내 드세요."
            if top == 0:
                self._say(Guidance(text, "info", "doseReminder"), now)
            elif top == 1:
                self._say(Guidance(text, "warn", "doseReminder"), now)
            else:
                guardian = self._guardian(
                    "MISSED_DOSE", "warning",
                    f"{slot['slot']} 약({slot['time']})을 {settings['missedGuardianMin']}분이 지나도록 아직 안 드셨어요.",
                    now, f"missed:{day}:{slot['slot']}")
                self._say(Guidance(text, "warn", "doseReminder"), now, guardian)
