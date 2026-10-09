"""Frame-accurate capture of the real UI replaying a recorded run.

The page runs on a virtual clock (?capture): every frame is stepped from here, screenshotted,
and piped to ffmpeg, so there are no dropped or duplicated frames.

  python demo/capture.py --stills 2,9,20,34,43 --out /tmp/stills
  python demo/capture.py --video demo/weaver-demo-45s.mp4
"""
import argparse
import os
import subprocess
import sys

from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:8766/film.html")
ap.add_argument("--seconds", type=float, default=45.0)
ap.add_argument("--fps", type=int, default=30)
ap.add_argument("--stills", default="")
ap.add_argument("--out", default="stills")
ap.add_argument("--video", default="")
args = ap.parse_args()

frames = int(round(args.seconds * args.fps))
stills = {int(round(float(s) * args.fps)): s for s in args.stills.split(",") if s}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
    page.on("console", lambda m: print("console:", m.text, file=sys.stderr) if m.type == "error" else None)
    page.on("pageerror", lambda e: print("pageerror:", e, file=sys.stderr))
    page.goto(args.url)
    page.wait_for_function("window.__ready === true", timeout=20000)
    fonts = page.evaluate("""() => ({
        body: getComputedStyle(document.body).fontFamily,
        serif: document.fonts.check('16px "CMU Serif"'), italic: document.fonts.check('italic 16px "CMU Serif"'),
        mono: document.fonts.check('16px "CMU Typewriter Text"'),
        loaded: [...document.fonts].filter(f => f.status === 'loaded').map(f => f.family + ' ' + f.style + ' ' + f.weight)})""")
    print("fonts:", fonts, file=sys.stderr)
    ff = None
    if args.video:
        ff = subprocess.Popen(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(args.fps), "-c:v", "png", "-i", "-",
             "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "14", "-pix_fmt", "yuv420p", "-r", str(args.fps),
             "-movflags", "+faststart", args.video], stdin=subprocess.PIPE)
    if stills:
        os.makedirs(args.out, exist_ok=True)
    for i in range(frames):
        page.evaluate("ms => window.__frame(ms)", i * 1000.0 / args.fps)
        if ff:
            ff.stdin.write(page.screenshot(type="png"))
        elif i in stills:
            page.screenshot(path=os.path.join(args.out, f"t{stills[i]}.png"))
        if not ff and stills and i > max(stills):
            break
    if ff:
        ff.stdin.close()
        ff.wait()
    browser.close()
