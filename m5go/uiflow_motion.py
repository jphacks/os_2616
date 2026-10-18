# ミマモリンク M5GO 側プログラム（UIFlow 1.x の Python タブに貼り付けて使う）
# PIRユニットを PORT B に接続し、動きを検知したらPCのサーバーへ送信する。
# 動きがなくても HEARTBEAT_MS ごとに motion=false を送り、
# サーバー側で「通信が途絶えた」状態と区別できるようにする。

from m5stack import *
from m5ui import *
from uiflow import *
import unit
import urequests
import wifiCfg
import time

# PCのIPアドレスに書き換える（PowerShell で ipconfig を実行して確認）
SERVER_URL = 'http://192.168.0.10:5000/motion'

# サーバーで MIMAMO_DEVICE_TOKEN を設定した場合は同じ値を入れる
DEVICE_TOKEN = ''

# 動きがないときの定期送信の間隔
HEARTBEAT_MS = 10000

# 動き続けているときに送りすぎないための間隔
MOTION_INTERVAL_MS = 2000

setScreenColor(0x000000)
label_state = M5TextBox(10, 20, 'starting', lcd.FONT_DejaVu24, 0xFFFFFF, rotate=0)
label_net = M5TextBox(10, 70, '', lcd.FONT_DejaVu18, 0xAAAAAA, rotate=0)

wifiCfg.autoConnect(lcdShow=True)
setScreenColor(0x000000)

pir = unit.get(unit.PIR, unit.PORTB)


def post_motion(motion):
    headers = {'Content-Type': 'application/json'}
    if DEVICE_TOKEN:
        headers['X-Device-Token'] = DEVICE_TOKEN
    try:
        req = urequests.request(
            method='POST',
            url=SERVER_URL,
            json={'motion': motion},
            headers=headers
        )
        code = req.status_code
        req.close()
        label_net.setText('send ' + str(code))
        return code == 200
    except Exception:
        label_net.setText('send error')
        return False


last_sent = time.ticks_ms() - HEARTBEAT_MS
last_motion_sent = time.ticks_ms() - MOTION_INTERVAL_MS

while True:
    now = time.ticks_ms()

    if pir.state == 1:
        label_state.setText('MOTION   ')
        if time.ticks_diff(now, last_motion_sent) >= MOTION_INTERVAL_MS:
            post_motion(True)
            last_motion_sent = now
            last_sent = now
    else:
        label_state.setText('no motion')

    if time.ticks_diff(now, last_sent) >= HEARTBEAT_MS:
        post_motion(False)
        last_sent = now

    wait_ms(100)
