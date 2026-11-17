#!/usr/bin/env python3
"""Screenshots and a walkthrough recording of the app, captured from a URL.

The service sits behind the platform login, so a reader of anything written about
this project cannot open it. These files are what they see instead, and they are
captured by script for the same reason the figures are registered: a refresh
changes what is on screen, and a hand-made screenshot goes stale silently.

Needs Playwright, which is not a dependency of the app; run it through uv:

    python app/main.py --port 8811 &
    uv run --with playwright python scripts/capture_media.py --base http://127.0.0.1:8811/

Writes to docs/img/article/:
  class-tariff.png     a class question: no detectable effect, portfolio range faded
  event-abqaiq.png     one shock, measured: detectable, portfolio beside the S&P
  supply-paths.png     the same day's supply paths, cropped: Hormuz -> helium -> TSMC
  event-tankers-2026.png  the newest supply scare: equities rotate, oil does not
  plain-english.png    a question typed in English, read into the form, then asked
  about.png            the explainer page, figures read live
  walkthrough.mp4      ~70 s: the four demo questions, then the 2026 day (needs ffmpeg)
"""
from __future__ import annotations

import argparse
import asyncio
import pathlib
import shutil
import subprocess
import tempfile

REPO = pathlib.Path(__file__).resolve().parent.parent
TANKERS = "2026-10-22"


async def settle(page, pause_ms: int = 1500) -> None:
    """Wait for the answer, not for a fixed time: class questions take seconds."""
    await page.wait_for_selector("#pfPanel", state="visible", timeout=180_000)
    await page.wait_for_load_state("networkidle", timeout=180_000)
    await page.wait_for_timeout(pause_ms)


async def pick_day(page, day: str) -> None:
    await page.evaluate("document.getElementById('pfPanel').style.display='none'")
    await page.select_option("#episode", day)
    await page.click("#goEvent")


async def stills(browser, base: str, out: pathlib.Path) -> None:
    page = await browser.new_page(viewport={"width": 1480, "height": 1100},
                                  device_scale_factor=2)
    for name, preset in (("class-tariff", "tariff"), ("event-abqaiq", "abqaiq")):
        await page.goto(f"{base}?preset={preset}")
        await settle(page)
        await page.screenshot(path=str(out / f"{name}.png"), full_page=True)
        print(f"  {name}.png")
        if preset == "abqaiq":
            await page.locator("#pathPanel").screenshot(path=str(out / "supply-paths.png"))
            print("  supply-paths.png")
    await pick_day(page, TANKERS)
    await settle(page)
    await page.screenshot(path=str(out / "event-tankers-2026.png"), full_page=True)
    print("  event-tankers-2026.png")
    await page.goto(base)
    await settle(page)
    await page.evaluate("document.getElementById('pfPanel').style.display='none'")
    await page.fill("#nlText", "What if the Houthis attack Saudi Aramco?")
    await page.click("#nlGo")
    await page.wait_for_selector("#nlConfirm", timeout=60_000)
    await page.click("#nlConfirm")
    await settle(page)
    await page.screenshot(path=str(out / "plain-english.png"), full_page=True)
    print("  plain-english.png")
    await page.goto(f"{base}about")
    await page.wait_for_load_state("networkidle", timeout=60_000)
    await page.wait_for_timeout(1000)
    await page.screenshot(path=str(out / "about.png"), full_page=True)
    print("  about.png")
    await page.close()


async def walkthrough(browser, base: str, out: pathlib.Path) -> None:
    if not shutil.which("ffmpeg"):
        print("  walkthrough skipped — ffmpeg not found")
        return
    with tempfile.TemporaryDirectory() as tmp:
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                        record_video_dir=tmp,
                                        record_video_size={"width": 1440, "height": 900})
        page = await ctx.new_page()
        await page.goto(base)
        await settle(page, 2500)
        for preset in ("tariff", "houthi", "abqaiq", "pricewar"):
            await page.evaluate("document.getElementById('pfPanel').style.display='none'")
            await page.click(f'.presets button[data-preset="{preset}"]')
            await settle(page, 6000)
            await page.mouse.wheel(0, 500)          # show the portfolio panel
            await page.wait_for_timeout(3500)
            await page.mouse.wheel(0, -500)
        await pick_day(page, TANKERS)
        await settle(page, 6000)
        await page.mouse.wheel(0, 500)
        await page.wait_for_timeout(5000)
        video = await page.video.path()
        await ctx.close()
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video,
                        "-vf", "scale=1280:-2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        "-movflags", "+faststart", str(out / "walkthrough.mp4")], check=True)
        print("  walkthrough.mp4")


async def main_async(args) -> None:
    from playwright.async_api import async_playwright
    out = REPO / args.out
    out.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        # The installed Chrome, so no separate browser download is needed.
        browser = await p.chromium.launch(channel=args.channel)
        await stills(browser, args.base, out)
        if not args.no_video:
            await walkthrough(browser, args.base, out)
        await browser.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="http://127.0.0.1:8811/")
    ap.add_argument("--out", default="docs/img/article")
    ap.add_argument("--channel", default="chrome")
    ap.add_argument("--no-video", action="store_true")
    asyncio.run(main_async(ap.parse_args()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
