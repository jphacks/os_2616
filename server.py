import os
import json
import time
import threading
from datetime import datetime, timezone, timedelta

import requests
from flask import Flask, request, jsonify, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")

# 動きがないと判断する時間（秒）
IDLE_SECONDS = int(os.environ.get("IDLE_SECONDS", "30"))

# M5GOから通信が途絶えたと判断する時間（秒）
OFFLINE_SECONDS = int(os.environ.get("OFFLINE_SECONDS", "60"))

# LINE送信に失敗したときの再送間隔（秒）
RETRY_SECONDS = 10

# LINEの環境変数
ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")
USER_ID = os.environ.get("LINE_USER_ID")

# M5GOとの合言葉（設定した場合のみ確認する）
DEVICE_TOKEN = os.environ.get("MIMAMO_DEVICE_TOKEN")

# 通知の記録を保存するファイルと、残す件数
HISTORY_PATH = os.environ.get(
    "MIMAMO_HISTORY_PATH",
    os.path.join(BASE_DIR, "data", "notifications.json")
)
HISTORY_LIMIT = 100
# 家族用画面に返す件数
HISTORY_SHOW = 20

# 家族用画面で変更できる設定を保存するファイル
SETTINGS_PATH = os.environ.get(
    "MIMAMO_SETTINGS_PATH",
    os.path.join(BASE_DIR, "data", "settings.json")
)
WHO_CHOICES = ("お母さま", "お父さま", "自分で入力")
DEFAULT_SETTINGS = {
    "who": "お母さま",   # 見守る方
    "name": "",          # 「自分で入力」のときのお名前
    "phone": "",         # 連絡先の電話番号（数字と + のみ）
    "sensor_name": "",   # センサーの場所
}
NAME_MAX = 20
PHONE_MAX = 15

JST = timezone(timedelta(hours=9))

lock = threading.Lock()

# M5GOから最初に通信があった時刻
first_seen = None
# M5GOから最後に通信があった時刻（動きなしの定期送信も含む）
last_seen = None
# 最後に動きを検知した時刻
last_motion = None
# 送信済みの警告 {"idle", "offline"}
alerted = set()
# 送信待ちの通知（history の要素と同じものを指す）
outbox = []
# 通知の記録（古い順）
history = []
# この時刻までは再送しない
retry_at = 0.0
# 家族用画面で変更できる設定
settings = dict(DEFAULT_SETTINGS)


def format_time(ts):
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, JST).isoformat(timespec="seconds")


def format_time_short(ts):
    if ts is None:
        return "なし"
    return datetime.fromtimestamp(ts, JST).strftime("%m/%d %H:%M:%S")


def load_history():
    """起動時に、保存してある通知の記録を読み込む"""
    try:
        with open(HISTORY_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as e:
        print("通知の記録を読み込めませんでした:", e, flush=True)
        return []
    return data[-HISTORY_LIMIT:] if isinstance(data, list) else []


def write_json(path, data, label):
    """lock内で呼ぶ。途中で止まっても壊れないよう、一時ファイル経由で保存する"""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except OSError as e:
        print(f"{label}を保存できませんでした:", e, flush=True)


def save_history():
    """lock内で呼ぶ。通知の記録をファイルに保存する"""
    write_json(HISTORY_PATH, history, "通知の記録")


def load_settings():
    """起動時に、保存してある設定を読み込む"""
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return dict(DEFAULT_SETTINGS)
    except (OSError, ValueError) as e:
        print("設定を読み込めませんでした:", e, flush=True)
        return dict(DEFAULT_SETTINGS)
    merged = dict(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        try:
            merged.update(clean_settings(data))
        except ValueError as e:
            print("設定ファイルの内容が正しくありません:", e, flush=True)
    return merged


def clean_settings(data):
    """家族用画面から届いた設定を確認し、保存できる形に整える。
    おかしな値があれば ValueError（メッセージは画面に表示する日本語）"""
    cleaned = {}
    for key, value in data.items():
        if key not in DEFAULT_SETTINGS:
            continue
        if not isinstance(value, str):
            raise ValueError("文字で入力してください")
        value = value.strip()
        if key == "who":
            if value not in WHO_CHOICES:
                raise ValueError("見守る方の選び方が正しくありません")
        elif key == "phone":
            # ハイフン・空白・かっこは取り除き、数字と先頭の + だけ残す
            value = "".join(c for c in value if c.isdigit() or c == "+")
            if value.count("+") > 1 or ("+" in value and not value.startswith("+")):
                raise ValueError("電話番号の書き方が正しくありません")
            if len(value) > PHONE_MAX:
                raise ValueError(f"電話番号は{PHONE_MAX}けた以内で入力してください")
        elif len(value) > NAME_MAX:
            raise ValueError(f"{NAME_MAX}文字以内で入力してください")
        cleaned[key] = value
    return cleaned


def display_name():
    """lock内で呼ぶ。画面やLINEで使う、見守る方の呼び名"""
    if settings["who"] == "自分で入力":
        return settings["name"] or "ご家族"
    return settings["who"]


def subject_text():
    """lock内で呼ぶ。LINEの文に入れる「お母さま（リビング）」のような表記"""
    place = settings["sensor_name"]
    return f"{display_name()}（{place}）" if place else display_name()


def add_notification(kind, message, now):
    """lock内で呼ぶ。通知を記録し、LINEの送信待ちに入れる"""
    entry = {
        "kind": kind,
        "message": message,
        "detected_at": format_time(now),
        "last_motion": format_time(last_motion),
        "line_status": "pending",
        "sent_at": None,
    }
    history.append(entry)
    del history[:-HISTORY_LIMIT]
    outbox.append(entry)
    save_history()


def current_status(now):
    """lock内で呼ぶ。waiting / ok / idle / offline のいずれかを返す"""
    if last_seen is None:
        return "waiting"
    if now - last_seen >= OFFLINE_SECONDS:
        return "offline"
    base = last_motion if last_motion is not None else first_seen
    if now - base >= IDLE_SECONDS:
        return "idle"
    return "ok"


def send_line(message):
    if not ACCESS_TOKEN or not USER_ID:
        print("エラー：LINEの環境変数が設定されていません", flush=True)
        return False

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    data = {
        "to": USER_ID,
        "messages": [
            {
                "type": "text",
                "text": message
            }
        ]
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=data,
            timeout=15
        )

        print(
            "LINE送信結果:",
            response.status_code,
            response.text,
            flush=True
        )

        return response.status_code == 200

    except requests.RequestException as e:
        print("LINE送信エラー:", e, flush=True)
        return False


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/motion", methods=["POST"])
def motion():
    global first_seen, last_seen, last_motion

    if DEVICE_TOKEN and request.headers.get("X-Device-Token") != DEVICE_TOKEN:
        return jsonify({"ok": False, "error": "invalid token"}), 401

    data = request.get_json(silent=True)
    value = data.get("motion") if isinstance(data, dict) else None

    # True/False のほか、UIFlowから送りやすい 1/0 も受け付ける
    if not isinstance(value, (bool, int)) or value not in (0, 1):
        print("不正なデータを受信:", data, flush=True)
        return jsonify({
            "ok": False,
            "error": 'send {"motion": true} or {"motion": false}'
        }), 400

    now = time.time()

    with lock:
        if first_seen is None:
            first_seen = now
        last_seen = now

        if "offline" in alerted:
            alerted.discard("offline")
            add_notification(
                "online",
                f"ミマモリンク：{subject_text()}の見守り機器との通信が復旧しました。",
                now
            )

        if value:
            last_motion = now
            if "idle" in alerted:
                alerted.discard("idle")
                add_notification(
                    "motion",
                    f"ミマモリンク：{subject_text()}の動きを検知しました"
                    f"（{format_time_short(now)}）。",
                    now
                )

        status = current_status(now)

    if value:
        print("動きを検知。タイマーをリセット", flush=True)

    return jsonify({"ok": True, "status": status})


@app.route("/api/status")
def status():
    now = time.time()

    with lock:
        state = current_status(now)
        base = last_motion if last_motion is not None else first_seen
        body = {
            "status": state,
            "last_motion": format_time(last_motion),
            "last_seen": format_time(last_seen),
            "seconds_since_motion": (
                int(now - base) if base is not None else None
            ),
            "idle_seconds": IDLE_SECONDS,
            "offline_seconds": OFFLINE_SECONDS,
            "server_time": format_time(now),
            # 新しい順
            "notifications": [
                dict(entry) for entry in reversed(history[-HISTORY_SHOW:])
            ],
            "settings": dict(settings),
            "display_name": display_name(),
        }

    return jsonify(body)


@app.route("/api/settings", methods=["GET", "POST"])
def settings_api():
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return jsonify({"ok": False, "error": "設定の形式が正しくありません"}), 400
        try:
            cleaned = clean_settings(data)
        except ValueError as e:
            return jsonify({"ok": False, "error": str(e)}), 400

        with lock:
            settings.update(cleaned)
            write_json(SETTINGS_PATH, settings, "設定")
            print("設定を変更しました:", cleaned, flush=True)

    with lock:
        return jsonify({
            "ok": True,
            "settings": dict(settings),
            "display_name": display_name(),
        })


def check_once(now=None):
    """監視処理を1回分行う。必要ならLINEを1通送る"""
    global retry_at

    now = time.time() if now is None else now

    with lock:
        state = current_status(now)

        if state in ("idle", "offline") and state not in alerted:
            alerted.add(state)
            if state == "idle":
                add_notification(
                    "idle",
                    f"ミマモリンク：{subject_text()}の様子："
                    f"{IDLE_SECONDS}秒間、動きが検知されていません。"
                    f"（最終検知 {format_time_short(last_motion)}）",
                    now
                )
            else:
                add_notification(
                    "offline",
                    f"ミマモリンク：{subject_text()}の見守り機器からの通信が"
                    f"{OFFLINE_SECONDS}秒以上途絶えています。"
                    "電源やWi-Fiを確認してください。",
                    now
                )

        entry = outbox[0] if outbox and now >= retry_at else None

    if entry is None:
        return

    print("LINE通知を送信します:", entry["message"], flush=True)

    if send_line(entry["message"]):
        with lock:
            # 取り出すのはこのスレッドだけなので先頭は entry のまま
            outbox.pop(0)
            entry["line_status"] = "sent"
            entry["sent_at"] = format_time(now)
            save_history()
    else:
        with lock:
            entry["line_status"] = "failed"
            save_history()
        retry_at = now + RETRY_SECONDS


def monitor_idle():
    while True:
        time.sleep(1)
        check_once()


if __name__ == "__main__":
    history.extend(load_history())
    settings.update(load_settings())

    threading.Thread(
        target=monitor_idle,
        daemon=True
    ).start()

    app.run(
        host="0.0.0.0",
        port=5000,
        debug=False
    )
