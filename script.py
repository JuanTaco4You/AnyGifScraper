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
from urllib.parse import parse_qs, unquote, urljoin, urlparse

try:
    import requests
except Exception:
    requests = None

try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

MAX_DOWNLOADS = 100
HEADLESS = False

# How long to keep listening for image requests after initial load:
CAPTURE_SECONDS = 8

# Set >0 if the site lazy-loads on scroll (0 = no extra steps)
SCROLL_STEPS = 0
SCROLL_WAIT_S = 0.8

TIMEOUT_MS = 45000
EXTS = (".webp", ".gif")
REQ_HEADERS = {
    "Accept": "image/avif,image/webp,image/gif,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}


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


def save_bytes(out_dir: str, url: str, content_type: str, body: bytes, index: int, name_hint=None) -> str:
    ext = ext_from_type_or_url(url, content_type)
    name = safe_filename(name_hint or os.path.basename(urlparse(url).path)) or f"image_{index}{ext}"
    if ext and not name.lower().endswith(ext):
        name = f"{name}{ext}"
    save_path = unique_path(out_dir, name)
    with open(save_path, "wb") as f:
        f.write(body)
    return save_path


def betterttv_targets(page_url: str, limit: int):
    parsed = urlparse(page_url)
    if parsed.netloc not in ("betterttv.com", "www.betterttv.com"):
        return None
    if not parsed.path.startswith("/emotes/shared/search"):
        return None
    if requests is None:
        return None

    query = parse_qs(parsed.query).get("query", [""])[0].strip()
    if not query:
        return []

    api_url = "https://api.betterttv.net/3/emotes/shared/search"
    try:
        resp = requests.get(
            api_url,
            params={"query": query, "offset": 0, "limit": limit},
            headers=REQ_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    if not isinstance(data, list):
        return []

    targets = []
    for emote in data:
        emote_id = emote.get("id")
        if not emote_id:
            continue
        code = emote.get("code") or emote_id
        base = f"https://cdn.betterttv.net/emote/{emote_id}/3x"
        targets.append({"urls": [f"{base}.webp", base], "name": code})
    return targets


def extract_srcset(value: str):
    urls = []
    for part in value.split(","):
        url = part.strip().split(" ")[0]
        if url:
            urls.append(url)
    return urls


def html_targets(page_url: str):
    if requests is None:
        return []

    try:
        resp = requests.get(page_url, headers=REQ_HEADERS, timeout=30)
        resp.raise_for_status()
    except Exception:
        return []

    urls = set(re.findall(r'https?://[^\s"\'<>]+?\.(?:webp|gif)(?:\?[^\s"\'<>]*)?', resp.text, re.I))

    if BeautifulSoup:
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup.find_all(["img", "source", "a", "link"]):
            for attr in ("src", "href", "data-src", "data-original", "data-lazy", "data-srcset", "srcset"):
                value = tag.get(attr)
                if not value:
                    continue
                values = extract_srcset(value) if "srcset" in attr else [value]
                for item in values:
                    abs_url = urljoin(page_url, item.strip())
                    if looks_like_target(abs_url, ""):
                        urls.add(abs_url)

    return sorted(urls)


def download_requests_targets(targets, out_dir: str, referer: str):
    if requests is None:
        print("requests is not installed; cannot download without a browser.")
        return

    ok = 0
    fail = 0
    total = min(len(targets), MAX_DOWNLOADS)

    for i, target in enumerate(targets[:MAX_DOWNLOADS], 1):
        if isinstance(target, dict):
            urls = target.get("urls") or []
            name_hint = target.get("name")
        else:
            urls = [target]
            name_hint = None

        saved = False
        last_err = "no urls"

        for url in urls:
            try:
                headers = dict(REQ_HEADERS)
                if referer:
                    headers["Referer"] = referer
                resp = requests.get(url, headers=headers, timeout=30)
                if not resp.ok:
                    last_err = f"HTTP {resp.status_code}"
                    continue

                content_type = resp.headers.get("content-type", "")
                if not looks_like_target(url, content_type):
                    last_err = f"skip content-type {content_type or 'unknown'}"
                    continue

                save_path = save_bytes(out_dir, url, content_type, resp.content, i, name_hint)
                ok += 1
                saved = True
                print(f"[{i}/{total}] OK   {os.path.basename(save_path)}")
                break
            except Exception as e:
                last_err = str(e)

        if not saved:
            fail += 1
            if urls:
                print(f"[{i}/{total}] FAIL {urls[0]} -> {last_err}")

    print(f"\nDone. OK={ok} FAIL={fail}")


def fallback_html_download(page_url: str, out_dir: str) -> bool:
    targets = html_targets(page_url)
    if not targets:
        print("No .webp or .gif links found in the page HTML.")
        return False

    print("[1/1] Using HTML scan (no browser required)...")
    download_requests_targets(targets, out_dir, page_url)
    return True


def main():
    page_url = input("Paste page URL: ").strip()
    if not page_url:
        print("No URL provided.")
        return

    out_dir = mk_out_dir(page_url)
    print(f"\nOutput folder:\n  {out_dir}\n")

    bttv = betterttv_targets(page_url, MAX_DOWNLOADS)
    if bttv is not None:
        if not bttv:
            print("No emotes found via the BetterTTV API.")
        else:
            print("[1/1] Using BetterTTV API (no browser required)...")
            download_requests_targets(bttv, out_dir, page_url)
        return

    if sync_playwright is None:
        print("Playwright is not installed; falling back to HTML scan.")
        fallback_html_download(page_url, out_dir)
        return

    captured = []  # list of (url, content_type)
    seen = set()

    try:
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

                    content_type = resp.headers.get("content-type") or ct
                    if not looks_like_target(url, content_type):
                        raise RuntimeError("not webp/gif")

                    save_path = save_bytes(out_dir, url, content_type, resp.body(), i, None)
                    ok += 1
                    print(f"[{i}/{len(targets)}] OK   {os.path.basename(save_path)}")
                except Exception as e:
                    fail += 1
                    print(f"[{i}/{len(targets)}] FAIL {url} -> {e}")

            print(f"\nDone. OK={ok} FAIL={fail}")
            browser.close()
    except Exception as e:
        print(f"Browser capture failed: {e}")
        if not fallback_html_download(page_url, out_dir):
            print("If the site is dynamic, install Playwright deps and retry.")


if __name__ == "__main__":
    main()
