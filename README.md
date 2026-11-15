# Boardgame Tutorial

ボードゲームのルールを、順番に学びながら理解できるブラウザチュートリアルのプロジェクトです。

## 現在のゲーム

- **ニムト男爵**：ルールチュートリアル・CPU対戦
- **アクワイア**：基本ルール解説・理解度チェック
- **カタン**：基本セット（3〜4人用）のルール解説・理解度チェック

## 方針

- スマートフォンで遊びやすいレスポンシブUI
- トップ画面のプルダウンからゲームを選択
- ゲームごとに独立したHTMLで管理
- ルールを学ぶチュートリアルを順次追加

## 構成

```text
boardgame-tutorial/
├── README.md
├── app.py
└── baron-nimmt/
    ├── home.html
    ├── index.html
    ├── draft.html
    ├── cpu.html
    ├── acquire.html
    └── catan.html
```

## URL

- トップ：`/`
- ニムト男爵：`/baron-nimmt`
- アクワイア：`/acquire`
- カタン：`/catan`

## 公開期間の制御

現在は期間制限を無効にし、通常アクセスで閲覧できる設定です。

期間制限を再度有効にする場合は、Renderの環境変数に `PUBLIC_WINDOW_ENABLED=true` を設定し、`PUBLIC_START_JST` と `PUBLIC_END_JST` に日本時間の開始・終了日時を設定します。

## テスト

```bash
pip install -r requirements-dev.txt
pytest -q
```

Pull Request と `main` への push のたびに、GitHub Actions（`.github/workflows/test.yml`）で同じテストが自動で実行されます。
