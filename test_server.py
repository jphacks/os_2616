import json
import os
import tempfile
import unittest
from unittest import mock

import server


class ServerTest(unittest.TestCase):
    def setUp(self):
        server.first_seen = None
        server.last_seen = None
        server.last_motion = None
        server.alerted.clear()
        server.outbox.clear()
        server.history.clear()
        server.retry_at = 0.0
        server.DEVICE_TOKEN = None
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.history_path = os.path.join(tmp.name, "data", "notifications.json")
        path_patcher = mock.patch.object(
            server, "HISTORY_PATH", self.history_path
        )
        path_patcher.start()
        self.addCleanup(path_patcher.stop)
        self.settings_path = os.path.join(tmp.name, "data", "settings.json")
        settings_patcher = mock.patch.object(
            server, "SETTINGS_PATH", self.settings_path
        )
        settings_patcher.start()
        self.addCleanup(settings_patcher.stop)
        server.settings.clear()
        server.settings.update(server.DEFAULT_SETTINGS)
        self.client = server.app.test_client()
        self.sent = []
        patcher = mock.patch.object(
            server, "send_line", side_effect=self.fake_send
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.send_ok = True

    def fake_send(self, message):
        self.sent.append(message)
        return self.send_ok

    def post(self, body, at, headers=None):
        with mock.patch.object(server.time, "time", return_value=at):
            return self.client.post("/motion", json=body, headers=headers)

    def status(self, at):
        with mock.patch.object(server.time, "time", return_value=at):
            return self.client.get("/api/status").get_json()

    def test_no_alert_before_device_connects(self):
        server.check_once(1000)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.status(1000)["status"], "waiting")

    def test_idle_alert_once_then_recovery(self):
        self.post({"motion": True}, 1000)
        self.post({"motion": False}, 1020)
        server.check_once(1029)
        self.assertEqual(self.sent, [])

        self.post({"motion": False}, 1030)
        server.check_once(1030)
        server.check_once(1031)
        self.assertEqual(len(self.sent), 1)
        self.assertIn("動きが検知されていません", self.sent[0])

        self.post({"motion": True}, 1040)
        server.check_once(1040)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("動きを検知しました", self.sent[1])

    def test_offline_alert_and_recovery(self):
        self.post({"motion": True}, 1000)
        server.check_once(1030)
        server.check_once(1060)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("途絶えています", self.sent[1])
        self.assertEqual(self.status(1060)["status"], "offline")

        self.post({"motion": False}, 1070)
        server.check_once(1070)
        self.assertIn("復旧しました", self.sent[2])

    def test_failed_send_is_retried(self):
        self.send_ok = False
        self.post({"motion": True}, 1000)
        self.post({"motion": False}, 1030)
        server.check_once(1030)
        server.check_once(1035)
        self.assertEqual(len(self.sent), 1)

        self.send_ok = True
        server.check_once(1040)
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.sent[0], self.sent[1])
        server.check_once(1041)
        self.assertEqual(len(self.sent), 2)

    def test_status_shows_last_motion(self):
        self.post({"motion": 1}, 1000)
        data = self.status(1005)
        self.assertEqual(data["status"], "ok")
        self.assertIsNotNone(data["last_motion"])
        self.assertEqual(data["seconds_since_motion"], 5)

    def test_invalid_body_rejected(self):
        self.assertEqual(self.post({"motion": "yes"}, 1000).status_code, 400)
        self.assertEqual(self.post({}, 1000).status_code, 400)
        self.assertIsNone(server.last_seen)

    def test_device_token(self):
        server.DEVICE_TOKEN = "secret"
        self.assertEqual(self.post({"motion": True}, 1000).status_code, 401)
        res = self.post(
            {"motion": True}, 1000, headers={"X-Device-Token": "secret"}
        )
        self.assertEqual(res.status_code, 200)

    def test_idle_notification_is_recorded(self):
        self.post({"motion": True}, 1000)
        self.post({"motion": False}, 1030)
        server.check_once(1030)

        data = self.status(1031)
        self.assertEqual(len(data["notifications"]), 1)
        entry = data["notifications"][0]
        self.assertEqual(entry["kind"], "idle")
        self.assertEqual(entry["last_motion"], server.format_time(1000))
        self.assertEqual(entry["detected_at"], server.format_time(1030))
        self.assertEqual(entry["line_status"], "sent")
        self.assertEqual(entry["sent_at"], server.format_time(1030))

        # 動きが戻ったときの通知も、新しい順で先頭に入る
        self.post({"motion": True}, 1040)
        server.check_once(1040)
        kinds = [e["kind"] for e in self.status(1041)["notifications"]]
        self.assertEqual(kinds, ["motion", "idle"])

    def test_failed_line_is_recorded_then_marked_sent(self):
        self.send_ok = False
        self.post({"motion": True}, 1000)
        self.post({"motion": False}, 1030)
        server.check_once(1030)
        entry = self.status(1031)["notifications"][0]
        self.assertEqual(entry["line_status"], "failed")
        self.assertIsNone(entry["sent_at"])

        self.send_ok = True
        server.check_once(1040)
        entry = self.status(1041)["notifications"][0]
        self.assertEqual(entry["line_status"], "sent")
        self.assertEqual(entry["sent_at"], server.format_time(1040))
        self.assertEqual(len(self.status(1041)["notifications"]), 1)

    def test_history_is_saved_and_loaded(self):
        self.post({"motion": True}, 1000)
        self.post({"motion": False}, 1030)
        server.check_once(1030)

        with open(self.history_path, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["line_status"], "sent")

        # サーバーを起動し直したときと同じように読み込める
        self.assertEqual(server.load_history(), saved)

    def test_history_is_limited(self):
        with mock.patch.object(server, "HISTORY_LIMIT", 3):
            with server.lock:
                for i in range(5):
                    server.add_notification("idle", f"msg{i}", 1000 + i)
        self.assertEqual(
            [e["message"] for e in server.history], ["msg2", "msg3", "msg4"]
        )

    def save_settings(self, body):
        return self.client.post("/api/settings", json=body)

    def test_settings_default(self):
        data = self.client.get("/api/settings").get_json()
        self.assertEqual(data["settings"], server.DEFAULT_SETTINGS)
        self.assertEqual(data["display_name"], "お母さま")

    def test_settings_saved_and_shown_in_status(self):
        res = self.save_settings({
            "who": "自分で入力",
            "name": " おばあちゃん ",
            "phone": "090-1234-5678",
            "sensor_name": "リビング",
        })
        self.assertEqual(res.status_code, 200)
        saved = res.get_json()["settings"]
        self.assertEqual(saved["name"], "おばあちゃん")
        self.assertEqual(saved["phone"], "09012345678")

        data = self.status(1000)
        self.assertEqual(data["display_name"], "おばあちゃん")
        self.assertEqual(data["settings"]["sensor_name"], "リビング")

        # ファイルに保存され、起動し直しても読み込める
        with open(self.settings_path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["phone"], "09012345678")
        self.assertEqual(server.load_settings()["name"], "おばあちゃん")

    def test_settings_partial_update_keeps_others(self):
        self.save_settings({"phone": "0312345678", "sensor_name": "寝室"})
        self.save_settings({"who": "お父さま"})
        data = self.client.get("/api/settings").get_json()
        self.assertEqual(data["settings"]["phone"], "0312345678")
        self.assertEqual(data["settings"]["sensor_name"], "寝室")
        self.assertEqual(data["display_name"], "お父さま")

    def test_settings_invalid_rejected(self):
        self.assertEqual(self.save_settings({"who": "となりの人"}).status_code, 400)
        self.assertEqual(self.save_settings({"phone": "0" * 16}).status_code, 400)
        self.assertEqual(self.save_settings({"phone": "09+0"}).status_code, 400)
        self.assertEqual(self.save_settings({"sensor_name": "あ" * 21}).status_code, 400)
        self.assertEqual(self.save_settings({"name": 123}).status_code, 400)
        self.assertEqual(self.client.post("/api/settings", data="x").status_code, 400)
        self.assertEqual(server.settings, server.DEFAULT_SETTINGS)
        self.assertFalse(os.path.exists(self.settings_path))

    def test_line_message_uses_name_and_place(self):
        self.save_settings({"who": "お父さま", "sensor_name": "リビング"})
        self.post({"motion": True}, 1000)
        self.post({"motion": False}, 1030)
        server.check_once(1030)
        self.assertIn("お父さま（リビング）の様子", self.sent[0])

        self.save_settings({"sensor_name": ""})
        self.post({"motion": True}, 1040)
        server.check_once(1040)
        self.assertIn("お父さまの動きを検知しました", self.sent[1])

    def test_family_page(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("ミマモリンク".encode(), res.data)
        res.close()


if __name__ == "__main__":
    unittest.main()
