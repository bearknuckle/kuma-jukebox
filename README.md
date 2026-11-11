# Boardgame Tutorial

ボードゲームのルールを、順番に学びながら理解できるブラウザチュートリアルのプロジェクトです。

## 現在のゲーム

- **ニムト男爵**：ルールチュートリアル・CPU対戦
- **アクワイア**：基本ルールの解説チュートリアル・理解度チェック

## 主な方針

- スマートフォンで遊びやすいレスポンシブUI
- トップ画面のプルダウンからゲームを選択
- ゲームごとに独立したHTMLで管理
- ゲームを順次追加できる構成

## ディレクトリ構成

```text
boardgame-tutorial/
├── README.md
├── app.py
└── baron-nimmt/
    ├── home.html       # ゲーム一覧トップ
    ├── index.html      # ニムト男爵のモード選択
    ├── draft.html      # ニムト男爵チュートリアル
    ├── cpu.html        # ニムト男爵CPU対戦
    └── acquire.html    # アクワイア基本ルールチュートリアル
```

## URL

- トップ画面：`/`
- ニムト男爵：`/baron-nimmt`
- アクワイア：`/acquire`

## 公開期間の制御

現在は期間制限を無効にし、通常アクセスで閲覧できる設定です。

期間制限を再度有効にする場合は、Renderの環境変数に `PUBLIC_WINDOW_ENABLED=true` を設定し、`PUBLIC_START_JST` と `PUBLIC_END_JST` に日本時間の開始・終了日時を設定します。
