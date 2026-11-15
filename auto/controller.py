"""自動開発の司令塔（Claude を呼ぶかどうかを決める。Claude 自身は呼ばない）。
mode を GITHUB_OUTPUT に出す: new（次の項目を作る）／fix（監査に答える）／none（何もしない）
合格した PR のマージと、監査の起動はここで直接行う（Claude の利用枠を使わない）"""
import datetime, json, os, re, subprocess, sys, urllib.request

REPO = os.environ.get("GITHUB_REPOSITORY", "ichiro-ai90/petsite")
TOK = os.environ["GITHUB_TOKEN"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "<!-- ichiro-audit round="


def gh(path, method="GET", body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{REPO}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {TOK}", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read()
        return json.loads(t) if t else {}


def out(**kw):
    with open(os.environ.get("GITHUB_OUTPUT", os.devnull), "a") as f:
        for k, v in kw.items():
            f.write(f"{k}={v}\n")
    print(kw)


def ts(s):
    return datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))


def safe_to_merge(n):
    """マージ前の機械チェック：PR の版でテスト（PR 表記・楽天クレジット・ガード等は tests_gate.py が全ページで確認）を通し、
    ワークフローの変更・通知の公開鍵の削除が無いことを確かめる"""
    sh = lambda c, cwd=ROOT: subprocess.run(c, shell=True, cwd=cwd, capture_output=True, text=True)
    d = f"/tmp/auto-pr-{n}"
    if sh(f"git fetch -q origin pull/{n}/head:auto-pr-{n} && git worktree add -f {d} auto-pr-{n}").returncode:
        return False, "PR の取得に失敗"
    names = sh(f"git diff --name-status origin/main...auto-pr-{n}").stdout
    if re.search(r"^\S+\s+\.github/workflows/", names, re.M):
        return False, "ワークフローの変更を含む"
    if re.search(r"^D\s+push/vapid_public\.txt", names, re.M):
        return False, "通知の公開鍵を消している"
    t = sh("python3 tests_amount.py && python3 tests_fetch.py && python3 tests_gate.py", cwd=d)
    if t.returncode:
        return False, "テスト不合格: " + (t.stdout + t.stderr)[-300:]
    return True, ""


def main():
    now = datetime.datetime.now(datetime.timezone.utc)
    if os.path.exists(os.path.join(ROOT, "auto", "STOP")):
        print("auto/STOP があるので止めています")
        return out(mode="none", pr="")
    prs = [p for p in gh("pulls?state=open&per_page=50") if p["head"]["ref"].startswith("dev/auto-")]
    if prs:
        p = sorted(prs, key=lambda x: x["number"])[0]
        n = p["number"]
        cs = gh(f"issues/{n}/comments?per_page=100")
        audits = [c for c in cs if c["body"].startswith(MARK)]
        if not audits:
            if now - ts(p["created_at"]) > datetime.timedelta(minutes=30):
                gh("actions/workflows/audit.yml/dispatches", "POST", {"ref": "main", "inputs": {"pr": str(n)}})
                print("監査が始まっていなかったので起動しました")
            return out(mode="none", pr=n)
        last = audits[-1]
        m = re.match(r"<!-- ichiro-audit round=(\d+) verdict=(\w+)", last["body"])
        rnd, verdict = int(m.group(1)), m.group(2)
        replies = [c for c in cs if not c["body"].startswith(MARK) and ts(c["created_at"]) > ts(last["created_at"])]
        if verdict == "pass":
            ok, why = safe_to_merge(n)
            if not ok:
                gh(f"issues/{n}/labels", "POST", {"labels": ["needs-taku"]})
                gh(f"issues/{n}/comments", "POST", {"body": f"自動開発：監査は合格したが、マージ前の機械チェックで止めました（{why}）。オーナーの判断を待ちます（needs-taku）。"})
                return out(mode="none", pr=n)
            gh(f"pulls/{n}/merge", "PUT", {"merge_method": "squash", "commit_title": f"{p['title']} (#{n})"})
            gh("actions/workflows/update.yml/dispatches", "POST", {"ref": "main"})
            print(f"PR #{n} は監査合格のためマージし、公開を起動しました")
            return out(mode="none", pr=n)
        if verdict in ("limit", "skipped", "unparsed") or rnd >= 3 and verdict != "pass":
            if not any("needs-taku" == l["name"] for l in p.get("labels", [])):
                gh(f"issues/{n}/labels", "POST", {"labels": ["needs-taku"]})
                gh(f"issues/{n}/comments", "POST", {"body": "自動開発：監査が上限に達したか合格しなかったため、自動では進めません。オーナーの判断を待ちます（needs-taku）。"})
            return out(mode="none", pr=n)
        if replies:  # 回答済みで次の監査待ち
            if now - ts(replies[-1]["created_at"]) > datetime.timedelta(minutes=40):
                gh("actions/workflows/audit.yml/dispatches", "POST", {"ref": "main", "inputs": {"pr": str(n)}})
                print("次の監査が始まっていなかったので起動しました")
            return out(mode="none", pr=n)
        return out(mode="fix", pr=n, branch=p["head"]["ref"])
    recent = [p for p in gh("pulls?state=all&per_page=30&sort=created&direction=desc")
              if p["head"]["ref"].startswith("dev/auto-") and now - ts(p["created_at"]) < datetime.timedelta(hours=20)]
    if recent:
        print("今日はもう1件作ったので、新しい項目は明日")
        return out(mode="none", pr="")
    todo = [l for l in open(os.path.join(ROOT, "auto", "backlog.md"), encoding="utf-8") if l.startswith("- [ ] ") and "（人）" not in l]
    if not todo:
        print("やることリストが空です")
        return out(mode="none", pr="")
    return out(mode="new", pr="")


if __name__ == "__main__":
    sys.exit(main())
