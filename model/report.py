"""조회용 계산: 오늘 복약 상태, 기록, 주간 리포트, 남은 약, 움직임"""

import math
from datetime import date, datetime, timedelta

import config
from .database import parse_iso
from .schedule import at


def record_dict(r):
    return {"recordId": r["id"], "date": r["date"], "slot": r["slot"], "takenAt": r["taken_at"][11:16],
            "count": r["count"], "water": bool(r["water"]), "source": r["source"]}


def slot_status(schedule, now: datetime, slot, record) -> str:
    """upcoming / due / taken / takenNoWater / outside / missed"""
    t = at(now, slot["time"])
    if record:
        if record["source"] == "outside":
            return "outside"
        if record["water"]:
            return "taken"
        taken = parse_iso(record["taken_at"])
        waited = (now - taken).total_seconds()
        return "taken" if waited < config.WATER_WINDOW_SEC else "takenNoWater"
    if now < t - schedule.window:
        return "upcoming"
    if now <= t + schedule.window:
        return "due"
    return "missed"


def today_status(db, schedule, now: datetime):
    day = now.strftime("%Y-%m-%d")
    records = {r["slot"]: r for r in db.query("SELECT * FROM records WHERE date = ?", (day,))}
    opens = {}
    for r in db.query("SELECT slot, COUNT(*) AS n FROM events WHERE date = ? AND type = 'open_after' "
                      "GROUP BY slot", (day,)):
        opens[r["slot"]] = r["n"]
    slots = []
    for s in schedule.slots if schedule.active_on(now.date()) else []:
        rec = records.get(s["slot"])
        slots.append({"slot": s["slot"], "time": s["time"], "pills": s["pills"],
                      "status": slot_status(schedule, now, s, rec),
                      "record": record_dict(rec) if rec else None,
                      "opensAfter": opens.get(s["slot"], 0)})
    taken = sum(1 for s in slots if s["record"])
    return {"date": day, "summary": {"taken": taken, "total": len(slots)}, "slots": slots}


def records_between(db, start: str, end: str):
    rows = db.query("SELECT * FROM records WHERE date BETWEEN ? AND ? ORDER BY date, taken_at", (start, end))
    return [record_dict(r) for r in rows]


def weekly(db, schedule, end_day: date, now: datetime):
    start_day = end_day - timedelta(days=6)
    s, e = start_day.isoformat(), end_day.isoformat()
    records = db.query("SELECT * FROM records WHERE date BETWEEN ? AND ?", (s, e))
    by_key = {(r["date"], r["slot"]): r for r in records}
    planned = taken = 0
    delays = []
    missed = {sl["slot"]: 0 for sl in schedule.slots}
    daily = []
    for i in range(7):
        d = start_day + timedelta(days=i)
        day_planned = day_taken = 0
        if schedule.active_on(d):
            base = datetime(d.year, d.month, d.day)
            for sl in schedule.slots:
                t = at(base, sl["time"])
                rec = by_key.get((d.isoformat(), sl["slot"]))
                if t - schedule.window > now:  # 아직 오지 않은 시간대는 세지 않음
                    continue
                day_planned += 1
                if rec:
                    day_taken += 1
                    delays.append(abs((parse_iso(rec["taken_at"]) - t).total_seconds()) / 60)
                elif t + schedule.window < now:
                    missed[sl["slot"]] += 1
        planned += day_planned
        taken += day_taken
        daily.append({"date": d.isoformat(), "planned": day_planned, "taken": day_taken})

    def count(type_):
        return db.one("SELECT COUNT(*) AS n FROM events WHERE date BETWEEN ? AND ? AND type = ?", (s, e, type_))["n"]

    return {
        "from": s, "to": e,
        "plannedCount": planned, "takenCount": taken,
        "adherencePct": round(taken / planned * 100) if planned else None,
        "avgDelayMin": round(sum(delays) / len(delays)) if delays else 0,
        "missedBySlot": missed,
        "noWaterCount": sum(1 for r in records if not r["water"] and r["source"] == "station"),
        "duplicateAttempts": count("duplicate"),
        "overDoseAttempts": count("over_dose"),
        "repeatOpens": count("open_after"),
        "daily": daily,
    }


def inventory(db, schedule):
    """None이면 아직 보정 전"""
    cal = db.get_kv("calibration", {})
    inv = db.get_kv("inventory")
    if not cal.get("onePillG") or inv is None:
        return None
    per_day = schedule.per_day if schedule else 0
    left = inv["pillsLeft"]
    days_left = math.floor(left / per_day) if per_day else None
    return {"pillsLeft": left, "perDay": per_day, "daysLeft": days_left,
            "low": days_left is not None and days_left <= config.LOW_INVENTORY_DAYS,
            "onePillG": cal["onePillG"], "reliable": cal.get("reliable", True),
            "measuredAt": inv["measuredAt"]}


def activity(db, day: str):
    rows = db.query("SELECT ts, hour FROM activity WHERE date = ? ORDER BY ts", (day,))
    hourly = [0] * 24
    for r in rows:
        hourly[r["hour"]] += 1
    return {"date": day, "lastSeenAt": rows[-1]["ts"] if rows else None, "total": len(rows), "hourly": hourly}
