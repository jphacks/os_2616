// ミマモリンク M5GO 設定ファイル
// 書き込む前に、このファイルの値を自分の環境に合わせて書き換えてください。
#pragma once

// ===== Wi-Fi =====
// M5GO は 2.4GHz の Wi-Fi にだけ接続できます（5GHz は不可）。
#define WIFI_SSID "iPhone"
#define WIFI_PASSWORD "arigatou"

// ===== PCのサーバー =====
// 192.168.0.10 の部分を、PCで ipconfig を実行して調べた「IPv4 アドレス」に書き換えます。
// :5000/motion はそのまま残してください。
#define SERVER_URL "http://172.20.10.12:5000/motion"

// server.py で環境変数 MIMAMO_DEVICE_TOKEN を設定した場合は、同じ文字列を入れます。
// 設定していない場合は "" のままにします。
#define DEVICE_TOKEN ""

// ===== PIRセンサー =====
// Unit PIR を PORT B（黒）につないだときの信号ピン。
// 根拠：Unit PIR は白線が信号、M5GO v2.7 の PORT B は白線が GPIO36（公式サンプルも 36）。
#define PIR_PIN 36

// ===== 送信間隔（ミリ秒） =====
// 動きがなくても、この間隔で「動きなし」を送ります（通信が生きている合図）。
#define HEARTBEAT_MS 10000
// 動き続けているときに送りすぎないための間隔。
#define MOTION_INTERVAL_MS 2000
