# KCO Relay

## ビルド済みバイナリ

[GitHub Actions](https://github.com/Ikumyon/KCO-relay/actions/workflows/build.yml) の成功した実行を開き、Artifacts からダウンロードしてください（GitHubへのログインが必要です）。

- `kco-relay-windows-x86_64`: 内側のZIPを展開し、`kco-relay.exe` を起動します。
- `kco-relay-linux-x86_64`: 内側の `tar.gz` を展開し、`./kco-relay` を起動します。tar形式で実行権限を保持しています。

Linux版はUbuntu 22.04のx86_64環境でビルドします。GUIのある環境と、D-Bus、Fontconfig、XKBなどの共有ライブラリが必要です。完全な静的リンクバイナリではありません。トレイ表示にはStatusNotifierItem対応のデスクトップ環境が必要です。

## GitHubでビルドする

`main`へのpush、`v`で始まるタグのpush、Pull RequestでWindows版とLinux版をビルドします。Actionsの **Build binaries → Run workflow** から手動でも実行できます。成果物の保存期間は30日です。

依存バージョンは `Cargo.lock` を使用し、両OSで `cargo build --release --locked` を実行します。
