"""처방 -> 복약 일정

- 처방 입력값 검사 (PUT /api/schedule)
- 지금 먹을 시간대, 다음 복용 시각 계산
"""

import json
import re
from datetime import date, datetime, timedelta

from .database import iso

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ScheduleError(Exception):
    def __init__(self, code, message, field=None):
        super().__init__(message)
        self.code, self.message, self.field = code, message, field


def at(day: datetime, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return day.replace(hour=h, minute=m, second=0, microsecond=0)


def korean_time(t: datetime) -> str:
    """15:30 -> '오후 3시 30분' (어르신이 듣기 쉬운 말)"""
    ampm = "오전" if t.hour < 12 else "오후"
    h = t.hour % 12 or 12
    return f"{ampm} {h}시" + (f" {t.minute}분" if t.minute else "")


class Schedule:
    def __init__(self, schedule_id, medicine, start_date, days, window_min, slots, prn, updated_at):
        self.id = schedule_id
        self.medicine = medicine
        self.start_date = start_date            # 'YYYY-MM-DD'
        self.days = days
        self.window_min = window_min
        self.slots = sorted(slots, key=lambda s: s["time"])
        self.prn = prn
        self.updated_at = updated_at

    @property
    def window(self):
        return timedelta(minutes=self.window_min)

    @property
    def start(self) -> date:
        return date.fromisoformat(self.start_date)

    @property
    def end(self) -> date:
        return self.start + timedelta(days=self.days - 1)

    @property
    def per_day(self) -> int:
        return sum(s["pills"] for s in self.slots)

    def active_on(self, d: date) -> bool:
        return self.start <= d <= self.end

    def current_slot(self, now: datetime):
        """지금 먹을 수 있는 시간대 (복용 시각 ±window). 없으면 None"""
        if not self.active_on(now.date()):
            return None
        for s in self.slots:
            t = at(now, s["time"])
            if t - self.window <= now <= t + self.window:
                return s
        return None

    def next_slot(self, now: datetime):
        """지금 이후 가장 가까운 (시간대, 시각)"""
        for s in self.slots:
            t = at(now, s["time"])
            if t > now:
                return s, t
        first = self.slots[0]
        return first, at(now + timedelta(days=1), first["time"])

    def to_dict(self):
        return {
            "scheduleId": self.id,
            "medicine": self.medicine,
            "startDate": self.start_date,
            "days": self.days,
            "endDate": self.end.isoformat(),
            "windowMin": self.window_min,
            "slots": self.slots,
            "prn": self.prn,
            "updatedAt": self.updated_at,
        }


def validate(payload) -> dict:
    """PUT /api/schedule 요청 본문 검사. 문제가 있으면 ScheduleError"""
    if not isinstance(payload, dict):
        raise ScheduleError("INVALID_SCHEDULE", "요청 본문은 JSON 객체여야 합니다.")

    def need(cond, msg, field):
        if not cond:
            raise ScheduleError("INVALID_SCHEDULE", msg, field)

    med = payload.get("medicine")
    need(isinstance(med, str) and 1 <= len(med.strip()) <= 30, "약 이름은 1~30자여야 합니다.", "medicine")
    start = payload.get("startDate")
    need(isinstance(start, str) and DATE_RE.match(start), "시작일은 YYYY-MM-DD 형식이어야 합니다.", "startDate")
    try:
        date.fromisoformat(start)
    except ValueError:
        raise ScheduleError("INVALID_SCHEDULE", "없는 날짜입니다.", "startDate")
    days = payload.get("days")
    need(isinstance(days, int) and not isinstance(days, bool) and 1 <= days <= 180,
         "처방 일수는 1~180 사이 정수여야 합니다.", "days")
    window = payload.get("windowMin", 60)
    need(isinstance(window, int) and not isinstance(window, bool) and 15 <= window <= 120,
         "windowMin은 15~120 사이 정수여야 합니다.", "windowMin")

    slots = payload.get("slots")
    need(isinstance(slots, list) and 1 <= len(slots) <= 6, "시간대는 1~6개여야 합니다.", "slots")
    clean = []
    for i, s in enumerate(slots):
        need(isinstance(s, dict), "시간대 형식이 잘못됐습니다.", f"slots[{i}]")
        name, t, pills = s.get("slot"), s.get("time"), s.get("pills")
        need(isinstance(name, str) and 1 <= len(name.strip()) <= 10, "시간대 이름은 1~10자여야 합니다.", f"slots[{i}].slot")
        need(isinstance(t, str) and TIME_RE.match(t), "시각은 HH:MM 형식이어야 합니다.", f"slots[{i}].time")
        need(isinstance(pills, int) and not isinstance(pills, bool) and 1 <= pills <= 10,
             "1회 알 수는 1~10 사이 정수여야 합니다.", f"slots[{i}].pills")
        clean.append({"slot": name.strip(), "time": t, "pills": pills})

    names = [s["slot"] for s in clean]
    times = [s["time"] for s in clean]
    if len(set(names)) != len(names) or len(set(times)) != len(times):
        raise ScheduleError("DUPLICATE_SLOT", "시간대 이름이나 시각이 겹칩니다.", "slots")
    ordered = sorted(clean, key=lambda s: s["time"])
    base = datetime(2000, 1, 1)
    for a, b in zip(ordered, ordered[1:]):
        gap = (at(base, b["time"]) - at(base, a["time"])).total_seconds() / 60
        if gap < 2 * window:
            raise ScheduleError("SLOT_WINDOW_OVERLAP",
                                f"{a['slot']}과 {b['slot']}의 허용 시간이 겹칩니다. "
                                f"시각을 {2 * window}분 이상 떨어뜨리거나 windowMin을 줄이세요.", "slots")

    prn = payload.get("prn")
    if prn is not None:
        need(isinstance(prn, dict), "prn은 객체이거나 null이어야 합니다.", "prn")
        pname, interval, maxd = prn.get("name"), prn.get("minIntervalHours"), prn.get("maxPerDay")
        need(isinstance(pname, str) and 1 <= len(pname.strip()) <= 30, "필요시 약 이름이 필요합니다.", "prn.name")
        need(isinstance(interval, (int, float)) and not isinstance(interval, bool) and 0.5 <= interval <= 24,
             "최소 간격은 0.5~24시간이어야 합니다.", "prn.minIntervalHours")
        need(isinstance(maxd, int) and not isinstance(maxd, bool) and 1 <= maxd <= 20,
             "하루 최대 개수는 1~20이어야 합니다.", "prn.maxPerDay")
        prn = {"name": pname.strip(), "minIntervalHours": interval, "maxPerDay": maxd}

    return {"medicine": med.strip(), "startDate": start, "days": days,
            "windowMin": window, "slots": ordered, "prn": prn}


def load(db, schedule_id=None):
    """schedule_id가 없으면 가장 최근에 수정한 처방"""
    if schedule_id is None:
        row = db.one("SELECT * FROM schedule ORDER BY updated_at DESC, id DESC LIMIT 1")
    else:
        row = db.one("SELECT * FROM schedule WHERE id = ?", (schedule_id,))
    if not row:
        return None
    return Schedule(row["id"], row["medicine"], row["start_date"], row["days"], row["window_min"],
                    json.loads(row["slots"]), json.loads(row["prn"]) if row["prn"] else None,
                    row["updated_at"])


def save(db, schedule_id, data, now: datetime) -> bool:
    """저장. 새로 만들었으면 True"""
    exists = db.one("SELECT id FROM schedule WHERE id = ?", (schedule_id,))
    values = (data["medicine"], data["startDate"], data["days"], data["windowMin"],
              json.dumps(data["slots"], ensure_ascii=False),
              json.dumps(data["prn"], ensure_ascii=False) if data["prn"] else None, iso(now))
    if exists:
        db.execute("UPDATE schedule SET medicine=?, start_date=?, days=?, window_min=?, slots=?, prn=?, "
                   "updated_at=? WHERE id=?", values + (schedule_id,))
        return False
    db.execute("INSERT INTO schedule(medicine, start_date, days, window_min, slots, prn, updated_at, id) "
               "VALUES(?, ?, ?, ?, ?, ?, ?, ?)", values + (schedule_id,))
    return True
