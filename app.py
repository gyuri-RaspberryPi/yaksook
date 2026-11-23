"""약속 - Flask 서버 진입점

실행
  python3 app.py               센서 없이 실행 (화면과 판정은 가짜 센서 버튼으로 시험)
  python3 app.py --demo        시연용 처방과 보정값을 미리 넣고 실행
  python3 app.py --hardware    라즈베리파이에서 실제 센서 4개 연결

접속 (같은 와이파이)
  스테이션 화면  http://<라즈베리파이 IP>:5000/
  보호자 화면    http://<라즈베리파이 IP>:5000/guardian
  처방·설정      http://<라즈베리파이 IP>:5000/register
  주간 리포트    http://<라즈베리파이 IP>:5000/report
"""

import argparse
import json
import queue
import re
import threading
import time
from datetime import date, datetime, timedelta

from flask import Flask, Response, jsonify, render_template, request

import config
from model import report
from model import schedule as sched
from model.alerts import AlertCenter
from model.database import Database, iso
from model.detector import Detector
from model.notifier import Notifier
from sensor import Health

TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def err(status, code, message, field=None):
    return jsonify({"error": code, "message": message, "field": field}), status


class EventHub:
    """스테이션 화면들로 안내를 실시간 전송 (Server-Sent Events)"""

    def __init__(self):
        self.listeners = []
        self.lock = threading.Lock()

    def publish(self, data: dict):
        msg = json.dumps(data, ensure_ascii=False)
        with self.lock:
            for q in self.listeners:
                q.put(msg)

    def subscribe(self):
        q = queue.Queue()
        with self.lock:
            self.listeners.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.listeners:
                self.listeners.remove(q)


def create_app(db_path=None, hardware=False, demo=False, start_threads=True):
    app = Flask(__name__)
    app.json.ensure_ascii = False

    db = Database(db_path or config.DB_PATH)
    hub = EventHub()
    health = Health()
    notifier = Notifier(config.TELEGRAM_TOKEN, config.TELEGRAM_CHAT_ID)
    cache = {"schedule": None, "loaded": False}

    def get_settings():
        s = json.loads(json.dumps(config.DEFAULT_SETTINGS))
        s.update(db.get_kv("settings", {}))
        return s

    def get_schedule():
        if not cache["loaded"]:
            cache["schedule"], cache["loaded"] = sched.load(db), True
        return cache["schedule"]

    def reload_schedule():
        cache["loaded"] = False

    alerts = AlertCenter(db, notifier, get_settings)
    detector = Detector(db, alerts, hub.publish, get_schedule, get_settings, started_at=datetime.now())

    if demo:
        _seed_demo(db)

    hw = None
    if hardware:
        from sensor import start_hardware
        hw = start_hardware(detector, health, db)

    if start_threads:
        def ticker():
            while True:
                now = datetime.now()
                try:
                    detector.tick(now)
                    if hw:
                        alerts.check_sensors(health.status(now), now)
                except Exception as e:  # 한 번 실패해도 타이머는 계속
                    print("[tick 오류]", e, flush=True)
                time.sleep(1)
        threading.Thread(target=ticker, daemon=True, name="ticker").start()

    app.config.update(DB=db, DETECTOR=detector, HUB=hub, ALERTS=alerts)

    # ================================================================ 화면
    @app.get("/")
    def page_station():
        return render_template("index.html", name=get_settings()["callName"])

    @app.get("/guardian")
    def page_guardian():
        return render_template("guardian.html")

    @app.get("/register")
    def page_register():
        return render_template("register.html")

    @app.get("/report")
    def page_report():
        return render_template("report.html")

    # ================================================================ 처방
    @app.get("/api/schedule/<int:schedule_id>")
    def get_schedule_api(schedule_id):
        s = sched.load(db, schedule_id)
        if not s:
            return err(404, "SCHEDULE_NOT_FOUND", "아직 등록된 처방이 없습니다.")
        return jsonify(s.to_dict())

    @app.put("/api/schedule/<int:schedule_id>")
    def put_schedule(schedule_id):
        try:
            data = sched.validate(request.get_json(silent=True))
        except sched.ScheduleError as e:
            return err(400, e.code, e.message, e.field)
        created = sched.save(db, schedule_id, data, datetime.now())
        reload_schedule()
        return jsonify(sched.load(db, schedule_id).to_dict()), (201 if created else 200)

    # ================================================================ 복약 기록
    @app.get("/api/records/today")
    def records_today():
        s = get_schedule()
        if not s:
            return err(404, "SCHEDULE_NOT_FOUND", "처방이 등록되지 않았습니다.")
        return jsonify(report.today_status(db, s, datetime.now()))

    @app.get("/api/records")
    def records_range():
        f, t = request.args.get("from"), request.args.get("to")
        try:
            d1, d2 = date.fromisoformat(f), date.fromisoformat(t)
        except (TypeError, ValueError):
            return err(400, "INVALID_DATE", "from과 to는 YYYY-MM-DD 형식이어야 합니다.", "from" if not f else "to")
        if d1 > d2:
            return err(400, "INVALID_DATE", "from이 to보다 늦습니다.", "from")
        if (d2 - d1).days >= 31:
            return err(400, "RANGE_TOO_LONG", "기간은 최대 31일입니다.", "to")
        rows = report.records_between(db, f, t)
        return jsonify({"from": f, "to": t, "count": len(rows), "records": rows})

    @app.delete("/api/records/<int:record_id>")
    def delete_record(record_id):
        row = db.one("SELECT * FROM records WHERE id = ?", (record_id,))
        if not row:
            return err(404, "RECORD_NOT_FOUND", "없는 기록 번호입니다.")
        db.execute("DELETE FROM records WHERE id = ?", (record_id,))
        status = None
        s = get_schedule()
        now = datetime.now()
        if s and row["date"] == now.strftime("%Y-%m-%d"):
            slot = next((x for x in s.slots if x["slot"] == row["slot"]), None)
            if slot:
                status = report.slot_status(s, now, slot, None)
        return jsonify({"recordId": record_id, "deleted": True, "date": row["date"],
                        "slot": row["slot"], "slotStatus": status})

    # ================================================================ 약통
    @app.get("/api/inventory")
    def get_inventory():
        s = get_schedule()
        if not s:
            return err(404, "SCHEDULE_NOT_FOUND", "처방이 없어 하루 복용량을 계산할 수 없습니다.")
        inv = report.inventory(db, s)
        if not inv:
            return err(409, "NOT_CALIBRATED", "로드셀 보정을 먼저 해 주세요.")
        return jsonify(inv)

    @app.post("/api/calibration")
    def calibration():
        body = request.get_json(silent=True) or {}
        step = body.get("step")
        if step not in ("tare", "scale", "box", "pill"):
            return err(400, "INVALID_STEP", "step은 tare, scale, box, pill 중 하나입니다.", "step")
        if not hw or not hw.scale:
            return err(409, "HARDWARE_UNAVAILABLE", "로드셀이 연결되지 않았습니다. --hardware로 실행했는지 확인하세요.")
        cal = db.get_kv("calibration", {})
        order = {"scale": "offset", "box": "scale", "pill": "boxG"}
        if step in order and order[step] not in cal:
            return err(409, "STEP_ORDER", "앞 단계를 먼저 해 주세요.")
        with hw.lock:
            raws = [hw.scale.read() for _ in range(3)]
        raw = sorted(r[0] for r in raws)[1]
        spread = max(r[1] for r in raws)
        result = {"step": step}
        if step == "tare":
            cal = {"offset": raw}
            result.update(offset=raw, next="scale")
        elif step == "scale":
            g = body.get("grams")
            if not isinstance(g, (int, float)) or isinstance(g, bool) or not 10 <= g <= 1000:
                return err(400, "INVALID_STEP", "grams는 10~1000 사이 숫자여야 합니다.", "grams")
            k = (raw - cal["offset"]) / g
            if abs(k) < 1:
                return err(409, "BAD_READING", "무게 변화가 없습니다. 물건을 올렸는지 확인하세요.")
            cal["scale"] = k
            result.update(scale=round(k, 2), next="box")
        elif step == "box":
            box = (raw - cal["offset"]) / cal["scale"]
            if box <= 0:
                return err(409, "BAD_READING", "빈 약통 무게가 0g 이하입니다. 약통을 올렸는지 확인하세요.")
            cal["boxG"] = box
            result.update(boxG=round(box, 2), next="pill")
        else:
            n = body.get("count")
            if not isinstance(n, int) or isinstance(n, bool) or not 5 <= n <= 100:
                return err(400, "INVALID_STEP", "count는 5~100 사이 정수여야 합니다.", "count")
            total = (raw - cal["offset"]) / cal["scale"] - cal["boxG"]
            if total <= 0:
                return err(409, "BAD_READING", "약을 넣었는데 무게가 늘지 않았습니다.")
            one = total / n
            noise = abs(spread / cal["scale"]) + 0.05
            reliable = one >= config.RELIABLE_RATIO * noise
            cal.update(onePillG=one, noiseG=noise, reliable=reliable)
            db.set_kv("inventory", {"pillsLeft": n, "measuredAt": iso(datetime.now())})
            hw.pills_before = n
            result.update(onePillG=round(one, 3), noiseG=round(noise, 3), reliable=reliable, pillsLeft=n, next=None)
        db.set_kv("calibration", cal)
        hw.scale.cal = cal
        return jsonify(result)

    @app.get("/api/device/health")
    def device_health():
        now = datetime.now()
        sensors = health.status(now)
        return jsonify({"serverTime": iso(now), "mode": "hardware" if hw else "simulation",
                        "allOk": all(v["ok"] for v in sensors.values()), "sensors": sensors})

    # ================================================================ 알림
    @app.get("/api/alerts")
    def list_alerts():
        try:
            limit = int(request.args.get("limit", 30))
            assert 1 <= limit <= 100
        except (ValueError, AssertionError):
            return err(400, "INVALID_LIMIT", "limit는 1~100 사이 정수여야 합니다.", "limit")
        return jsonify({"unreadCount": alerts.unread_count(), "alerts": alerts.list(limit)})

    @app.put("/api/alerts/<int:alert_id>/read")
    def read_alert(alert_id):
        a = alerts.mark_read(alert_id, datetime.now())
        if not a:
            return err(404, "ALERT_NOT_FOUND", "없는 알림 번호입니다.")
        return jsonify({"alertId": alert_id, "read": True, "readAt": a["readAt"],
                        "unreadCount": alerts.unread_count()})

    @app.get("/api/activity")
    def get_activity():
        day = request.args.get("date") or date.today().isoformat()
        try:
            d = date.fromisoformat(day)
            assert d <= date.today()
        except (ValueError, AssertionError):
            return err(400, "INVALID_DATE", "날짜 형식이 아니거나 미래 날짜입니다.", "date")
        return jsonify(report.activity(db, day))

    # ================================================================ 리포트
    @app.get("/api/report/weekly")
    def weekly_report():
        s = get_schedule()
        if not s:
            return err(404, "SCHEDULE_NOT_FOUND", "처방이 등록되지 않았습니다.")
        try:
            end = date.fromisoformat(request.args.get("endDate") or date.today().isoformat())
            assert end <= date.today()
        except (ValueError, AssertionError):
            return err(400, "INVALID_DATE", "날짜 형식이 아니거나 미래 날짜입니다.", "endDate")
        return jsonify(report.weekly(db, s, end, datetime.now()))

    # ================================================================ 설정
    @app.get("/api/settings")
    def get_settings_api():
        return jsonify(get_settings())

    @app.put("/api/settings")
    def put_settings():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return err(400, "INVALID_SETTING", "요청 본문은 JSON 객체여야 합니다.")
        current = get_settings()
        for key in body:
            if key not in config.DEFAULT_SETTINGS:
                return err(400, "UNKNOWN_FIELD", f"없는 설정 이름입니다: {key}", key)
        new = {**current, **body}
        problem = _check_settings(new)
        if problem:
            return err(400, "INVALID_SETTING", problem[1], problem[0])
        if new["telegramEnabled"] and not notifier.configured:
            return err(409, "TELEGRAM_NOT_CONFIGURED", "라즈베리파이에 텔레그램 토큰이 설정되지 않았습니다.",
                       "telegramEnabled")
        saved = db.get_kv("settings", {})
        saved.update(body)
        db.set_kv("settings", saved)
        return jsonify(get_settings())

    # ================================================================ 실시간 안내
    @app.get("/events")
    def events():
        q = hub.subscribe()

        def stream():
            try:
                while True:
                    try:
                        yield f"data: {q.get(timeout=15)}\n\n"
                    except queue.Empty:
                        yield ": keep-alive\n\n"
            finally:
                hub.unsubscribe(q)

        return Response(stream(), mimetype="text/event-stream",
                        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ================================================================ 시연용 가짜 센서
    @app.post("/api/sim/<name>")
    def simulate(name):
        now = datetime.now()
        if name == "approach":
            return jsonify(detector.approach(now))
        if name == "open":
            return jsonify(detector.lid_open(now))
        if name == "measured":
            try:
                pills = int(request.args.get("pills", ""))
            except ValueError:
                return err(400, "INVALID_PILLS", "pills는 정수여야 합니다.", "pills")
            return jsonify(detector.measured(now, pills))
        if name == "cup":
            try:
                held = float(request.args.get("heldSec", 3))
                assert 0.5 <= held <= 60
            except (ValueError, AssertionError):
                return err(400, "INVALID_HELD_SEC", "heldSec는 0.5~60 사이 숫자여야 합니다.", "heldSec")
            return jsonify(detector.cup(now, held))
        return err(404, "UNKNOWN_SIM", "approach, open, measured, cup 중 하나입니다.")

    return app


def _check_settings(s):
    """(필드, 메시지) 또는 None"""
    def is_int(v, lo, hi):
        return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi

    if not (isinstance(s["callName"], str) and 1 <= len(s["callName"].strip()) <= 10):
        return "callName", "호칭은 1~10자여야 합니다."
    for key, lo, hi in [("approachWaitSec", 5, 60), ("approachCooldownMin", 1, 60), ("repeatOpenAlert", 1, 10),
                        ("missedRemindMin", 5, 60), ("missedGuardianMin", 10, 120), ("inactiveHours", 1, 12)]:
        if not is_int(s[key], lo, hi):
            return key, f"{key}는 {lo}~{hi} 사이 정수여야 합니다."
    if s["missedRemindMin"] >= s["missedGuardianMin"]:
        return "missedRemindMin", "다시 안내하는 시간은 보호자 알림 시간보다 짧아야 합니다."
    for key in ("dayStart", "dayEnd", "wakeCheckTime"):
        if not (isinstance(s[key], str) and TIME_RE.match(s[key])):
            return key, f"{key}는 HH:MM 형식이어야 합니다."
    if not s["dayStart"] < s["wakeCheckTime"] < s["dayEnd"]:
        return "wakeCheckTime", "기상 확인 시각은 dayStart와 dayEnd 사이여야 합니다."
    n = s["night"]
    if not (isinstance(n, dict) and TIME_RE.match(str(n.get("start", ""))) and TIME_RE.match(str(n.get("end", "")))
            and is_int(n.get("count"), 1, 10)):
        return "night", "night는 start·end(HH:MM)와 count(1~10)가 필요합니다."
    if not (isinstance(s["voiceRate"], (int, float)) and not isinstance(s["voiceRate"], bool)
            and 0.5 <= s["voiceRate"] <= 1.5):
        return "voiceRate", "voiceRate는 0.5~1.5 사이 숫자여야 합니다."
    if not isinstance(s["telegramEnabled"], bool):
        return "telegramEnabled", "telegramEnabled는 true 또는 false입니다."
    return None


def _demo_slots(now: datetime):
    """발표가 몇 시에 있든 시연되도록, 지금 시각을 복약 시간으로 잡고 앞뒤로 4시간 간격 시간대를 둠"""
    center = now.replace(second=0, microsecond=0) - timedelta(minutes=now.minute % 5)
    def name(h):
        return "아침" if h < 11 else "점심" if h < 16 else "저녁" if h < 21 else "밤"
    slots, used = [], set()
    for hours in (-4, 0, 4):
        t = center + timedelta(hours=hours)
        if t.date() != now.date():
            continue
        n = name(t.hour)
        n = n if n not in used else f"{n}2"
        used.add(n)
        slots.append({"slot": n, "time": t.strftime("%H:%M"), "pills": 2})
    return slots


def _seed_demo(db):
    """시연용: 지금이 복약 시간인 처방으로 덮어쓰고, 보정값이 없으면 가상 보정값과 남은 약 30알"""
    now = datetime.now()
    data = sched.validate({**config.DEMO_SCHEDULE, "startDate": date.today().isoformat(),
                           "slots": _demo_slots(now)})
    sched.save(db, 1, data, now)
    print("[demo] 시연용 처방:", [f"{s['slot']} {s['time']}" for s in data["slots"]], flush=True)
    if not db.get_kv("calibration"):
        db.set_kv("calibration", {"onePillG": 0.6, "noiseG": 0.15, "reliable": True, "simulated": True})
        db.set_kv("inventory", {"pillsLeft": 30, "measuredAt": iso(datetime.now())})
        print("[demo] 가상 보정값과 남은 약 30알을 넣었어요.")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="약속 서버")
    p.add_argument("--hardware", action="store_true", help="실제 센서 연결 (라즈베리파이)")
    p.add_argument("--demo", action="store_true", help="시연용 처방과 보정값을 미리 넣기")
    p.add_argument("--port", type=int, default=config.PORT)
    args = p.parse_args()
    application = create_app(hardware=args.hardware, demo=args.demo)
    print(f"약속 서버 시작: http://localhost:{args.port}  (보호자 화면 /guardian)", flush=True)
    application.run(host=config.HOST, port=args.port, threaded=True)
