"""압력 센서 FSR - 물컵 들림·놓임

연결: 3.3V -> FSR 한쪽 다리, FSR 다른 쪽 다리 -> GPIO17, GPIO17 -> 10kΩ -> GND
컵을 올려도 '들림'으로 나오면 저항을 22kΩ, 47kΩ처럼 큰 것으로 바꾸세요.
FSR 위에 동전을 올리고 그 위에 컵 받침을 놓으면 무게가 한곳에 모여 잘 감지됩니다.
"""

import time


class Cup:
    def __init__(self, pin, on_placed):
        """on_placed(held_sec): 컵을 들었다가 다시 놓았을 때 호출"""
        from gpiozero import Button  # 라즈베리파이에서만 필요
        self.button = Button(pin, pull_up=None, active_state=True, bounce_time=0.1)
        self.lifted_at = None
        self.on_placed = on_placed
        self.button.when_released = self._lifted
        self.button.when_pressed = self._placed

    def _lifted(self):
        self.lifted_at = time.time()

    def _placed(self):
        if self.lifted_at is not None:
            held = time.time() - self.lifted_at
            self.lifted_at = None
            self.on_placed(held)

    @property
    def is_placed(self) -> bool:
        return self.button.is_pressed
