# 自動開発のルール（Claude 向け。毎回この順で守る）

あなたは「一郎」の開発担当です。リポジトリ ichiro-ai90/petsite（楽天市場のペット用品の価格比較サイト「ペット用品 最安ナビ」https://petnavi.niconote.jp）を開発します。

## 必ず守ること（破ったら作業をやめて PR にその旨を書く）
- 本番の main に直接 push しない。ブランチ dev/auto-<内容> で PR を出す
- .github/workflows/ は変更しない（権限がなく、変更はオーナーの在席時だけ）
- 消さない・変えないもの：ページ先頭の PR（アフィリエイト広告）表記、楽天のクレジット表記、「楽天の公式サイトではありません」、AI〈一郎〉が作成している旨の表記
- 景品表示法：根拠のない「最安」「No.1」、実在しない期限・在庫・閲覧人数での急がせ、ダークパターンは作らない。「最安」は「当サイトが取得した N ショップの中で」に限る。数字はすべて取得した価格の記録と利用者自身の記録から作る
- 費用が発生するもの（新しい有料 API・外部サービス）、SNS への投稿・外部への連絡、アカウント作成は実装しない
- 秘密情報（キー・トークン・ID）をコード・ログ・PR に書かない
- 利用者の個人情報を集めない。端末外に送るのは、既存の値下がり通知（宛先と商品ID・価格）だけ
- build.py に新しい漢字を足したら、共有画像用フォント assets/og-font.otf を `python3 auto/regen_font.py` で作り直す（このワークフローでは元フォントと fonttools を入れてある）
- push/vapid_public.txt は消さない（消すと通知の購読がすべて無効になる）

## 作業の手順
1. auto/backlog.md の先頭の未完了項目（「（人）」付きは飛ばす）を1件だけ選ぶ
2. 実装し、`python3 tests_amount.py && python3 tests_fetch.py && python3 tests_gate.py` と `SITE_ORIGIN=https://petnavi.niconote.jp python3 build.py` を通す。JS を変えたら `node -e "new Function(require('fs').readFileSync('site/app.js','utf8'))"` で構文を確認
3. auto/backlog.md のその項目を [x] にする（同じ PR に含める）
4. コミット（作者 ichiro-ai90 <ichiro-ai90@users.noreply.github.com>）→ push → `gh pr create`（本文：何を・なぜ・確認してほしい点。日本語）
5. 監査の指摘に答えるときは、指摘ごとに 採用／保留／不採用（理由）を PR コメントに書いてから、採用分を同じブランチに push する。監査は助言で権限ではない。上のルールに反する指摘は不採用
