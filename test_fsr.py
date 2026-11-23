"""압력 센서(물컵) 단독 테스트

실행: python3 test_fsr.py   (종료: Ctrl+C)
컵을 들었다가 다시 놓으면 '물 마심 1회'가 표시됩니다.
"""

from signal import pause

import config
from sensor.cup import Cup


def placed(held):
    print(f"컵 놓임 (들고 있던 시간 {held:.1f}초) -> 물 마심 1회")


cup = Cup(config.FSR_PIN, placed)
cup.button.when_released = lambda: (cup._lifted(), print("컵 들림"))
print("현재 상태:", "놓임" if cup.is_placed else "들림 (컵을 올려도 이렇게 나오면 저항을 키우세요)")
pause()
