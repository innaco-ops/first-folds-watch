#!/usr/bin/env python3
"""Capture peer homepages to static PNGs for the Competitor Watch boards.

Runs in GitHub Actions with Playwright's bundled Chromium. Scrolls each page so
lazy/animated sections load, dismisses cookie banners, takes a full-page shot,
and writes a first-fold (16:9) crop. A blank-guard keeps the last good image if a
capture comes back mostly white (some GPU-heavy heroes don't render headless).
Writes: shots/<domain>.png, shots/fold-<domain>.png, shots/fingerprints.json, shots/meta.json
"""
import os, json, time, sys
from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(ROOT, "shots")
os.makedirs(OUT_DIR, exist_ok=True)

SITES = [
    {"domain": "monday.com",     "url": "https://monday.com"},
    {"domain": "notion.com",     "url": "https://www.notion.com"},
    {"domain": "asana.com",      "url": "https://asana.com"},
    {"domain": "clickup.com",    "url": "https://clickup.com"},
    {"domain": "airtable.com",   "url": "https://www.airtable.com"},
    {"domain": "smartsheet.com", "url": "https://www.smartsheet.com"},
]

def dismiss_cookie_banner(page):
    for sel in ["button:has-text('Reject All, Except Strictly Necessary')",
                "button:has-text('Reject All')", "button:has-text('Reject')",
                "button:has-text('Decline')", "[aria-label='Close']",
                "button:has-text('Agree')", "button:has-text('Accept')"]:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                el.click(timeout=2000); time.sleep(1); return
        except Exception:
            pass

def capture(page, url, out, width=1920):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(url, wait_until="load", timeout=90000)
    time.sleep(5)
    dismiss_cookie_banner(page)
    h = page.evaluate("document.body.scrollHeight"); y = 0
    while y < h:
        page.evaluate(f"window.scrollTo(0,{y})")
        time.sleep(2.2)
        page.evaluate("document.querySelectorAll('video').forEach(v=>{v.muted=true;v.play&&v.play().catch(()=>{});})")
        y += 450; h = page.evaluate("document.body.scrollHeight")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)"); time.sleep(2)
    try:
        page.wait_for_function(
            "Array.from(document.images).every(i => i.complete && i.naturalWidth > 0)", timeout=15000)
    except Exception:
        pass
    page.evaluate("window.scrollTo(0,0)"); time.sleep(8)
    page.screenshot(path=out, full_page=True)
    save_fold_crop(out)

def save_fold_crop(full_png):
    try:
        from PIL import Image
        img = Image.open(full_png).convert("RGB"); w, _ = img.size
        fold_h = min(img.height, int(w * 9 / 16))
        d = os.path.dirname(full_png); base = os.path.basename(full_png)
        img.crop((0, 0, w, fold_h)).save(os.path.join(d, "fold-" + base))
    except Exception as e:
        print(f"  fold-crop failed: {e}", flush=True)

def blank_fraction(path):
    """Fraction of near-white pixels in the first fold — high = blank render."""
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB"); w, _ = img.size
        crop = img.crop((0, 0, w, min(img.height, int(w * 9 / 16)))).resize((160, 90))
        px = list(crop.getdata())
        white = sum(1 for r, g, b in px if r > 240 and g > 240 and b > 240)
        return white / len(px)
    except Exception:
        return 0.0

def fingerprint(path):
    try:
        from PIL import Image
        img = Image.open(path).convert("RGB").resize((100, 140))
        px = list(img.getdata()); s = 0
        for i in range(0, len(px), 6):
            r, g, b = px[i]; s += r + g + b
        return s
    except Exception:
        return None

def main():
    fp_path = os.path.join(OUT_DIR, "fingerprints.json")
    try: old_fp = json.load(open(fp_path))
    except Exception: old_fp = {}
    new_fp, changed, failed = {}, [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=[
            "--autoplay-policy=no-user-gesture-required",
            "--disable-features=PreloadMediaEngagementData,AutoplayIgnoreWebAudio",
            "--disable-blink-features=AutomationControlled",
            "--enable-unsafe-swiftshader",  # software WebGL so canvas heroes render headless
        ])
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 900}, device_scale_factor=1,
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        for site in SITES:
            dom, url = site["domain"], site["url"]
            out = os.path.join(OUT_DIR, f"{dom}.png")
            fold = os.path.join(OUT_DIR, f"fold-{dom}.png")
            # remember last good images in case this capture is blank
            prev_full = open(out, "rb").read() if os.path.exists(out) else None
            prev_fold = open(fold, "rb").read() if os.path.exists(fold) else None
            print(f"Capturing {dom} …", flush=True)
            page = ctx.new_page()
            try:
                capture(page, url, out)
                bf = blank_fraction(out)
                if bf > 0.9 and prev_full:           # mostly white → keep last good
                    open(out, "wb").write(prev_full)
                    if prev_fold: open(fold, "wb").write(prev_fold)
                    failed.append(dom)
                    print(f"  blank ({bf:.0%} white) — kept previous good image", flush=True)
                else:
                    nfp = fingerprint(out)
                    if nfp is not None:
                        new_fp[dom] = nfp; ofp = old_fp.get(dom)
                        if ofp is not None and abs(nfp - ofp) / max(nfp, ofp, 1) > 0.015:
                            changed.append(dom)
                    print(f"  ok ({os.path.getsize(out)//1024} KB, {bf:.0%} white)", flush=True)
            except Exception as e:
                failed.append(dom)
                if prev_full: open(out, "wb").write(prev_full)
                if prev_fold: open(fold, "wb").write(prev_fold)
                print(f"  FAILED {dom}: {e}", flush=True)
            finally:
                page.close()
        browser.close()
    for dom, v in old_fp.items(): new_fp.setdefault(dom, v)
    json.dump(new_fp, open(fp_path, "w"), indent=2)
    json.dump({"updated": int(time.time()), "changed": changed, "failed": failed},
              open(os.path.join(OUT_DIR, "meta.json"), "w"), indent=2)
    print(f"Done. changed={changed} failed={failed}", flush=True)
    if len(failed) == len(SITES): sys.exit(1)

if __name__ == "__main__":
    main()
