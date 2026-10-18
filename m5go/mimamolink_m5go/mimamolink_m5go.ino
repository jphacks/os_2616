// ミマモリンク M5GO 側プログラム（M5GO IoT Kit v2.7 / Arduino IDE）
//
// Unit PIR（人感センサー）で動きを検知し、PCの server.py に HTTP POST で送ります。
//   動きあり : {"motion":true}  … 検知中は MOTION_INTERVAL_MS ごと
//   動きなし : {"motion":false} … HEARTBEAT_MS ごと（通信が生きている合図）
// 設定は config.h にまとめています。

#include <M5Unified.h>
#include <WiFi.h>
#include <HTTPClient.h>

#include "config.h"

// HTTP通信を待つ最大時間（ミリ秒）。サーバーが応答しないときにずっと止まらないようにする。
const uint16_t HTTP_TIMEOUT_MS = 5000;
// Wi-Fiが切れたときに再接続を試す間隔（ミリ秒）
const unsigned long WIFI_RETRY_MS = 10000;
// シリアルモニタに状態を記録する間隔（ミリ秒）
const unsigned long STATUS_LOG_MS = 5000;
unsigned long lastStatusLogMs = 0;

unsigned long lastSentMs = 0;        // 最後に送信を試した時刻
unsigned long lastMotionSentMs = 0;  // 最後に「動きあり」を送った時刻
unsigned long lastWifiRetryMs = 0;
bool everSent = false;               // 一度でも送信に成功したか
bool motionEverSent = false;
int lastHttpCode = 0;                // 直近の送信結果（0 = まだ送っていない）
String lastScreen;                   // 前回画面に表示した内容（変化したときだけ描き直す）
bool lastMotion = false;             // 前回のPIRの状態（シリアル記録用）
bool lastWifiConnected = false;      // 前回のWi-Fi接続状態（シリアル記録用）

// 画面の文字は英語（日本語フォントを入れると標準設定のフラッシュ容量に収まらないため）。
// 意味は README.md の「M5GOの画面の見方」に日本語で書いてある。
String httpResultText(int code) {
  if (code == 0) return "not sent yet";
  if (code == 200) return "OK (200)";
  if (code == 400) return "bad data (400)";
  if (code == 401) return "bad token (401)";
  if (code < 0) return "no PC (" + String(code) + ")";
  return "error (" + String(code) + ")";
}

void drawScreen(bool motion) {
  String wifiText = WiFi.status() == WL_CONNECTED
                        ? "OK " + WiFi.localIP().toString()
                        : "connecting...";

  String screen = String(motion ? "YES" : "no") + "|" + wifiText + "|" +
                  httpResultText(lastHttpCode);
  if (screen == lastScreen) return;
  lastScreen = screen;

  M5.Display.fillScreen(TFT_BLACK);
  M5.Display.setCursor(0, 10);
  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.println(" MimamoLink");
  M5.Display.println();

  M5.Display.setTextColor(motion ? TFT_GREEN : TFT_LIGHTGREY, TFT_BLACK);
  M5.Display.println(motion ? " Motion: YES" : " Motion: no");
  M5.Display.println();

  M5.Display.setTextColor(TFT_WHITE, TFT_BLACK);
  M5.Display.println(" WiFi: " + wifiText);
  M5.Display.println(" Send: " + httpResultText(lastHttpCode));
}

bool postMotion(bool motion) {
  lastSentMs = millis();

  if (WiFi.status() != WL_CONNECTED) {
    lastHttpCode = -1;
    return false;
  }

  HTTPClient http;
  http.setConnectTimeout(HTTP_TIMEOUT_MS);
  http.setTimeout(HTTP_TIMEOUT_MS);

  if (!http.begin(SERVER_URL)) {
    lastHttpCode = -2;
    Serial.println("SERVER_URL の書き方を確認してください");
    return false;
  }

  http.addHeader("Content-Type", "application/json");
  if (strlen(DEVICE_TOKEN) > 0) {
    http.addHeader("X-Device-Token", DEVICE_TOKEN);
  }

  // server.py の POST /motion が受け付ける形式
  int code = http.POST(motion ? "{\"motion\":true}" : "{\"motion\":false}");
  http.end();

  lastHttpCode = code;
  Serial.printf("送信 motion=%s 結果=%d\n", motion ? "true" : "false", code);

  if (code == 200) {
    everSent = true;
    return true;
  }
  return false;
}

void setup() {
  auto cfg = M5.config();
  cfg.serial_baudrate = 115200;  // シリアルモニタの速度（115200 baud）に合わせる
  M5.begin(cfg);

  M5.Display.setFont(&fonts::FreeSans12pt7b);
  M5.Display.setTextSize(1);

  pinMode(PIR_PIN, INPUT);
  Serial.printf("ミマモリンク起動 PIR_PIN=%d 送信先=%s\n", PIR_PIN, SERVER_URL);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWifiRetryMs = millis();
  Serial.println("Wi-Fiに接続しています...");
}

void loop() {
  M5.update();
  unsigned long now = millis();

  if (WiFi.status() != WL_CONNECTED && now - lastWifiRetryMs >= WIFI_RETRY_MS) {
    Serial.println("Wi-Fiに再接続します");
    WiFi.disconnect();
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
    lastWifiRetryMs = now;
  }

  bool motion = digitalRead(PIR_PIN) == HIGH;

  // 変化したときだけシリアルモニタに記録する（実機での確認用）
  if (motion != lastMotion) {
    Serial.printf("PIR(GPIO%d): %s\n", PIR_PIN, motion ? "HIGH 動きあり" : "LOW 動きなし");
    lastMotion = motion;
  }
  bool wifiConnected = WiFi.status() == WL_CONNECTED;
  if (wifiConnected != lastWifiConnected) {
    if (wifiConnected) {
      Serial.println("Wi-Fi接続: " + WiFi.localIP().toString());
    } else {
      Serial.println("Wi-Fi未接続");
    }
    lastWifiConnected = wifiConnected;
  }

  // 変化がなくても、STATUS_LOG_MS ごとに今の状態を記録する（実機での確認用）
  if (now - lastStatusLogMs >= STATUS_LOG_MS) {
    Serial.printf("状態 PIR=%d WiFi=%s 送信結果=%d\n", motion ? 1 : 0,
                  wifiConnected ? "接続" : "未接続", lastHttpCode);
    lastStatusLogMs = now;
  }

  if (motion &&
      (!motionEverSent || now - lastMotionSentMs >= MOTION_INTERVAL_MS)) {
    if (postMotion(true)) {
      motionEverSent = true;
    }
    lastMotionSentMs = now;
  }

  // 起動直後（まだ一度も成功していない間）は、2秒おきに送って早くつながるようにする
  unsigned long interval = everSent ? HEARTBEAT_MS : MOTION_INTERVAL_MS;
  bool heartbeatDue = lastSentMs == 0 || now - lastSentMs >= interval;
  if (!motion && heartbeatDue && WiFi.status() == WL_CONNECTED) {
    postMotion(false);
  }

  drawScreen(motion);
  delay(100);
}
