"""자이로 센서 MPU-6050 - 약통 뚜껑 열림·닫힘, 흔들림

연결: VCC -> 3.3V, GND -> GND, SDA -> GPIO2, SCL -> GPIO3
준비: sudo raspi-config -> Interface Options -> I2C -> Enable
      i2cdetect -y 1 에서 68 이 보이면 연결 성공
센서는 뚜껑 안쪽에 붙이고, 뚜껑을 닫은 상태에서 프로그램을 시작하세요 (닫힌 각도를 기준으로 잡음).
"""

import math


class MPU6050:
    def __init__(self, bus=1, addr=0x68):
        from smbus2 import SMBus  # 라즈베리파이에서만 필요
        self.addr = addr
        self.bus = SMBus(bus)
        self.bus.write_byte_data(addr, 0x6B, 0)  # 절전 모드 해제

    def _word(self, reg):
        v = (self.bus.read_byte_data(self.addr, reg) << 8) | self.bus.read_byte_data(self.addr, reg + 1)
        return v - 65536 if v > 32767 else v

    def accel(self):
        """(x, y, z) 단위 g. 기본 설정에서 16384 = 1g"""
        return tuple(self._word(r) / 16384 for r in (0x3B, 0x3D, 0x3F))


def tilt_deg(a) -> float:
    """센서가 수평에서 기운 각도"""
    x, y, z = a
    return math.degrees(math.atan2(math.sqrt(x * x + y * y), z))


class LidTracker:
    """가속도 값으로 뚜껑 상태를 추적

    update()는 상태가 바뀐 순간에만 이벤트를 돌려줌
      'open'   : 닫힘 기준보다 open_deg 넘게 기울어짐
      'closed' : 다시 닫히고 still_sec 동안 흔들림이 없음 (이때 무게를 재면 정확함)
    """

    def __init__(self, open_deg=30, still_limit=0.05, still_sec=2):
        self.open_deg, self.still_limit, self.still_sec = open_deg, still_limit, still_sec
        self.base = None
        self.prev = None
        self.is_open = False
        self.still_since = None

    def update(self, a, now_sec: float):
        if self.base is None:
            self.base, self.prev = tilt_deg(a), a
            return None
        shake = sum(abs(x - y) for x, y in zip(a, self.prev))
        self.prev = a
        opened = abs(tilt_deg(a) - self.base) > self.open_deg

        if opened and not self.is_open:
            self.is_open, self.still_since = True, None
            return "open"
        if not opened and self.is_open:
            if shake < self.still_limit:
                self.still_since = self.still_since or now_sec
                if now_sec - self.still_since >= self.still_sec:
                    self.is_open, self.still_since = False, None
                    return "closed"
            else:
                self.still_since = None
        return None

    @property
    def is_still(self):
        return not self.is_open
