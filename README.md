# KUMA MUSIC ジュークボックス

KUMA MUSIC の曲をボタンひとつで聴けるページ（Render ワークスペース「KUMA MUSIC JUKEBOX」で公開）。

- `jukebox/` … 静的サイト `kuma-jukebox`（ページ本体・アイコン）
- `jukebox-api/` … Web サービス `kuma-jukebox-api`（サブスク曲の一覧・動画の長さ。環境変数 `YOUTUBE_API_KEY` があれば YouTube 公式 API を使う）

このブランチ（`jukebox`）に push すると自動で反映される。試作ゲームラボ（`lab` ブランチ）とは別。
