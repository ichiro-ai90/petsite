import asyncio, json
from playwright.async_api import async_playwright
B = "https://petnavi.niconote.jp/"
async def main():
    out = {}
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await b.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        errs = []; pg.on("pageerror", lambda e: errs.append(str(e)))
        r = await pg.goto(B + "travel/gd/gunma.json"); out["gd_status"] = r.status
        await pg.goto(B + "travel.html", wait_until="load"); await pg.wait_for_timeout(2500)
        out["h1"] = await pg.inner_text(".tv-t h1")
        await pg.screenshot(path="shots/q_top.png")
        await pg.evaluate("document.getElementById('guide').scrollIntoView()"); await pg.wait_for_timeout(1200)
        await pg.screenshot(path="shots/q_cards.png")
        await pg.click(".tv-glb[data-a='群馬県']"); await pg.wait_for_timeout(4000)
        out["sel_visible"] = await pg.evaluate("!document.getElementById('tvsel').hidden")
        await pg.screenshot(path="shots/q_hotel.png")
        await pg.click("#tvtabs button[data-t='s']"); await pg.wait_for_timeout(2500)
        out["spots_n"] = await pg.evaluate("document.querySelectorAll('#tvgi li').length")
        await pg.evaluate("document.getElementById('tvsel').scrollIntoView()"); await pg.wait_for_timeout(1500)
        await pg.screenshot(path="shots/q_spots.png")
        await pg.click("#tvtabs button[data-t='d']"); await pg.wait_for_timeout(1500)
        out["shops_n"] = await pg.evaluate("document.querySelectorAll('#tvgi li').length")
        await pg.goto(B + "travel-shiga.html", wait_until="load"); await pg.wait_for_timeout(2000)
        await pg.evaluate("document.getElementById('dining').scrollIntoView()"); await pg.wait_for_timeout(6000)
        out["ig"] = await pg.evaluate("[...document.querySelectorAll('.tg-ig')].slice(0,2).map(b=>[b.clientWidth,b.clientHeight,b.firstChild&&b.firstChild.style.transform])")
        await pg.screenshot(path="shots/q_ig.png")
        await pg.goto(B + "submit.html", wait_until="load"); await pg.wait_for_timeout(1500)
        await pg.fill("input[name=name]", "テスト（送信しない確認）"); await pg.select_option("select[name=pref]", "群馬県"); await pg.fill("input[name=url]", "https://example.com/")
        # 送信は本番の issue を作りうるため、トークン未設定時の応答だけ API で確認
        out["submit_api"] = await pg.evaluate("fetch('/api/submit',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({hp:'x'})}).then(r=>r.status+' '+r.headers.get('content-type')).catch(e=>String(e))")
        await pg.screenshot(path="shots/q_form.png")
        out["errs"] = errs
        await b.close()
    json.dump(out, open("shots/prod2.json", "w"), ensure_ascii=False, indent=1); print(out)
asyncio.run(main())
