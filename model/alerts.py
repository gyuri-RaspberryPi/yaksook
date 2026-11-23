"""보호자 알림과 안부 확인

- 알림 저장, 텔레그램 전송, 같은 알림 중복 방지 (dedupe_key)
- 약통 앞 움직임 기록 (초음파)
- 기상 확인, 장시간 무활동, 야간 배회 판정
- 센서 고장 알림
"""

from datetime import datetime, timedelta

import config
from .database import iso, parse_iso
from .schedule import at


class AlertCenter:
    def __init__(self, db, notifier, get_settings):
        self.db = db
        self.notifier = notifier
        self.get_settings = get_settings

    # ------------------------------------------------------------ 알림
    def raise_alert(self, type_, level, message, now: datetime, dedupe_key=None):
        """알림을 저장하고 보냄. 같은 dedupe_key가 이미 있으면 None"""
        rid, changed = self.db.execute(
            "INSERT OR IGNORE INTO alerts(created_at, level, type, message, dedupe_key) VALUES(?, ?, ?, ?, ?)",
            (iso(now), level, type_, message, dedupe_key))
        if not changed:
            return None
        self.notifier.send(level, message, enabled=self.get_settings().get("telegramEnabled", False))
        return self.get(rid)

    def get(self, alert_id):
        row = self.db.one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
        return self._to_dict(row) if row else None

    def list(self, limit=30):
        rows = self.db.query("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
        return [self._to_dict(r) for r in rows]

    def unread_count(self):
        return self.db.one("SELECT COUNT(*) AS n FROM alerts WHERE read_at IS NULL")["n"]

    def mark_read(self, alert_id, now: datetime):
        row = self.db.one("SELECT * FROM alerts WHERE id = ?", (alert_id,))
        if not row:
            return None
        if not row["read_at"]:  # 이미 확인했으면 처음 확인한 시각 유지
            self.db.execute("UPDATE alerts SET read_at = ? WHERE id = ?", (iso(now), alert_id))
        return self.get(alert_id)

    @staticmethod
    def _to_dict(r):
        return {"alertId": r["id"], "createdAt": r["created_at"], "level": r["level"], "type": r["type"],
                "message": r["message"], "read": r["read_at"] is not None, "readAt": r["read_at"]}

    # ------------------------------------------------------------ 움직임
    def record_presence(self, now: datetime) -> bool:
        """초음파 센서가 사람을 감지했을 때. 60초 안의 연속 감지는 1번으로 셈"""
        last = self.db.one("SELECT ts FROM activity ORDER BY id DESC LIMIT 1")
        if last and 0 <= (now - parse_iso(last["ts"])).total_seconds() < config.PRESENCE_GAP_SEC:
            return False
        self.db.execute("INSERT INTO activity(ts, date, hour) VALUES(?, ?, ?)",
                        (iso(now), now.strftime("%Y-%m-%d"), now.hour))
        return True

    def activity_times(self, day: str):
        return [parse_iso(r["ts"]) for r in
                self.db.query("SELECT ts FROM activity WHERE date = ? ORDER BY ts", (day,))]

    # ------------------------------------------------------------ 안부 확인 (1분마다)
    def check_wellness(self, now: datetime, started_at: datetime):
        s = self.get_settings()
        day = now.strftime("%Y-%m-%d")
        today = self.activity_times(day)
        start, end = at(now, s["dayStart"]), at(now, s["dayEnd"])
        watching_since_morning = started_at <= start

        # 1) 기상 확인: 아침 전부터 켜져 있던 날, 기준 시각까지 움직임이 한 번도 없음
        wake = at(now, s["wakeCheckTime"])
        if watching_since_morning and now >= wake and not any(t >= start for t in today):
            self.raise_alert("NO_ACTIVITY_MORNING", "warning",
                             f"오늘 {s['wakeCheckTime']}까지 약통 앞 움직임이 한 번도 없어요. 안부 전화를 해 주세요.",
                             now, f"wake:{day}")

        # 2) 낮 동안 오래 움직임 없음
        inactive = timedelta(hours=s["inactiveHours"])
        day_events = [t for t in today if t >= start]
        if day_events and start + inactive <= now <= end:
            last = max(day_events[-1], started_at)
            if now - last >= inactive:
                hours = int((now - last).total_seconds() // 3600)
                self.raise_alert("LONG_INACTIVITY", "urgent",
                                 f"{last.strftime('%H:%M')} 이후 {hours}시간 동안 움직임이 없어요. 바로 확인해 주세요.",
                                 now, f"inactive:{day}:{last.strftime('%H:%M')}")

        # 3) 밤중 배회
        night = s["night"]
        n_start, n_end = at(now, night["start"]), at(now, night["end"])
        count = sum(1 for t in today if n_start <= t < n_end)
        if count >= night["count"]:
            self.raise_alert("NIGHT_WANDERING", "notice",
                             f"밤중({night['start']}~{night['end']})에 약통 앞을 {count}번 오가셨어요. "
                             f"잠을 잘 못 주무시는지 살펴 주세요.", now, f"night:{day}")

    # ------------------------------------------------------------ 센서 고장
    def check_sensors(self, health: dict, now: datetime):
        """health = {"loadcell": {"ok": False, ...}, ...}. 고장 1번당 알림 1번"""
        names = {"ultrasonic": "초음파 센서", "gyro": "자이로 센서", "loadcell": "로드셀", "cup": "물컵 압력 센서"}
        for key, st in health.items():
            fault_key = f"sensor:{key}"
            if not st["ok"] and st["lastSignalAt"]:
                faults = self.db.get_kv("sensorFaults", {})
                if key not in faults:
                    faults[key] = iso(now)
                    self.db.set_kv("sensorFaults", faults)
                    self.raise_alert("SENSOR_FAULT", "warning",
                                     f"약통의 {names.get(key, key)} 신호가 끊겼어요. 점검이 필요해요.",
                                     now, f"{fault_key}:{faults[key]}")
            elif st["ok"]:
                faults = self.db.get_kv("sensorFaults", {})
                if key in faults:
                    del faults[key]
                    self.db.set_kv("sensorFaults", faults)
