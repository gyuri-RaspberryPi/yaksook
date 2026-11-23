"""로드셀 단독 테스트 + 보정

실행: python3 test_loadcell.py   (종료: Ctrl+C)
화면 안내에 따라 4단계로 보정하고, 마지막에 저장하면 서버(app.py)가 그 보정값을 그대로 씁니다.
보호자 화면의 '로드셀 보정'(POST /api/calibration)으로 해도 결과는 같습니다.
"""

import time

import config
from model.database import Database
from sensor.loadcell import HX711, Scale, median_and_noise

scale = Scale(HX711(config.HX711_DT, config.HX711_SCK), config.LOADCELL_SAMPLES)

input("1) 받침 위를 비우고 Enter (영점)")
offset, _ = scale.read()

g = float(input("2) 무게를 아는 물건을 올리고 그 무게(g) 입력: "))
raw, _ = scale.read()
k = (raw - offset) / g
print(f"   보정값 {k:.2f}")

input("3) 물건을 내리고 빈 약통을 올린 뒤 Enter")
raw, _ = scale.read()
box_g = (raw - offset) / k

n = int(input("4) 약(또는 사탕)을 정확히 넣은 개수 입력 (10 추천): "))
raws = [scale.read()[0] for _ in range(3)]
mid, spread = median_and_noise(raws)
one = ((mid - offset) / k - box_g) / n
noise_g = abs(spread / k) + 0.05
reliable = one >= config.RELIABLE_RATIO * noise_g
print(f"   1알 무게 {one:.2f} g, 흔들림 약 {noise_g:.2f} g -> {'믿을 만함' if reliable else '알약이 너무 가벼워 개수가 틀릴 수 있음'}")

scale.cal = {"offset": offset, "scale": k, "boxG": box_g, "onePillG": one, "noiseG": noise_g, "reliable": reliable}
if input("이 보정값을 서버에 저장할까요? (y/n): ").strip().lower() == "y":
    db = Database(config.DB_PATH)
    db.set_kv("calibration", scale.cal)
    db.set_kv("inventory", {"pillsLeft": n, "measuredAt": time.strftime("%Y-%m-%dT%H:%M:%S")})
    print("   저장했어요. 이제 app.py를 --hardware로 실행하면 이 값을 씁니다.")

print("\n약을 빼거나 넣어 보세요.\n")
while True:
    grams, noise = scale.grams()
    print(f"무게 {grams:7.2f} g (흔들림 {noise:.2f} g)   남은 약 {scale.pills()}알")
    time.sleep(0.5)
