# 試作ゲームラボ 作業ルール（lab ブランチ）

このブランチは「試作ゲームラボ」（https://game-lab-oj9t.onrender.com）の置き場。
複数の会話から同時に更新されるので、**他の人の変更を消さない**ことを最優先にする。

## 構成
- `lab/` … Render の静的サイト `game-lab` が公開するフォルダ。push すると自動で反映される
  - `lab/index.html` … ホーム画面。ゲーム一覧は `GAMES`、カテゴリは `CATS`
  - `lab/<ゲームID>/index.html` … 各ゲーム（1ファイル完結）
  - `lab/icon.svg` / `lab/apple-touch-icon.png` / `lab/og.png` … アイコンとリンクプレビュー画像
- `lab-api/` … 遊んだ回数カウンター（Render の Web サービス `game-lab-api`）。lab ブランチへの push で再デプロイされる
- `tools/make_og.py` … `lab/og.png` を作り直すスクリプト
- `main` ブランチは別プロジェクト（ニムト男爵）。**main には push しない**

## 編集する前に必ず
1. `git fetch origin lab` して、最新の `origin/lab` の上で作業する
2. 古いコピーや会話の中に残っているファイルの中身で、ファイルを丸ごと上書きしない
   - 丸ごと書き直すときは、必ず最新のファイルを読んでから、下の「消してはいけないもの」を残す
3. push が拒否されたら `git fetch` → `git rebase origin/lab` で取り込み直す。**force push はしない**

## 消してはいけないもの（各ゲームの index.html）
- `<!-- 試作ゲームラボ: ... -->` のコメントと、その下の OG タグ（`og:title` `og:image` など）
- `icon.svg` / `apple-touch-icon.png` を読む `<link rel="icon">` `<link rel="apple-touch-icon">`
- 「← 試作ゲームラボに戻る」（または ⌂）など `href="../"` のリンク
- カンパイ！の 20歳以上・飲みすぎ注意の一文

## ゲームを追加するとき
1. `lab/<ゲームID>/index.html` を作る（IDは英小文字・数字・ハイフン）
2. 上の「消してはいけないもの」を入れる（既存ゲームからコピーしてタイトルと説明だけ変える）
3. `lab/index.html` の `GAMES` に1件追加（`genre` は `CATS` の id と同じにする）
4. 新しいカテゴリを作ったら `CATS` に追加し、`python3 tools/make_og.py` で `lab/og.png` を作り直す
5. コミットメッセージは日本語で「ゲーム名: 変更内容」の形にする

## 文言のルール
- カテゴリの説明は「AIと考えた試作ゲーム」（「オリジナル」「AIが考えた」ではない）
- ホームに「感想を送ってください」の類の呼びかけは入れない
