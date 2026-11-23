import asyncio, json
from playwright.async_api import async_playwright
B = "https://petnavi.niconote.jp/"
async def main():
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        # 1 guide
        await pg.goto(B + "travel-shiga.html", wait_until="load"); await pg.wait_for_timeout(4000)
        h = await pg.content()
        out["guide"] = {"figs": h.count("tg-f"), "cred": h.count("Wikimedia Commons"), "lic": h.count("creativecommons.org"),
                        "site": h.count("公式サイト ›"), "ig": h.count("公式Instagram ›"), "igframe": h.count("instagram.com/") ,
                        "media": sum(h.count(k) for k in ["wankonowa", "honda.co.jp", "tabelog", "hoshinoresorts"])}
        await pg.screenshot(path="shots/p_guide.png")
        await pg.evaluate("document.getElementById('dining').scrollIntoView()"); await pg.wait_for_timeout(5000)
        await pg.screenshot(path="shots/p_dining.png")
        # 2 item page
        await pg.goto(B, wait_until="load"); await pg.wait_for_timeout(2000)
        h = await pg.content()
        out["top"] = {"wet": h.count("ウェットティッシュ"), "dogcat": h.count("犬・猫"), "tvbar_visible": await pg.evaluate("(()=>{var e=document.querySelector('.tvbar');return !!(e&&e.offsetParent)})()")}
        await pg.screenshot(path="shots/p_top.png", full_page=False)
        href = await pg.evaluate("(()=>{var a=[...document.querySelectorAll('a[href^=\"item-\"]')][0];return a&&a.getAttribute('href')})()")
        if href:
            await pg.goto(B + href, wait_until="load"); await pg.wait_for_timeout(1500)
            h = await pg.content()
            import re
            out["item"] = {"href": href, "pt_num": len(re.findall(r"約[0-9,]+pt", h)), "card_new": h.count("ポイントが増える場合あり")}
        # 3 trip bar: travel page, click a hotel link without confirm, then top
        await pg.goto(B + "travel.html", wait_until="load"); await pg.wait_for_timeout(2000)
        await pg.evaluate("localStorage.removeItem('pn_trip')")
        await pg.goto(B + "travel.html#guide", wait_until="load"); await pg.wait_for_timeout(2000)
        await pg.screenshot(path="shots/p_travel.png")
        await pg.goto(B, wait_until="load"); await pg.wait_for_timeout(1500)
        out["bar_after_visit"] = await pg.evaluate("(()=>{var e=document.querySelector('.tvbar');return !!(e&&e.offsetParent)})()")
        await b.close()
    json.dump(out, open("shots/prod.json", "w"), ensure_ascii=False, indent=1); print(out)
asyncio.run(main())
