from server import send_line

# LINE_CHANNEL_ACCESS_TOKEN と LINE_USER_ID を環境変数に設定してから実行する
if send_line("ミマモリンク テスト通知：PCからLINEへ送信できました！"):
    print("LINE通知の送信成功！")
else:
    print("LINE通知の送信失敗（上のログを確認してください）")
