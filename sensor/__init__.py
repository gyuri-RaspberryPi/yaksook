"""하드웨어 입력 (센서마다 파일 하나)

start_hardware()가 센서 4개를 읽는 스레드를 시작하고, 센서 값을 판정 이벤트로 바꿔 Detector에 넘깁니다.
센서 하나가 고장 나도 나머지는 계속 동작하고, Health가 센서별 마지막 신호 시각을 기록합니다.
"""

import threading
import time
from datetime import datetime

import config


class Health:
    """센서별 마지막 신호 시각 (GET /api/device/health)"""

    NAMES = ("ultrasonic", "gyro", "loadcell", "cup")

    def __init__(self):
        self.last = {n: None for n in self.NAMES}
        self.errors = {}

    def beat(self, name, now=None):
        self.last[name] = now or datetime.now()

    def status(self, now: datetime):
        out = {}
        for n in self.NAMES:
            t = self.last[n]
            ok = t is not None and (now - t).total_seconds() <= config.SENSOR_TIMEOUT_SEC
            out[n] = {"ok": ok, "lastSignalAt": t.strftime("%Y-%m-%dT%H:%M:%S") if t else None}
        return out


class Hardware:
    """실제 센서 묶음. 각 센서는 연결에 실패하면 None"""

    def __init__(self, detector, health, db):
        self.detector, self.health, self.db = detector, health, db
        self.ultra = self.gyro = self.scale = self.cup = None
        self.lock = threading.Lock()  # 로드셀은 보정 API와 센서 루프가 같이 쓰므로 잠금

        from .ultrasonic import NearDetector, Ultrasonic
        from .gyro import LidTracker, MPU6050
        from .loadcell import HX711, Scale
        from .cup import Cup

        self.near = NearDetector(config.NEAR_CM)
        self.lid = LidTracker(config.LID_OPEN_DEG, config.STILL_LIMIT_G, config.STILL_SEC)
        self._try("ultrasonic", lambda: setattr(self, "ultra", Ultrasonic(config.ULTRASONIC_TRIG, config.ULTRASONIC_ECHO)))
        self._try("gyro", lambda: setattr(self, "gyro", MPU6050(config.I2C_BUS, config.MPU_ADDR)))
        self._try("loadcell", lambda: setattr(self, "scale", Scale(HX711(config.HX711_DT, config.HX711_SCK),
                                                                     config.LOADCELL_SAMPLES)))
        self._try("cup", lambda: setattr(self, "cup", Cup(config.FSR_PIN, self._cup_placed)))
        if self.scale:
            self.scale.cal = db.get_kv("calibration", {})
        self.pills_before = None

    def _try(self, name, fn):
        try:
            fn()
            print(f"[센서] {name} 연결됨", flush=True)
        except Exception as e:  # 배선이 빠졌거나 라이브러리가 없음
            self.health.errors[name] = str(e)
            print(f"[센서] {name} 연결 실패: {e}", flush=True)

    def _cup_placed(self, held):
        self.health.beat("cup")
        self.detector.cup(datetime.now(), held)

    def read_pills(self):
        with self.lock:
            return self.scale.pills()

    def loop(self):
        last_idle_read = 0
        while True:
            now = datetime.now()
            try:
                if self.ultra:
                    cm = self.ultra.distance_cm()
                    self.health.beat("ultrasonic", now)
                    if self.near.update(cm):
                        self.detector.approach(now)
            except Exception as e:
                self.health.errors["ultrasonic"] = str(e)

            try:
                if self.gyro:
                    ev = self.lid.update(self.gyro.accel(), time.time())
                    self.health.beat("gyro", now)
                    if ev == "open":
                        self.detector.lid_open(now)
                    elif ev == "closed" and self.scale and self.scale.ready:
                        after = self.read_pills()
                        self.health.beat("loadcell", now)
                        if self.pills_before is not None:
                            self.detector.measured(now, self.pills_before - after, pills_left=after)
                        self.pills_before = after
            except Exception as e:
                self.health.errors["gyro"] = str(e)

            try:
                # 뚜껑이 닫혀 있는 동안 가끔 무게를 확인 (로드셀 상태 확인 + 기준 알 수 갱신)
                if self.scale and self.scale.ready and not self.lid.is_open \
                        and time.time() - last_idle_read >= config.LOADCELL_IDLE_READ_SEC:
                    last_idle_read = time.time()
                    self.pills_before = self.read_pills()
                    self.health.beat("loadcell", now)
            except Exception as e:
                self.health.errors["loadcell"] = str(e)

            if self.cup:
                self.health.beat("cup", now)  # 물컵은 이벤트 방식이라 연결돼 있으면 살아 있음

            time.sleep(config.SENSOR_POLL_SEC)


def start_hardware(detector, health, db):
    hw = Hardware(detector, health, db)
    threading.Thread(target=hw.loop, daemon=True, name="sensor-loop").start()
    return hw
