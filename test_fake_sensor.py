"""센서 없이 판정·알림·API를 시험하는 테스트

실행: python3 test_fake_sensor.py
시각을 직접 넣어서 "점심 12:20에 다가옴 -> 뚜껑 열림 -> 2알 꺼냄 -> 물 마심" 같은 장면을 재현합니다.
코드를 고친 뒤에는 항상 이 테스트를 돌려서 기존 기능이 망가지지 않았는지 확인하세요.
"""

import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta

import config
from model import report
from model import schedule as sched
from model.alerts import AlertCenter
from model.database import Database
from model.detector import Detector
from model.notifier import Notifier
from sensor.gyro import LidTracker
from sensor.loadcell import median_and_noise, pills_from_grams
from sensor.ultrasonic import NearDetector

TODAY = date(2026, 11, 23)


def t(hhmm, sec=0):
    h, m = map(int, hhmm.split(":"))
    return datetime(TODAY.year, TODAY.month, TODAY.day, h, m, sec)


PLAN = {"medicine": "혈압약", "startDate": TODAY.isoformat(), "days": 30, "windowMin": 60,
        "slots": [{"slot": "아침", "time": "08:00", "pills": 2},
                  {"slot": "점심", "time": "12:30", "pills": 2},
                  {"slot": "저녁", "time": "18:30", "pills": 2}], "prn": None}


class Base(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.said = []
        settings = dict(config.DEFAULT_SETTINGS, callName="할머니")
        self.alerts = AlertCenter(self.db, Notifier(), lambda: settings)
        sched.save(self.db, 1, sched.validate(PLAN), t("07:00"))
        self.schedule = sched.load(self.db)
        self.d = Detector(self.db, self.alerts, self.said.append, lambda: self.schedule,
                          lambda: settings, started_at=t("06:00"))

    def alert_types(self):
        return [a["type"] for a in self.alerts.list(100)]


class ScheduleTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(len(sched.validate(PLAN)["slots"]), 3)

    def test_errors(self):
        bad_time = json.loads(json.dumps(PLAN)); bad_time["slots"][1]["time"] = "25:00"
        with self.assertRaises(sched.ScheduleError) as c:
            sched.validate(bad_time)
        self.assertEqual(c.exception.field, "slots[1].time")
        dup = json.loads(json.dumps(PLAN)); dup["slots"][1]["slot"] = "아침"
        with self.assertRaises(sched.ScheduleError) as c:
            sched.validate(dup)
        self.assertEqual(c.exception.code, "DUPLICATE_SLOT")
        close = json.loads(json.dumps(PLAN)); close["slots"][1]["time"] = "08:30"
        with self.assertRaises(sched.ScheduleError) as c:
            sched.validate(close)
        self.assertEqual(c.exception.code, "SLOT_WINDOW_OVERLAP")


class DoseTest(Base):
    def test_normal_flow_with_water(self):
        r = self.d.approach(t("12:20"))
        self.assertEqual(r["type"], "approachPending")
        self.d.lid_open(t("12:20", 5))                    # 15초 안에 열었으니 다가옴 안내는 취소
        self.assertEqual(self.said[-1]["type"], "doseTime")
        self.d.tick(t("12:20", 30))
        self.assertNotIn("approach", [s["type"] for s in self.said])
        r = self.d.measured(t("12:21"), 2)
        self.assertEqual(r["type"], "ok")
        self.d.cup(t("12:22"), 3)
        st = report.today_status(self.db, self.schedule, t("12:25"))
        lunch = st["slots"][1]
        self.assertEqual(lunch["status"], "taken")
        self.assertTrue(lunch["record"]["water"])

    def test_approach_without_opening(self):
        self.d.approach(t("12:20"))
        self.d.tick(t("12:20", 10))
        self.assertEqual(self.said, [])
        self.d.tick(t("12:20", 15))
        self.assertEqual(self.said[-1]["type"], "approach")

    def test_too_few_then_more(self):
        self.assertEqual(self.d.measured(t("08:05"), 1)["type"], "tooFew")
        self.assertEqual(self.d.measured(t("08:06"), 1)["type"], "ok")      # 합쳐서 2알
        self.assertNotIn("DUPLICATE_DOSE", self.alert_types())

    def test_over_dose(self):
        r = self.d.measured(t("08:05"), 4)
        self.assertEqual(r["type"], "tooMany")
        self.assertIn("OVER_DOSE", self.alert_types())

    def test_duplicate_is_urgent(self):
        self.d.measured(t("12:21"), 2)
        r = self.d.measured(t("12:50"), 2)
        self.assertEqual(r["type"], "duplicate")
        self.assertEqual(self.alerts.list(1)[0]["level"], "urgent")

    def test_repeat_open_reassures_and_alerts_third_time(self):
        self.d.measured(t("12:21"), 2)
        r = self.d.lid_open(t("12:40"))
        self.assertIn("오후 12시 21분에 이미 드셨어요", r["text"])
        self.assertIn("오후 6시 30분 저녁 약", r["text"])
        self.d.lid_open(t("12:45"))
        r = self.d.lid_open(t("12:50"))
        self.assertIn("3번 더", r["guardian"])

    def test_off_schedule(self):
        r = self.d.lid_open(t("15:00"))
        self.assertEqual(r["type"], "offSchedule")
        r = self.d.measured(t("15:01"), 2)
        self.assertEqual(r["type"], "offScheduleTaken")
        self.assertIn("OFF_SCHEDULE_DOSE", self.alert_types())

    def test_no_water(self):
        self.d.measured(t("12:21"), 2)
        self.d.tick(t("12:23", 1))                        # 2분 넘게 물을 안 마심
        self.assertEqual(self.said[-1]["type"], "noWater")
        self.assertIn("NO_WATER", self.alert_types())
        st = report.today_status(self.db, self.schedule, t("12:30"))
        self.assertEqual(st["slots"][1]["status"], "takenNoWater")

    def test_missed_stages(self):
        self.d.tick(t("18:30"))
        self.assertEqual(self.said[-1]["type"], "doseReminder")
        self.d.tick(t("18:45"))
        self.assertEqual(self.said[-1]["level"], "warn")
        self.assertNotIn("MISSED_DOSE", self.alert_types())
        self.d.tick(t("19:00"))
        self.assertIn("MISSED_DOSE", self.alert_types())
        n = len(self.said)
        self.d.tick(t("19:01"))
        self.assertEqual(len(self.said), n)               # 같은 안내를 반복하지 않음

    def test_inventory_and_low_alert(self):
        self.db.set_kv("calibration", {"onePillG": 0.6, "reliable": True})
        self.db.set_kv("inventory", {"pillsLeft": 20, "measuredAt": "2026-11-23T07:00:00"})
        self.d.measured(t("08:05"), 2)
        inv = report.inventory(self.db, self.schedule)
        self.assertEqual(inv["pillsLeft"], 18)
        self.assertEqual(inv["daysLeft"], 3)
        self.assertIn("LOW_INVENTORY", self.alert_types())
        self.assertEqual(self.d.measured(t("15:00"), -10)["type"], "refill")
        self.assertEqual(report.inventory(self.db, self.schedule)["pillsLeft"], 28)

    def test_weekly(self):
        self.d.measured(t("08:05"), 2)
        self.d.measured(t("08:30"), 2)                    # 중복 시도
        w = report.weekly(self.db, self.schedule, TODAY, t("20:00"))
        self.assertEqual(w["plannedCount"], 3)
        self.assertEqual(w["takenCount"], 1)
        self.assertEqual(w["missedBySlot"], {"아침": 0, "점심": 1, "저녁": 1})
        self.assertEqual(w["duplicateAttempts"], 1)


class WellnessTest(Base):
    def test_wake_check(self):
        self.alerts.check_wellness(t("10:00"), t("06:00"))
        self.assertIn("NO_ACTIVITY_MORNING", self.alert_types())

    def test_no_wake_alert_if_started_late(self):
        self.alerts.check_wellness(t("17:00"), t("16:00"))
        self.assertEqual(self.alert_types(), [])

    def test_long_inactivity_and_night(self):
        self.alerts.record_presence(t("09:00"))
        self.alerts.check_wellness(t("12:59"), t("06:00"))
        self.assertNotIn("LONG_INACTIVITY", self.alert_types())
        self.alerts.check_wellness(t("13:00"), t("06:00"))
        self.assertIn("LONG_INACTIVITY", self.alert_types())
        for hhmm in ("01:00", "02:10", "03:30"):
            self.alerts.record_presence(t(hhmm))
        self.alerts.check_wellness(t("13:01"), t("00:00"))
        self.assertIn("NIGHT_WANDERING", self.alert_types())

    def test_presence_debounce(self):
        self.assertTrue(self.alerts.record_presence(t("08:00")))
        self.assertFalse(self.alerts.record_presence(t("08:00", 30)))
        self.assertTrue(self.alerts.record_presence(t("08:02")))


class SensorLogicTest(unittest.TestCase):
    def test_near(self):
        n = NearDetector(50)
        self.assertEqual([n.update(c) for c in (120, 40, 35, 80, 30)], [False, True, False, False, True])

    def test_lid(self):
        lid = LidTracker(30, 0.05, 2)
        closed, opened = (0, 0, 1), (0, 0.87, 0.5)        # 약 60도 기울어짐
        self.assertIsNone(lid.update(closed, 0))
        self.assertEqual(lid.update(opened, 1), "open")
        self.assertIsNone(lid.update(closed, 2))          # 닫혔지만 흔들림
        self.assertIsNone(lid.update(closed, 3))
        self.assertEqual(lid.update(closed, 5.1), "closed")

    def test_pills(self):
        self.assertEqual(pills_from_grams(38.4 + 0.6 * 9 + 0.1, 38.4, 0.6), 9)
        self.assertEqual(median_and_noise([5, 1, 3]), (3, 4))


class ApiTest(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.tmp = tempfile.mkdtemp()
        self.app = create_app(os.path.join(self.tmp, "t.db"), start_threads=False)
        self.c = self.app.test_client()

    def test_schedule_crud(self):
        self.assertEqual(self.c.get("/api/schedule/1").status_code, 404)
        r = self.c.put("/api/schedule/1", json=PLAN)
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.get_json()["endDate"], (TODAY + timedelta(days=29)).isoformat())
        self.assertEqual(self.c.put("/api/schedule/1", json=PLAN).status_code, 200)
        bad = self.c.put("/api/schedule/1", json={**PLAN, "days": 0}).get_json()
        self.assertEqual((bad["error"], bad["field"]), ("INVALID_SCHEDULE", "days"))

    def test_records_and_alerts(self):
        today = date.today().isoformat()
        self.c.put("/api/schedule/1", json={**PLAN, "startDate": today})
        self.assertEqual(self.c.get("/api/records/today").status_code, 200)
        self.assertEqual(self.c.get("/api/records?from=2026-11-01&to=2026-12-15").get_json()["error"], "RANGE_TOO_LONG")
        self.assertEqual(self.c.get("/api/records?from=x&to=y").status_code, 400)
        self.assertEqual(self.c.delete("/api/records/999").status_code, 404)
        self.c.post("/api/sim/measured?pills=2")
        r = self.c.post("/api/sim/measured?pills=2").get_json()  # 지금 시각과 상관없이 경고가 하나 생김
        alerts = self.c.get("/api/alerts").get_json()
        self.assertGreaterEqual(alerts["unreadCount"], 1)
        aid = alerts["alerts"][0]["alertId"]
        self.assertTrue(self.c.put(f"/api/alerts/{aid}/read").get_json()["read"])
        self.assertEqual(self.c.put("/api/alerts/999/read").status_code, 404)
        self.assertEqual(self.c.get("/api/alerts?limit=0").status_code, 400)

    def test_misc(self):
        self.assertEqual(self.c.get("/api/inventory").status_code, 404)
        self.c.put("/api/schedule/1", json=PLAN)
        self.assertEqual(self.c.get("/api/inventory").get_json()["error"], "NOT_CALIBRATED")
        self.assertEqual(self.c.post("/api/calibration", json={"step": "tare"}).status_code, 409)
        self.assertEqual(self.c.post("/api/calibration", json={"step": "x"}).status_code, 400)
        self.assertEqual(self.c.get("/api/device/health").get_json()["mode"], "simulation")
        self.assertEqual(len(self.c.get("/api/activity").get_json()["hourly"]), 24)
        self.assertEqual(self.c.get("/api/activity?date=2999-01-01").status_code, 400)
        self.assertEqual(self.c.get("/api/report/weekly").status_code, 200)
        self.assertEqual(self.c.put("/api/settings", json={"callName": "어머니"}).get_json()["callName"], "어머니")
        self.assertEqual(self.c.put("/api/settings", json={"nope": 1}).get_json()["error"], "UNKNOWN_FIELD")
        self.assertEqual(self.c.put("/api/settings", json={"inactiveHours": 99}).get_json()["field"], "inactiveHours")
        self.assertEqual(self.c.put("/api/settings", json={"telegramEnabled": True}).status_code, 409)
        for path in ("/", "/guardian", "/register", "/report"):
            self.assertEqual(self.c.get(path).status_code, 200, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
