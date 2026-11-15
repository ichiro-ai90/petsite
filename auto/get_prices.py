"""直近の価格データ（data/prices.json）を、GitHub Actions の成果物（artifact「prices」、保持1日）から取ってくる。
楽天の規約で価格情報の保存は24時間までのため、価格はリポジトリにコミットしない（11/15）。
- 取得できれば data/prices.json に書いて exit 0。24時間より古い・無い・失敗なら何もせず exit 0（呼び出し側はサンプルか新規取得で続ける）
- 必要: GITHUB_TOKEN（actions: read）、GITHUB_REPOSITORY"""
import datetime, io, json, os, sys, urllib.error, urllib.request, zipfile

REPO = os.environ.get("GITHUB_REPOSITORY", "ichiro-ai90/petsite")
TOK = os.environ.get("GITHUB_TOKEN", "")
DST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "prices.json")


def req(url):
    r = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOK}", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(r, timeout=60)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def download(url):
    """成果物の zip は保存先（別ドメイン）へ転送される。転送先に GitHub の認証ヘッダーを送ると 401 になるため、転送先は認証なしで取る"""
    op = urllib.request.build_opener(_NoRedirect)
    r = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOK}", "Accept": "application/vnd.github+json"})
    try:
        return op.open(r, timeout=60).read()
    except urllib.error.HTTPError as e:
        if e.code in (301, 302, 303, 307, 308) and e.headers.get("Location"):
            return urllib.request.urlopen(e.headers["Location"], timeout=120).read()
        raise


def main():
    if not TOK:
        print("GITHUB_TOKEN が無いため、前回の価格は使いません")
        return 0
    try:
        arts = json.load(req(f"https://api.github.com/repos/{REPO}/actions/artifacts?name=prices&per_page=10"))["artifacts"]
        arts = [a for a in arts if not a.get("expired")]
        if not arts:
            print("前回の価格（artifact）はありません")
            return 0
        a = max(arts, key=lambda x: x["created_at"])
        age = datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(a["created_at"].replace("Z", "+00:00"))
        if age > datetime.timedelta(hours=24):
            print("前回の価格は24時間を超えているため使いません")
            return 0
        z = zipfile.ZipFile(io.BytesIO(download(a["archive_download_url"])))
        data = z.read("prices.json")
        json.loads(data)
        os.makedirs(os.path.dirname(DST), exist_ok=True)
        open(DST, "wb").write(data)
        print(f"前回の価格を取得しました（{age.seconds // 3600}時間前）")
    except Exception as e:
        print("前回の価格を取得できませんでした:", str(e)[:200])
    return 0


if __name__ == "__main__":
    sys.exit(main())
