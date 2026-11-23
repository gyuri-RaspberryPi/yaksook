"""자이로 센서 단독 테스트 (약통 뚜껑)

준비: I2C 켜기, i2cdetect -y 1 에서 68 확인
실행: 뚜껑을 닫은 상태에서 python3 test_gyro.py   (종료: Ctrl+C)
뚜껑을 열면 '열림', 닫고 2초 동안 가만히 두면 '닫힘 + 멈춤 -> 무게 측정 시점'이 표시됩니다.
"""

import time

import config
from sensor.gyro import LidTracker, MPU6050, tilt_deg

mpu = MPU6050(config.I2C_BUS, config.MPU_ADDR)
lid = LidTracker(config.LID_OPEN_DEG, config.STILL_LIMIT_G, config.STILL_SEC)
base = None

while True:
    a = mpu.accel()
    if base is None:
        base = tilt_deg(a)
        print(f"기준 각도(닫힘): {base:.1f}도")
    ev = lid.update(a, time.time())
    state = "열림" if lid.is_open else "닫힘"
    msg = {"open": "   <- 뚜껑 열림!", "closed": "   <- 닫힘 + 멈춤 -> 무게 측정 시점"}.get(ev, "")
    print(f"각도 변화 {tilt_deg(a) - base:6.1f}도  뚜껑 {state}{msg}")
    time.sleep(config.SENSOR_POLL_SEC)
