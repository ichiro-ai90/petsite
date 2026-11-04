"""ChatGPT（OpenAI API）による UI/UX・PM 監査。GitHub Actions の audit.yml から呼ぶ。標準ライブラリ＋playwright のみ。

1. site/ をローカルで配信し、スマホ幅で主要画面のスクリーンショットと本文テキストを取る
2. 監査の前提（audit/brief.md）・PR の説明・変更ファイル・前回までの監査と開発側の回答を添えて OpenAI API に送る
3. 結果を PR にコメントする（先頭に <!-- ichiro-audit round=N verdict=... --> の目印）

環境変数: OPENAI_API_KEY, OPENAI_MODEL（既定 gpt-5-mini）, GITHUB_TOKEN, GITHUB_REPOSITORY, PR_NUMBER, AUDIT_MAX_ROUNDS（既定 3）
キーが無い・上限回数に達した・API が失敗した場合も、理由を PR にコメントして exit 0（開発を止めない）。
"""
import base64
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO = os.environ.get("GITHUB_REPOSITORY", "")
PR = os.environ.get("PR_NUMBER", "")
MODEL = os.environ.get("OPENAI_MODEL") or "gpt-5-mini"
MAX_ROUNDS = int(os.environ.get("AUDIT_MAX_ROUNDS") or 3)
MARK = "<!-- ichiro-audit"


def gh(path, data=None, method=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", data=json.dumps(data).encode() if data else None,
                                 method=method or ("POST" if data else "GET"),
                                 headers={"Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def comment(body):
    print(body)
    if REPO and PR and os.environ.get("GITHUB_TOKEN"):
        gh(f"issues/{PR}/comments", {"body": body})


def history():
    """この PR の過去の監査（目印つき）と、それ以外のコメント（開発側の回答）を時系列で返す。"""
    if not (REPO and PR):
        return [], []
    cs = gh(f"issues/{PR}/comments?per_page=100")
    # 回数に数えるのは実際に監査した回だけ（スキップ・エラー・上限の通知は数えない）
    audits = [c for c in cs if c["body"].startswith(MARK) and re.match(MARK + r" round=\d+ verdict=(pass|changes|unparsed)", c["body"])]
    replies = [c for c in cs if not c["body"].startswith(MARK)]
    replies += [c for c in cs if "verdict=limit" in c["body"][:60]]
    return audits, replies


def shots():
    from playwright.sync_api import sync_playwright
    data = json.load(open(os.path.join(ROOT, "site", "search.json"), encoding="utf-8"))
    items = sorted(data["items"], key=lambda x: -x.get("rv", 0))
    first = items[0] if items else None
    pages = [("トップ", "index.html", False)]
    if first:
        pages += [("カテゴリの商品一覧", f"{first['c']}.html", False), ("商品ごとのショップ比較", f"item-{first['id']}.html", True)]
        kw = (first.get("m") or first["n"]).split(" ")[0]
        pages += [("検索結果", f"search.html?q={urllib.request.quote(kw)}", False)]
    pages += [("お気に入り（登録後）", "favorites.html", False)]
    out = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(**dict(p.devices["iPhone 13"], device_scale_factor=1))  # 画像トークンを抑える
        pg = ctx.new_page()
        if first:  # お気に入り画面を空でなく見せるため、1件登録しておく
            pg.goto("http://localhost:8000/index.html")
            pg.evaluate("id => localStorage.setItem('petsite_fav_v1', JSON.stringify({[id]: {p: 0, t: Date.now()}}))", first["id"])
        for name, path, full in pages:
            pg.goto("http://localhost:8000/" + path, wait_until="networkidle", timeout=30000)
            pg.wait_for_timeout(800)
            png = pg.screenshot(full_page=full, clip=None if not full else {"x": 0, "y": 0, "width": 390, "height": 2400})
            text = pg.inner_text("body")[:2500]
            out.append((name, path, png, text))
        b.close()
    return out


def diffstat():
    try:
        return subprocess.run(["git", "diff", "--stat", "origin/main...HEAD"], cwd=ROOT, capture_output=True, text=True).stdout[-3000:]
    except Exception:
        return "（取得できませんでした）"


def ask(prompt, images):
    content = [{"type": "input_text", "text": prompt}]
    for name, png in images:
        content.append({"type": "input_text", "text": f"↓ スクリーンショット: {name}（iPhone 13 幅）"})
        content.append({"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode()})
    schema = {"type": "object", "additionalProperties": False, "required": ["summary", "verdict", "findings", "next"], "properties": {
        "summary": {"type": "string"}, "verdict": {"type": "string", "enum": ["pass", "changes"]},
        "findings": {"type": "array", "items": {"type": "object", "additionalProperties": False,
                     "required": ["severity", "screen", "problem", "evidence", "suggestion", "effort"],
                     "properties": {"severity": {"type": "string", "enum": ["高", "中", "低"]}, "screen": {"type": "string"},
                                    "problem": {"type": "string"}, "evidence": {"type": "string"}, "suggestion": {"type": "string"},
                                    "effort": {"type": "string", "enum": ["小", "中", "大"]}}}},
        "next": {"type": "array", "items": {"type": "string"}}}}
    # JSON を必ず正しい形で返させる（第1回は文中の引用符で JSON が壊れた）
    body = {"model": MODEL, "input": [{"role": "user", "content": content}],
            "text": {"format": {"type": "json_schema", "name": "audit", "schema": schema, "strict": True}},
            "reasoning": {"effort": os.environ.get("OPENAI_REASONING") or "low"}}  # 第3回が300秒でタイムアウトしたため
    # 応答に5分以上かかり接続が切れたため（第3回）、background モードで投げて状態を取りに行く
    body.update({"background": True, "store": True})
    hdr = {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}", "Content-Type": "application/json"}
    req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(), method="POST", headers=hdr)
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.load(r)
    import time
    for _ in range(90):  # 最大15分
        if d.get("status") in ("completed", "failed", "cancelled", "incomplete"):
            break
        time.sleep(10)
        with urllib.request.urlopen(urllib.request.Request(f"https://api.openai.com/v1/responses/{d['id']}", headers=hdr), timeout=60) as r:
            d = json.load(r)
    if d.get("status") != "completed":
        raise RuntimeError(f"status={d.get('status')} {json.dumps(d.get('error') or d.get('incomplete_details'), ensure_ascii=False)[:200]}")
    text = d.get("output_text") or "".join(c.get("text", "") for o in d.get("output", []) if o.get("type") == "message"
                                          for c in o.get("content", []))
    return text, d.get("usage", {})


def parse(text):
    m = re.search(r"\{.*\}", text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except ValueError:
        return None


def esc(s):
    return str(s or "").replace("|", "／").replace("\n", " ")


def main():
    audits, replies = history()
    rnd = len(audits) + 1
    if rnd > MAX_ROUNDS:
        if not any("verdict=limit" in c["body"] for c in replies + audits):
            comment(f"{MARK} round={rnd} verdict=limit -->\n## 監査（ChatGPT）\nこの PR の監査は上限の {MAX_ROUNDS} 回に達したため、実行しませんでした。マージするかどうかは Takuさんの判断を待ちます。")
        return 0
    if not os.environ.get("OPENAI_API_KEY"):
        comment(f"{MARK} round={rnd} verdict=skipped -->\n## 監査（ChatGPT）\nOPENAI_API_KEY が未設定のため監査をスキップしました（リポジトリの Settings → Secrets and variables → Actions に登録すると動きます）。")
        return 0
    pr = gh(f"pulls/{PR}") if PR else {"title": "", "body": ""}
    caps = shots()
    past = ""
    for c in audits[-2:]:
        past += "\n--- 前回の監査 ---\n" + c["body"][:4000]
    for c in replies[-3:]:
        past += "\n--- 開発側の回答 ---\n" + c["body"][:3000]
    prompt = (open(os.path.join(ROOT, "audit", "brief.md"), encoding="utf-8").read()
              + f"\n\n■ 今回の変更（PR #{PR}: {pr.get('title', '')}）\n{(pr.get('body') or '')[:3000]}\n\n■ 変更ファイル\n{diffstat()}"
              + f"\n\n■ これまでの監査とやりとり（第{rnd}回の監査です）{past or '（初回）'}"
              + "\n\n■ 各画面の本文テキスト（先頭部分）\n" + "\n".join(f"### {n}（{p}）\n{t}" for n, p, _, t in caps))
    try:
        text, usage = ask(prompt, [(n, png) for n, _, png, _ in caps])
    except Exception as e:
        body = getattr(e, "read", lambda: b"")().decode("utf-8", "replace")[:300] if hasattr(e, "read") else ""
        comment(f"{MARK} round={rnd} verdict=error -->\n## 監査（ChatGPT）\nOpenAI API の呼び出しに失敗しました: {type(e).__name__} {e} {body}")
        return 0
    r = parse(text)
    tok = f"モデル {MODEL}・入力 {usage.get('input_tokens', '?')} / 出力 {usage.get('output_tokens', '?')} トークン"
    if not r:
        comment(f"{MARK} round={rnd} verdict=unparsed -->\n## 監査（ChatGPT）第{rnd}回\n（JSON として読めなかったため原文のまま）\n\n{text[:6000]}\n\n<sub>{tok}</sub>")
        return 0
    v = r.get("verdict") if r.get("verdict") in ("pass", "changes") else ("changes" if any(f.get("severity") == "高" for f in r.get("findings", [])) else "pass")
    rows = "\n".join(f"| {i} | {esc(f.get('severity'))} | {esc(f.get('screen'))} | {esc(f.get('problem'))} | {esc(f.get('evidence'))} | {esc(f.get('suggestion'))} | {esc(f.get('effort'))} |"
                     for i, f in enumerate(r.get("findings", []), 1))
    nxt = "\n".join(f"{i}. {esc(x)}" for i, x in enumerate(r.get("next", []), 1))
    comment(f"{MARK} round={rnd} verdict={v} -->\n## 監査（ChatGPT）第{rnd}回: {'合格（高の指摘なし）' if v == 'pass' else '要修正'}\n\n{r.get('summary', '')}\n\n"
            f"| # | 重要度 | 画面 | 問題 | 根拠 | 改善案 | 手間 |\n|---|---|---|---|---|---|---|\n{rows}\n\n**次にやるべきこと**\n{nxt}\n\n"
            f"<sub>{tok}。開発側（一郎）は各指摘に 採用／保留／不採用（理由）で回答し、採用分を同じ PR に push します。</sub>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
