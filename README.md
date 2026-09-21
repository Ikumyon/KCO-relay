# KCO Relay

## ビルド済みバイナリ

[Releases](https://github.com/Ikumyon/KCO-relay/releases) のAssetsからダウンロードしてください。Release公開時にタグのソースをビルドし、各OSのビルド完了後に配布ファイルを直接添付します。

- `kco-relay-windows-x86_64.zip`: 展開して `kco-relay.exe` を起動します。
- `kco-relay-linux-x86_64.tar.gz`: 展開して `./kco-relay` を起動します。tar形式で実行権限を保持しています。

Linux版はUbuntu 22.04のx86_64環境でビルドします。GUIのある環境と、D-Bus、Fontconfig、XKBなどの共有ライブラリが必要です。完全な静的リンクバイナリではありません。トレイ表示にはStatusNotifierItem対応のデスクトップ環境が必要です。

## GitHubでビルドする

GitHubでReleaseを公開すると、**Build release binaries** がWindows版とLinux版をビルドして、そのReleaseのAssetsへアップロードします。

既存Releaseをビルドする場合は、[Actions](https://github.com/Ikumyon/KCO-relay/actions/workflows/build.yml) の **Build release binaries → Run workflow** でブランチに `main`、`tag` に対象のタグ（例: `v1.1.0`）を指定してください。同名の配布ファイルは再ビルドした内容に置き換えます。

依存バージョンは `Cargo.lock` を使用し、両OSで `cargo build --release --locked` を実行します。
