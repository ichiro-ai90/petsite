"""値下がり通知（Web プッシュ）の準備。毎朝の更新処理で build の前に1回だけ動く。
- Cloudflare の KV（保存場所）を使えるか確かめ、使えなければ何もしないで終わる（通知ボタンも出ない）
- 初回だけ VAPID 鍵を作り、秘密鍵は Cloudflare Pages の secret にだけ入れる（リポジトリ・ログに出さない）。公開鍵は push/vapid_public.txt に保存
- 毎回、送信用の使い捨てトークン SEND_TOKEN を作って secret に入れ、wrangler.toml（KV の割り当て）を書き出す
出力: GITHUB_ENV に PUSH=1 と SEND_TOKEN（マスク済み）"""
import base64, json, os, re, secrets, subprocess, sys

PROJ = os.environ.get("CF_PROJECT") or "petsite-ichiro"
HERE = os.path.dirname(os.path.abspath(__file__))
W = ["npx", "--yes", "wrangler@3"]


def run(args, inp=None):
    p = subprocess.run(W + args, input=inp, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def env_out(k, v):
    with open(os.environ.get("GITHUB_ENV", os.devnull), "a") as f:
        f.write(f"{k}={v}\n")


def secret(name, value):
    rc, out = run(["pages", "secret", "put", name, "--project-name", PROJ], inp=value)
    if rc:
        sys.exit(f"secret {name} の登録に失敗しました: {out[-300:]}")


def main():
    rc, out = run(["kv", "namespace", "list"])
    m = re.search(r"\[.*\]", out, re.S)
    if rc or not m:
        print("通知は無効のまま（KV を使う権限がありません）")
        return 0
    ns = [x for x in json.loads(m.group(0)) if x.get("title") == "petsite-push"]
    kid = ns[0]["id"] if ns else ""
    if not kid:
        rc, out = run(["kv", "namespace", "create", "petsite-push"])
        f = re.search(r'id\s*=\s*"([0-9a-f]{32})"', out)
        if rc or not f:
            print("通知は無効のまま（KV を作れませんでした）")
            return 0
        kid = f.group(1)
    pub_file = os.path.join(HERE, "vapid_public.txt")
    if not os.path.exists(pub_file):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives import serialization
        b = lambda n: base64.urlsafe_b64encode(n).rstrip(b"=").decode()
        k = ec.generate_private_key(ec.SECP256R1())
        nums = k.private_numbers()
        x, y = nums.public_numbers.x.to_bytes(32, "big"), nums.public_numbers.y.to_bytes(32, "big")
        jwk = {"kty": "EC", "crv": "P-256", "d": b(nums.private_value.to_bytes(32, "big")), "x": b(x), "y": b(y), "ext": True}
        pub = b(k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))
        secret("VAPID_PRIVATE", json.dumps(jwk))  # 秘密鍵は Cloudflare にだけ。先に入れてから公開鍵を保存する
        secret("VAPID_PUBLIC", pub)
        open(pub_file, "w").write(pub + "\n")
        print("VAPID 鍵を作りました（公開鍵のみ push/vapid_public.txt）")
    tok = secrets.token_urlsafe(32)
    secret("SEND_TOKEN", tok)
    print(f"::add-mask::{tok}")
    env_out("SEND_TOKEN", tok)
    env_out("PUSH", "1")
    open(os.path.join(os.path.dirname(HERE), "wrangler.toml"), "w").write(
        f'name = "{PROJ}"\npages_build_output_dir = "site"\ncompatibility_date = "2026-11-01"\n\n[[kv_namespaces]]\nbinding = "SUBS"\nid = "{kid}"\n')
    print("通知の準備ができました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
