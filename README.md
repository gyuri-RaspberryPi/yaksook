# 약속 💊 — 복약 확인 약통

어르신이 약통에 다가가면 안내하고, 뚜껑·무게·물컵으로 **복용 추정 / 꺼내기만 함 / 미복용**을 판정해
중복 복용·과다 복용·미복용을 보호자에게 알려 주는 라즈베리파이 + Flask 서비스입니다.

- 김규민 — 임베디드, 프론트엔드
- 김리원 — 백엔드, 디자인

---

## 1. 바로 실행해 보기 (센서 없이, 노트북에서)

VS Code에서 `medicine` 폴더를 열고 터미널(`Ctrl + \``)에서:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install flask
python3 app.py --demo
```

| 화면 | 주소 | 누가 |
| --- | --- | --- |
| 스테이션 | http://localhost:5000/ | 어르신 (약통 옆 모니터·태블릿) |
| 보호자 | http://localhost:5000/guardian | 보호자 (휴대폰) |
| 처방·설정 | http://localhost:5000/register | 보호자 |
| 주간 리포트 | http://localhost:5000/report | 보호자·의사 |

- `--demo`는 **실행한 시각이 복약 시간**이 되도록 시연용 처방을 넣습니다. 발표가 몇 시든 바로 시연됩니다.
- 스테이션 화면에서 **"음성 안내 켜기"** 를 한 번 눌러야 소리가 납니다 (브라우저 정책).
- 보호자 화면 맨 아래 **"시연용 가짜 센서"** 버튼으로 다가옴·뚜껑 열림·2알 꺼냄·물컵을 흉내 낼 수 있습니다.

## 2. 라즈베리파이에서 실제 센서로 실행

```bash
git clone <우리 저장소 주소> && cd medicine
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install -r requirements.txt
sudo raspi-config      # Interface Options → I2C → Enable (자이로용), 재부팅
```

센서를 하나씩 확인한 뒤 서버를 켭니다.

```bash
python3 test_ultrasonic.py     # 손을 50cm 안으로 → "다가옴!" 출력
python3 test_gyro.py           # 뚜껑 열기/닫기 → 기울기·열림 출력
python3 test_fsr.py            # 컵 올리기/내리기
python3 test_loadcell.py       # 4단계 무게 보정 → DB에 저장
python3 app.py --hardware      # 실제 센서로 서버 실행
```

휴대폰은 **라즈베리파이와 같은 와이파이(핫스팟)** 에 연결하고 `http://<파이 IP>:5000/guardian` 으로 접속합니다.
파이 IP는 `hostname -I` 로 확인합니다.

### 배선 (config.py와 같아야 함)

| 센서 | 핀 | 라즈베리파이 (BCM) |
| --- | --- | --- |
| 초음파 HC-SR04P | VCC / GND / TRIG / ECHO | 3.3V / GND / GPIO23 / GPIO24 |
| 자이로 MPU-6050 | VCC / GND / SDA / SCL | 3.3V / GND / GPIO2 / GPIO3 |
| 압력 FSR (물컵) | 한쪽 → 3.3V, 다른 쪽 → GPIO17 + 10kΩ → GND | GPIO17 |
| 로드셀 HX711 | VCC / GND / DT / SCK | 3.3V / GND / GPIO5 / GPIO6 |

> HC-SR04P는 3.3V로 쓰면 ECHO를 그대로 연결해도 됩니다. 5V짜리 HC-SR04라면 ECHO에 분압 저항이 꼭 필요합니다.

## 3. 텔레그램 알림 (선택)

```bash
export MEDICINE_TELEGRAM_TOKEN="봇 토큰"
export MEDICINE_TELEGRAM_CHAT_ID="채팅 ID"
python3 app.py --hardware
```

그다음 처방·설정 화면에서 **"텔레그램으로 휴대폰 알림 받기"** 를 켭니다. 토큰이 없으면 보호자 웹 화면 알림만 동작합니다.

## 4. 테스트

```bash
python3 test_fake_sensor.py
```

센서 없이 시각을 직접 넣어 판정 로직과 API 23가지를 확인합니다. **코드를 고친 뒤에는 꼭 돌려 주세요.**

---

## 파일 구조

```
medicine/
├── app.py                 Flask 서버: 화면 4개 + API 15개 + 실시간 안내(SSE) + 시연용 /api/sim
├── config.py              핀 번호, 판정 기준값, 기본 설정
├── requirements.txt
├── model/
│   ├── database.py        SQLite 테이블·연결 (노션 구조에 추가한 파일)
│   ├── schedule.py        처방 검증·저장, 지금 어느 시간대인지 계산
│   ├── detector.py        ★ 판정의 중심: 다가옴·뚜껑·무게·물컵 이벤트 → 안내/알림
│   ├── alerts.py          알림 저장·중복 방지, 안부 확인(기상·무활동·밤 배회), 센서 고장
│   ├── report.py          오늘 상태, 주간 리포트, 남은 약, 활동 그래프
│   └── notifier.py        텔레그램 전송
├── sensor/
│   ├── __init__.py        센서 4개를 묶어 0.1초마다 읽고 detector에 전달 (노션 구조에 추가한 파일)
│   ├── ultrasonic.py      초음파: 50cm 안으로 들어온 순간 감지
│   ├── gyro.py            자이로: 뚜껑 기울기 30° 이상 = 열림, 2초 멈추면 닫힘
│   ├── loadcell.py        로드셀: HX711 읽기, 무게 → 알약 개수
│   └── cup.py             압력(FSR): 물컵 들었다 놓음
├── templates/             index(스테이션) · guardian · register · report
├── static/
│   ├── css/style.css
│   ├── js/live.js         화면 4개 스크립트 (스테이션은 SSE로 실시간 안내 + 음성)
│   └── sounds/            chime.wav(안내), alert.wav(경고)
├── db/                    medicine.db가 여기에 생김 (git에는 안 올라감)
├── test_fake_sensor.py    센서 없이 전체 로직 테스트
└── test_ultrasonic.py · test_gyro.py · test_fsr.py · test_loadcell.py   센서 단독 테스트
```

## 판정 흐름 한눈에

1. **초음파** 50cm 안 → 복약 시간인데 아직 안 먹었으면 15초 기다림
2. 15초 안에 **자이로**가 뚜껑 열림을 잡으면 "점심 약 2알 꺼내세요", 안 열면 "약 드실 시간이에요"
3. 뚜껑을 닫고 2초 멈추면 **로드셀**로 무게 측정 → 꺼낸 알약 개수
   - 맞게 꺼냄 → 기록 / 적게 → 더 꺼내라고 안내(합산) / 많이 → 과다 경고 / 이미 먹음 → 🔴 중복 복용 긴급 알림
4. 2분 안에 **압력 센서** 위 컵을 들었다 놓으면 "물과 함께 복용 추정", 아니면 보호자에게 확인 요청
5. 복약 시간 0분·15분에 안내, 30분 지나면 보호자에게 미복용 알림
