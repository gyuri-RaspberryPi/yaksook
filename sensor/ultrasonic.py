"""초음파 센서 HC-SR04P - 약통 앞 사람 감지

연결: VCC -> 3.3V, GND -> GND, TRIG -> GPIO23, ECHO -> GPIO24
"""


class Ultrasonic:
    def __init__(self, trigger, echo, max_distance_m=2.0):
        from gpiozero import DistanceSensor  # 라즈베리파이에서만 필요
        self.sensor = DistanceSensor(echo=echo, trigger=trigger, max_distance=max_distance_m)

    def distance_cm(self) -> float:
        return self.sensor.distance * 100


class NearDetector:
    """멀리 있다가 가까이 들어온 '순간'만 True (계속 서 있어도 한 번만)"""

    def __init__(self, near_cm):
        self.near_cm = near_cm
        self.was_near = False

    def update(self, cm: float) -> bool:
        near = cm < self.near_cm
        entered = near and not self.was_near
        self.was_near = near
        return entered
