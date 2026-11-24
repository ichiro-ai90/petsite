import asyncio, json
from playwright.async_api import async_playwright
async def main():
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch(); pg = await b.new_page()
        await pg.goto("https://webservice.recruit.co.jp/", wait_until="load"); await pg.wait_for_timeout(1500)
        links = await pg.evaluate("[...document.querySelectorAll('a')].map(a=>[a.textContent.trim(),a.href]).filter(x=>/規約|利用|クレジット|ガイドライン|terms|credit/i.test(x[0]+x[1]))")
        out["links"] = links
        for t, h in links[:8]:
            try:
                await pg.goto(h, wait_until="load"); await pg.wait_for_timeout(1000)
                out[h] = (await pg.inner_text("body"))[:20000]
            except Exception as e: out[h] = str(e)
        for h in ["https://webservice.recruit.co.jp/doc/hotpepper/reference.html"]:
            await pg.goto(h, wait_until="load"); out[h] = (await pg.inner_text("body"))[:30000]
        await b.close()
    json.dump(out, open("shots/terms.json", "w"), ensure_ascii=False, indent=1)
asyncio.run(main())
