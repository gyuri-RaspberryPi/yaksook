"""보호자 휴대폰 알림 (텔레그램 봇)

토큰이 없거나 설정에서 꺼져 있으면 터미널에만 출력합니다.
전송은 별도 스레드에서 해서 센서 판정이 멈추지 않게 합니다.
"""

import json
import threading
import urllib.error
import urllib.request

LEVEL_ICON = {"notice": "🟡", "warning": "🟠", "urgent": "🔴"}


class Notifier:
    def __init__(self, token="", chat_id=""):
        self.token, self.chat_id = token, chat_id

    @property
    def configured(self):
        return bool(self.token and self.chat_id)

    def send(self, level, text, enabled=True):
        msg = f"{LEVEL_ICON.get(level, '🔔')} [약속] {text}"
        print(msg, flush=True)
        if enabled and self.configured:
            threading.Thread(target=self._telegram, args=(msg,), daemon=True).start()

    def _telegram(self, msg):
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = json.dumps({"chat_id": self.chat_id, "text": msg}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except (urllib.error.URLError, OSError) as e:
            print("텔레그램 전송 실패:", e, flush=True)
