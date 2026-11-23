"""초음파 센서 단독 테스트

실행: python3 test_ultrasonic.py   (종료: Ctrl+C)
손을 센서 앞에서 가까이 댔다 멀리 하면서 거리가 바뀌는지 확인하세요.
"""

from time import sleep

import config
from sensor.ultrasonic import NearDetector, Ultrasonic

sensor = Ultrasonic(config.ULTRASONIC_TRIG, config.ULTRASONIC_ECHO)
near = NearDetector(config.NEAR_CM)
print(f"{config.NEAR_CM}cm 안으로 들어오면 '다가옴'이 표시됩니다.")

while True:
    cm = sensor.distance_cm()
    entered = near.update(cm)
    state = "사람 있음" if cm < config.NEAR_CM else "없음"
    print(f"거리 {cm:6.1f} cm  {state}" + ("   <- 다가옴!" if entered else ""))
    sleep(0.2)
