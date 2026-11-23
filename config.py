"""약속 - 설정값

핀 번호, 센서 판정 기준값, 서비스 기본 설정이 모두 여기에 있습니다.
실제 약통에서 테스트하면서 숫자를 조정하세요.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------- 서버
HOST = "0.0.0.0"          # 같은 와이파이의 휴대폰에서 접속할 수 있게 모든 주소에서 받음
PORT = 5000
DB_PATH = os.environ.get("MEDICINE_DB", os.path.join(BASE_DIR, "db", "medicine.db"))

# ---------------------------------------------------------------- 핀 번호 (BCM 번호)
ULTRASONIC_TRIG = 23      # 초음파 TRIG
ULTRASONIC_ECHO = 24      # 초음파 ECHO (HC-SR04P는 3.3V에서 동작)
FSR_PIN = 17              # 물컵 압력 센서 (3.3V - FSR - GPIO17 - 10kΩ - GND)
HX711_DT = 5              # 로드셀 HX711 DT
HX711_SCK = 6             # 로드셀 HX711 SCK
I2C_BUS = 1               # 자이로 MPU-6050 (SDA=GPIO2, SCL=GPIO3)
MPU_ADDR = 0x68

# ---------------------------------------------------------------- 센서 판정 기준값
SENSOR_POLL_SEC = 0.1     # 센서를 읽는 간격
NEAR_CM = 50              # 이 거리 안으로 들어오면 "약통 앞에 사람이 옴"
LID_OPEN_DEG = 30         # 닫힌 상태보다 이만큼 기울면 "뚜껑 열림"
STILL_LIMIT_G = 0.05      # 가속도 변화가 이보다 작으면 "멈춤"
STILL_SEC = 2             # 뚜껑이 닫히고 이 시간 동안 멈춰 있으면 무게 측정
LOADCELL_SAMPLES = 15     # 무게를 이만큼 재서 가운데 값 사용
LOADCELL_IDLE_READ_SEC = 5  # 뚜껑이 닫혀 있을 때 무게를 확인하는 간격
SENSOR_TIMEOUT_SEC = 10   # 센서 값이 이 시간 넘게 안 들어오면 "점검 필요"
RELIABLE_RATIO = 3        # 1알 무게가 흔들림의 이 배수 이상이어야 개수를 믿을 수 있음

# ---------------------------------------------------------------- 서비스 판정 기준값
WATER_WINDOW_SEC = 120    # 약을 꺼낸 뒤 이 시간 안에 물컵을 들면 "복용 추정"
PRESENCE_GAP_SEC = 60     # 이 시간 안의 연속 감지는 움직임 1번으로 셈
LOW_INVENTORY_DAYS = 3    # 남은 약이 이 일수 이하면 보호자 알림

# 보호자가 설정 화면(PUT /api/settings)에서 바꿀 수 있는 값의 기본값
DEFAULT_SETTINGS = {
    "callName": "어르신",
    "approachWaitSec": 15,
    "approachCooldownMin": 10,
    "repeatOpenAlert": 3,
    "missedRemindMin": 15,
    "missedGuardianMin": 30,
    "dayStart": "07:00",
    "dayEnd": "21:00",
    "wakeCheckTime": "10:00",
    "inactiveHours": 4,
    "night": {"start": "00:00", "end": "05:00", "count": 3},
    "voiceRate": 0.85,
    "telegramEnabled": False,
}

# ---------------------------------------------------------------- 텔레그램 (보호자 휴대폰 알림)
# 라즈베리파이에서: export MEDICINE_TELEGRAM_TOKEN=...  export MEDICINE_TELEGRAM_CHAT_ID=...
TELEGRAM_TOKEN = os.environ.get("MEDICINE_TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("MEDICINE_TELEGRAM_CHAT_ID", "")

# ---------------------------------------------------------------- 시연용 처방 (python app.py --demo)
# 시간대는 실행한 시각을 기준으로 자동으로 만들어집니다 (지금이 복약 시간이 되도록).
DEMO_SCHEDULE = {
    "medicine": "혈압약",
    "days": 30,
    "windowMin": 60,
    "prn": None,
}
