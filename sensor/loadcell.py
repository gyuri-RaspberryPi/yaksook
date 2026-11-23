"""로드셀 + HX711 - 약통 무게 -> 알 수

연결: HX711 VCC -> 3.3V, GND -> GND, DT -> GPIO5, SCK -> GPIO6
      로드셀 선 -> HX711: 빨강 E+, 검정 E-, 흰색 A-, 초록 A+
      (무게가 음수로 나오면 흰색과 초록을 바꿔 꽂으세요)
로드셀은 한쪽 끝을 바닥판에, 반대쪽 끝을 윗판에 나사로 고정해야 정확합니다.
라즈베리파이 5에서는 'pip install rpi-lgpio'를 설치하면 RPi.GPIO 코드가 그대로 동작합니다.
"""

import time


class HX711:
    def __init__(self, dt, sck):
        import RPi.GPIO as GPIO  # 라즈베리파이에서만 필요
        self.GPIO = GPIO
        self.dt, self.sck = dt, sck
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(sck, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(dt, GPIO.IN)

    def read_raw(self, timeout=0.5) -> int:
        """24비트 값 하나. 연결이 빠져 있으면 TimeoutError"""
        G = self.GPIO
        start = time.time()
        while G.input(self.dt) == 1:
            if time.time() - start > timeout:
                raise TimeoutError("HX711 응답 없음 (배선 확인)")
            time.sleep(0.001)
        v = 0
        for _ in range(24):
            G.output(self.sck, True)
            v = (v << 1) | G.input(self.dt)
            G.output(self.sck, False)
        G.output(self.sck, True)   # 25번째 펄스: 다음도 A채널, 증폭 128배
        G.output(self.sck, False)
        return v - (1 << 24) if v & 0x800000 else v


def median_and_noise(values):
    """가운데 값과 흔들림(최대-최소)"""
    s = sorted(values)
    return s[len(s) // 2], s[-1] - s[0]


def grams(raw, offset, scale):
    return (raw - offset) / scale


def pills_from_grams(g, box_g, one_pill_g):
    return max(0, round((g - box_g) / one_pill_g))


class Scale:
    """보정값을 가지고 무게와 알 수를 계산

    보정값(calibration)은 DB에 저장: {"offset", "scale", "boxG", "onePillG", "noiseG", "reliable"}
    """

    def __init__(self, hx: HX711, samples=15):
        self.hx, self.samples = hx, samples
        self.cal = {}

    def read(self):
        """(가운데 raw 값, raw 흔들림)"""
        return median_and_noise([self.hx.read_raw() for _ in range(self.samples)])

    def grams(self):
        raw, noise = self.read()
        g = grams(raw, self.cal["offset"], self.cal["scale"])
        return g, abs(noise / self.cal["scale"])

    def pills(self):
        g, _ = self.grams()
        return pills_from_grams(g, self.cal["boxG"], self.cal["onePillG"])

    @property
    def ready(self):
        return all(k in self.cal for k in ("offset", "scale", "boxG", "onePillG"))
