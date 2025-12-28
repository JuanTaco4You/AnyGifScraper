#!/usr/bin/env python3
"""
Network-tab style downloader:
- Opens the page
- Captures *network image responses* (like DevTools: Network -> Img)
- Filters to .webp / .gif
- Downloads directly from the source URL
- Max 100 downloads

Install:
  pip install playwright
  python -m playwright install chromium

Run:
  python script.py
"""

import os
import re
import time
from urllib.parse import urlparse, unquote

from playwright.sync_api import sync_playwright

MAX_DOWNLOADS = 100
HEADLESS = True

# How long to keep listening for image requests after initial load:
CAPTURE_SECONDS = 8

# Set >0 if the site lazy-loads on scroll (0 = no extra steps)
SCROLL_STEPS = 0
SCROLL_WAIT_S = 0.8

TIMEOUT_MS = 45000
EXTS = (".webp", ".gif")


def safe_filename(name: str) -> str:
    name = unquote(name or "").strip().strip(".")
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", name)
    return name or "file"


def unique_path(dirpath: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dirpath, filename)
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(dirpath, f"{base}_{n}{ext}")
        n += 1
    return candidate


def mk_out_dir(page_url: str) -> str:
    netloc = (urlparse(page_url).netloc or "site").replace(":", "_")
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(os.getcwd(), f"downloads_{netloc}_{ts}")
    os.makedirs(out, exist_ok=True)
    return out


def looks_like_target(url: str, content_type: str) -> bool:
    if not url:
        return False
    u = url.strip()
    if u.startswith(("data:", "javascript:", "#", "blob:")):
        return False

    path = urlparse(u).path.lower()
    if path.endswith(EXTS):
        return True

    ct = (content_type or "").lower()
    return ("image/webp" in ct) or ("image/gif" in ct)


def ext_from_type_or_url(url: str, content_type: str) -> str:
    path_ext = os.path.splitext(urlparse(url).path)[1].lower()
    if path_ext in EXTS:
        return path_ext

    ct = (content_type or "").lower()
    if "image/webp" in ct:
        return ".webp"
    if "image/gif" in ct:
        return ".gif"
    return ""


def main():
    page_url = input("Paste page URL: ").strip()
    if not page_url:
        print("No URL provided.")
        return

    out_dir = mk_out_dir(page_url)
    print(f"\nOutput folder:\n  {out_dir}\n")

    captured = []  # list of (url, content_type)
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()

        # Helps with hotlink protection / CDN behavior
        context.set_extra_http_headers(
            {
                "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
        )

        page = context.new_page()
        page.set_default_timeout(TIMEOUT_MS)

        def on_response(resp):
            try:
                req = resp.request
                if req.resource_type != "image":
                    return
                if not resp.ok:
                    return

                url = resp.url
                ct = (resp.headers.get("content-type") or "")
                if not looks_like_target(url, ct):
                    return
                if url in seen:
                    return

                seen.add(url)
                captured.append((url, ct))
                print(f"\rCaptured: {len(captured)}", end="", flush=True)
            except Exception:
                return

        page.on("response", on_response)

        print("[1/2] Opening page + listening for network images...")
        page.goto(page_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass

        # Keep listening a bit longer for late image loads
        t_end = time.time() + CAPTURE_SECONDS
        while time.time() < t_end and len(captured) < MAX_DOWNLOADS:
            time.sleep(0.1)

        # Optional lightweight scroll (OFF by default)
        if SCROLL_STEPS > 0 and len(captured) < MAX_DOWNLOADS:
            print("\n[2/2] Scrolling (optional) to trigger lazy-load...")
            for _ in range(SCROLL_STEPS):
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                time.sleep(SCROLL_WAIT_S)
                if len(captured) >= MAX_DOWNLOADS:
                    break

        print("\n\nDownloading (direct from source URLs)...")
        targets = captured[:MAX_DOWNLOADS]
        if not targets:
            print("No .webp or .gif found via network image responses.")
            browser.close()
            return

        ok = 0
        fail = 0

        for i, (url, ct) in enumerate(targets, 1):
            try:
                resp = context.request.get(
                    url,
                    headers={"Referer": page_url},
                    timeout=30000,
                )
                if not resp.ok:
                    raise RuntimeError(f"HTTP {resp.status}")

                body = resp.body()
                ext = ext_from_type_or_url(url, ct)
                name = safe_filename(os.path.basename(urlparse(url).path)) or f"image_{i}{ext}"
                if ext and not name.lower().endswith(ext):
                    name = f"{name}{ext}"

                save_path = unique_path(out_dir, name)
                with open(save_path, "wb") as f:
                    f.write(body)

                ok += 1
                print(f"[{i}/{len(targets)}] OK   {os.path.basename(save_path)}")
            except Exception as e:
                fail += 1
                print(f"[{i}/{len(targets)}] FAIL {url} -> {e}")

        print(f"\nDone. OK={ok} FAIL={fail}")
        browser.close()


if __name__ == "__main__":
    main()
