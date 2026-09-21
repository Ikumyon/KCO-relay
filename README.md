# KCO Relay

## ビルド済みバイナリ

[Releases](https://github.com/Ikumyon/KCO-relay/releases) のAssetsからダウンロードしてください。Release公開時にタグのソースをビルドし、各OSのビルド完了後に配布ファイルを直接添付します。

- `kco-relay-windows-x86_64.zip`: 展開して `kco-relay.exe` を起動します。
- `kco-relay-linux-x86_64.tar.gz`: 展開して `./kco-relay` を起動します。tar形式で実行権限を保持しています。

Linux版はUbuntu 22.04のx86_64環境でビルドします。GUIのある環境と、D-Bus、Fontconfig、XKBなどの共有ライブラリが必要です。完全な静的リンクバイナリではありません。トレイ表示にはStatusNotifierItem対応のデスクトップ環境が必要です。
