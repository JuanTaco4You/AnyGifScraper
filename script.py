#!/usr/bin/env python3
"""
Download up to 100 .webp/.gif files from a webpage the way your browser sees them.
- Renders JS (Playwright)
- Scrolls a bit to trigger lazy-load
- Collects final URLs from DOM + CSS background images
- Downloads using the browser session (cookies/headers), like "Save image as"

Install:
  pip install playwright
  python -m playwright install chromium

Run:
  python grab_webp_gif_browser.py
"""

import os
import re
import time
from urllib.parse import urlparse, unquote

MAX_DOWNLOADS = 100
HEADLESS = True        # set False to watch the browser
SCROLL_STEPS = 10      # bump if page lazy-loads a lot
SCROLL_WAIT_S = 0.8
NAV_TIMEOUT_MS = 45000

EXTS = (".webp", ".gif")


def _safe_filename(name: str) -> str:
    name = unquote(name or "").strip().strip(".")
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", name)
    return name or "file"


def _unique_path(dirpath: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dirpath, filename)
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(dirpath, f"{base}_{n}{ext}")
        n += 1
    return candidate


def _out_dir(page_url: str) -> str:
    netloc = (urlparse(page_url).netloc or "site").replace(":", "_")
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(os.getcwd(), f"downloads_{netloc}_{ts}")
    os.makedirs(out, exist_ok=True)
    return out


def _looks_like_target(url: str) -> bool:
    if not url:
        return False
    u = url.strip()
    if u.startswith(("data:", "javascript:", "#", "blob:")):
        return False
    path = urlparse(u).path.lower()
    return path.endswith(EXTS)


def main():
    page_url = input("Paste page URL: ").strip()
    if not page_url:
        print("No URL provided.")
        return

    from playwright.sync_api import sync_playwright

    out_dir = _out_dir(page_url)
    print(f"\nOutput folder:\n  {out_dir}\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        context = browser.new_context()

        # A normal browser-ish header set helps on hotlink-protected CDNs
        context.set_extra_http_headers({
            "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })

        page = context.new_page()
        page.set_default_timeout(NAV_TIMEOUT_MS)

        print("[1/3] Loading page...")
        page.goto(page_url, wait_until="domcontentloaded")
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass

        print("[2/3] Scrolling to trigger lazy-load...")
        last_h = 0
        for _ in range(SCROLL_STEPS):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(SCROLL_WAIT_S)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            h = page.evaluate("document.body.scrollHeight")
            if h == last_h:
                break
            last_h = h

        print("[3/3] Collecting .webp/.gif URLs from the rendered page...")

        urls = page.evaluate(
            """
            () => {
              const out = new Set();

              // <img> final resolved sources
              for (const img of Array.from(document.images || [])) {
                const u = img.currentSrc || img.src;
                if (u) out.add(u);
                const srcset = img.getAttribute('srcset');
                if (srcset) {
                  for (const part of srcset.split(',')) {
                    const cand = part.trim().split(' ')[0];
                    if (cand) out.add(cand);
                  }
                }
              }

              // <source> in <picture>/<video>
              for (const s of Array.from(document.querySelectorAll('source'))) {
                const u = s.currentSrc || s.src || s.getAttribute('src');
                if (u) out.add(u);
                const srcset = s.getAttribute('srcset');
                if (srcset) {
                  for (const part of srcset.split(',')) {
                    const cand = part.trim().split(' ')[0];
                    if (cand) out.add(cand);
                  }
                }
              }

              // links
              for (const a of Array.from(document.querySelectorAll('a[href]'))) {
                out.add(a.href);
              }

              // CSS background-image urls (common for webp thumbnails)
              const urlRe = /url\\((['"]?)(.*?)\\1\\)/g;
              const els = Array.from(document.querySelectorAll('*'));
              // avoid going insane on gigantic pages
              const maxEls = 6000;
              for (let i=0; i<Math.min(els.length, maxEls); i++) {
                const el = els[i];
                const bg = getComputedStyle(el).backgroundImage;
                if (!bg || bg === 'none') continue;
                let m;
                while ((m = urlRe.exec(bg)) !== null) {
                  if (m[2]) out.add(m[2]);
                }
              }

              return Array.from(out);
            }
            """
        )

        # Filter + dedupe + cap
        seen = set()
        targets = []
        for u in urls:
            if not _looks_like_target(u):
                continue
            if u not in seen:
                seen.add(u)
                targets.append(u)
            if len(targets) >= MAX_DOWNLOADS:
                break

        if not targets:
            print("No .webp or .gif URLs found after rendering/scrolling.")
            print("If the site uses blob: URLs or requires login, that can block saving.")
            browser.close()
            return

        print(f"Found {len(targets)} target files. Downloading (max {MAX_DOWNLOADS})...\n")

        ok = 0
        fail = 0

        for i, file_url in enumerate(targets, 1):
            try:
                # Download through the browser context request = cookies/session included
                resp = context.request.get(
                    file_url,
                    headers={"Referer": page_url},
                    timeout=30000
                )
                if not resp.ok:
                    raise RuntimeError(f"HTTP {resp.status}")

                body = resp.body()
                # Name from URL path; fallback to index + content-type
                path = urlparse(file_url).path
                name = _safe_filename(os.path.basename(path)) or f"image_{i}"

                ext = os.path.splitext(name)[1].lower()
                if ext not in EXTS:
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if "image/gif" in ctype:
                        ext = ".gif"
                    elif "image/webp" in ctype:
                        ext = ".webp"
                    else:
                        # last-resort: skip unknown
                        ext = os.path.splitext(path)[1].lower()
                        if ext not in EXTS:
                            ext = ".bin"
                    if not name.endswith(ext):
                        name = f"{name}{ext}"

                save_path = _unique_path(out_dir, name)
                with open(save_path, "wb") as f:
                    f.write(body)

                ok += 1
                print(f"[{i}/{len(targets)}] OK   {os.path.basename(save_path)}")
            except Exception as e:
                fail += 1
                print(f"[{i}/{len(targets)}] FAIL {file_url} -> {e}")

        print(f"\nDone. OK={ok} FAIL={fail}")
        browser.close()


if __name__ == "__main__":
    main()
