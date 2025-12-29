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
EXTS = (".webp", ".gif", ".webm")
REQ_HEADERS = {
    "Accept": "image/avif,image/webp,image/gif,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}
JSON_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": REQ_HEADERS["User-Agent"],
}
GRAPHQL_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/json",
    "User-Agent": REQ_HEADERS["User-Agent"],
}
GIPHY_KEY_MISSING = object()


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
    return ("image/webp" in ct) or ("image/gif" in ct) or ("video/webm" in ct)


def ext_from_type_or_url(url: str, content_type: str) -> str:
    path_ext = os.path.splitext(urlparse(url).path)[1].lower()
    if path_ext in EXTS:
        return path_ext

    ct = (content_type or "").lower()
    if "image/webp" in ct:
        return ".webp"
    if "image/gif" in ct:
        return ".gif"
    if "video/webm" in ct:
        return ".webm"
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


def normalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("cdn."):
        return "https://" + u
    return u


def dedupe_urls(urls):
    seen = set()
    out = []
    for url in urls:
        u = normalize_url(url)
        if not u or u in seen:
            continue
        seen.add(u)
        out.append(u)
    return out


def pick_list(data, keys):
    if isinstance(data, list):
        return True, data
    if isinstance(data, dict):
        for key in keys:
            if key in data and isinstance(data[key], list):
                return True, data[key]
    return False, []


def get_query_param(parsed, keys):
    params = parse_qs(parsed.query)
    for key in keys:
        value = params.get(key, [""])[0].strip()
        if value:
            return value
    return ""


def looks_like_7tv_id(value: str) -> bool:
    if re.fullmatch(r"[a-f0-9]{24}", value or "", re.I):
        return True
    return bool(re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{26}", (value or "").upper()))


def query_from_path(parsed, prefix: str) -> str:
    if parsed.path.startswith(prefix):
        value = parsed.path[len(prefix):].strip("/")
        if value:
            return unquote(value)
    return ""


def betterttv_targets(page_url: str, limit: int):
    parsed = urlparse(page_url)
    if parsed.netloc not in ("betterttv.com", "www.betterttv.com"):
        return None
    if not parsed.path.startswith("/emotes/shared/search"):
        return None
    if requests is None:
        return None

    query = get_query_param(parsed, ("query", "q", "search", "term"))
    if not query:
        return []

    api_url = "https://api.betterttv.net/3/emotes/shared/search"
    try:
        resp = requests.get(
            api_url,
            params={"query": query, "offset": 0, "limit": limit},
            headers=JSON_HEADERS,
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


def seventv_cdn_urls(emote_id: str):
    urls = []
    for size in ("4x", "3x", "2x", "1x"):
        for ext in (".gif", ".webp"):
            urls.append(f"https://cdn.7tv.app/emote/{emote_id}/{size}{ext}")
    return urls


def seventv_urls_from_item(item):
    urls = []
    host = item.get("host")
    if not host and isinstance(item.get("data"), dict):
        host = item["data"].get("host")

    if isinstance(host, dict):
        base = normalize_url(host.get("url"))
        files = host.get("files") or []
        for file in files:
            name = file.get("name")
            if name and base:
                url = urljoin(base + "/", name)
                if looks_like_target(url, ""):
                    urls.append(url)

    emote_id = item.get("id") or item.get("_id")
    if emote_id and not urls:
        urls.extend(seventv_cdn_urls(emote_id))

    return dedupe_urls(urls)


def seventv_api_search(query: str, limit: int):
    gql = """
    query SearchEmotes($query: String!, $limit: Int, $page: Int) {
      emotes(query: $query, limit: $limit, page: $page) {
        items { id name host { url files { name format } } }
      }
    }
    """

    try:
        resp = requests.post(
            "https://7tv.io/v3/gql",
            headers=GRAPHQL_HEADERS,
            json={"query": gql, "variables": {"query": query, "limit": limit, "page": 1}},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    items = data.get("data", {}).get("emotes", {}).get("items")
    return items if isinstance(items, list) else []


def seventv_targets(page_url: str, limit: int):
    parsed = urlparse(page_url)
    if parsed.netloc not in ("7tv.app", "www.7tv.app", "7tv.io", "www.7tv.io"):
        return None
    if requests is None:
        return None

    emote_id = query_from_path(parsed, "/emotes/")
    if emote_id and "/" not in emote_id and looks_like_7tv_id(emote_id):
        return [{"urls": seventv_cdn_urls(emote_id), "name": emote_id}]

    query = get_query_param(parsed, ("query", "q", "search", "term"))
    if not query:
        query = query_from_path(parsed, "/search/")
    if not query and emote_id and "/" not in emote_id:
        query = emote_id
    if not query:
        return []

    items = seventv_api_search(query, limit)
    targets = []
    for item in items:
        emote_id = item.get("id") or item.get("_id")
        name = item.get("name") or item.get("code") or emote_id
        urls = seventv_urls_from_item(item)
        if urls:
            targets.append({"urls": urls, "name": name})
    return targets


def ffz_api_search(query: str, limit: int):
    endpoints = [
        ("https://api.frankerfacez.com/v1/emotes", {"q": query, "per_page": limit}),
        ("https://api.frankerfacez.com/v1/search/emotes", {"q": query, "per_page": limit}),
    ]

    for url, params in endpoints:
        try:
            resp = requests.get(url, params=params, headers=JSON_HEADERS, timeout=30)
            if not resp.ok:
                continue
            data = resp.json()
        except Exception:
            continue

        found, items = pick_list(data, ("emotes", "emoticons", "results"))
        if found:
            return items

    return []


def ffz_urls_from_emote(emote):
    urls = []

    def add_urls(url_map):
        for key in ("4", "2", "1"):
            url = url_map.get(key)
            if url:
                urls.append(url)

    urls_map = emote.get("urls") or {}
    if isinstance(urls_map, dict):
        add_urls(urls_map)

    animated = emote.get("animated")
    if isinstance(animated, dict):
        anim_urls = animated.get("urls") or {}
        if isinstance(anim_urls, dict):
            add_urls(anim_urls)

    return dedupe_urls(urls)


def ffz_targets(page_url: str, limit: int):
    parsed = urlparse(page_url)
    if parsed.netloc not in ("frankerfacez.com", "www.frankerfacez.com"):
        return None
    if requests is None:
        return None

    query = get_query_param(parsed, ("q", "query", "search", "term"))
    if not query:
        query = query_from_path(parsed, "/emoticons/")
    if not query:
        return []

    items = ffz_api_search(query, limit)
    targets = []
    for item in items:
        emote_id = item.get("id")
        name = item.get("name") or item.get("code") or (str(emote_id) if emote_id else None)
        urls = ffz_urls_from_emote(item)
        if urls:
            targets.append({"urls": urls, "name": name})
    return targets


def giphy_targets(page_url: str, limit: int):
    parsed = urlparse(page_url)
    if parsed.netloc not in ("giphy.com", "www.giphy.com"):
        return None
    if requests is None:
        return None

    query = get_query_param(parsed, ("q", "query", "search", "term"))
    if not query:
        query = query_from_path(parsed, "/search/")
        if query:
            query = query.replace("-", " ")
    if not query:
        return []

    api_key = os.environ.get("GIPHY_API_KEY", "").strip()
    if not api_key:
        return GIPHY_KEY_MISSING

    try:
        resp = requests.get(
            "https://api.giphy.com/v1/gifs/search",
            params={"api_key": api_key, "q": query, "limit": min(limit, 100)},
            headers=JSON_HEADERS,
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    found, items = pick_list(data, ("data",))
    if not found:
        return []

    targets = []
    for item in items:
        images = item.get("images") or {}
        urls = []

        original = images.get("original") or {}
        if original.get("webp"):
            urls.append(original.get("webp"))
        if original.get("url"):
            urls.append(original.get("url"))

        downsized = images.get("downsized_large") or images.get("downsized") or {}
        if downsized.get("url"):
            urls.append(downsized.get("url"))

        preview_webp = images.get("preview_webp") or {}
        if preview_webp.get("url"):
            urls.append(preview_webp.get("url"))

        urls = dedupe_urls(urls)
        if not urls:
            continue

        name = item.get("title") or item.get("slug") or item.get("id")
        targets.append({"urls": urls, "name": name})

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

    urls = set(re.findall(r'https?://[^\s"\'<>]+?\.(?:webp|gif|webm)(?:\?[^\s"\'<>]*)?', resp.text, re.I))

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

    seventv = seventv_targets(page_url, MAX_DOWNLOADS)
    if seventv is not None:
        if not seventv:
            print("No emotes found via the 7TV API.")
        else:
            print("[1/1] Using 7TV API (no browser required)...")
            download_requests_targets(seventv, out_dir, page_url)
        return

    ffz = ffz_targets(page_url, MAX_DOWNLOADS)
    if ffz is not None:
        if not ffz:
            print("No emotes found via the FrankerFaceZ API.")
        else:
            print("[1/1] Using FrankerFaceZ API (no browser required)...")
            download_requests_targets(ffz, out_dir, page_url)
        return

    giphy = giphy_targets(page_url, MAX_DOWNLOADS)
    if giphy is GIPHY_KEY_MISSING:
        print("GIPHY_API_KEY is not set; skipping Giphy API.")
    elif giphy is not None:
        if not giphy:
            print("No GIFs found via the Giphy API.")
        else:
            print("[1/1] Using Giphy API (no browser required)...")
            download_requests_targets(giphy, out_dir, page_url)
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
