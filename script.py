#!/usr/bin/env python3
"""
Download up to 100 .webp/.gif files found on a webpage.

Install deps:
  pip install requests beautifulsoup4

Run:
  python grab_webp_gif.py
Then paste a link in the GUI and click Start.
"""

import os
import re
import threading
import time
from urllib.parse import urljoin, urlparse, unquote

import requests
from bs4 import BeautifulSoup

MAX_DOWNLOADS = 100
TIMEOUT = 20

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

EXTS = {".webp", ".gif"}


def _safe_filename(name: str) -> str:
    name = unquote(name)
    name = name.strip().strip(".")
    name = re.sub(r"[\\/:*?\"<>|\n\r\t]+", "_", name)
    return name or "file"


def _pick_output_dir(page_url: str) -> str:
    netloc = urlparse(page_url).netloc.replace(":", "_") or "site"
    ts = time.strftime("%Y%m%d-%H%M%S")
    out = os.path.join(os.getcwd(), f"downloads_{netloc}_{ts}")
    os.makedirs(out, exist_ok=True)
    return out


def _extract_urls(page_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    found: list[str] = []

    def add(u: str | None):
        if not u:
            return
        u = u.strip()
        if not u or u.startswith("data:") or u.startswith("javascript:") or u.startswith("#"):
            return
        found.append(urljoin(page_url, u))

    # <img src>, common lazy-load attrs
    for img in soup.find_all("img"):
        add(img.get("src"))
        add(img.get("data-src"))
        add(img.get("data-original"))
        add(img.get("data-lazy-src"))
        srcset = img.get("srcset")
        if srcset:
            for part in srcset.split(","):
                add(part.strip().split(" ")[0])

    # <source src>, <source srcset> (picture/video)
    for src in soup.find_all("source"):
        add(src.get("src"))
        srcset = src.get("srcset")
        if srcset:
            for part in srcset.split(","):
                add(part.strip().split(" ")[0])

    # <a href>
    for a in soup.find_all("a"):
        add(a.get("href"))

    # Dedup preserving order
    seen = set()
    dedup = []
    for u in found:
        if u not in seen:
            seen.add(u)
            dedup.append(u)
    return dedup


def _filter_webp_gif(urls: list[str]) -> list[str]:
    out = []
    for u in urls:
        path = urlparse(u).path.lower()
        for ext in EXTS:
            if path.endswith(ext):
                out.append(u)
                break
    return out


def _unique_path(dirpath: str, filename: str) -> str:
    base, ext = os.path.splitext(filename)
    candidate = os.path.join(dirpath, filename)
    n = 2
    while os.path.exists(candidate):
        candidate = os.path.join(dirpath, f"{base}_{n}{ext}")
        n += 1
    return candidate


def download_from_page(page_url: str, log_cb, prog_cb):
    headers = {"User-Agent": UA}
    r = requests.get(page_url, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()

    urls = _extract_urls(page_url, r.text)
    targets = _filter_webp_gif(urls)

    if not targets:
        log_cb("No .webp or .gif links found in the page HTML.")
        prog_cb(0, 0)
        return

    out_dir = _pick_output_dir(page_url)
    log_cb(f"Found {len(targets)} candidate files. Saving to:\n  {out_dir}")

    targets = targets[:MAX_DOWNLOADS]
    total = len(targets)

    sess = requests.Session()
    base_headers = {"User-Agent": UA, "Referer": page_url}

    for i, file_url in enumerate(targets, 1):
        try:
            resp = sess.get(file_url, headers=base_headers, timeout=TIMEOUT, stream=True)
            resp.raise_for_status()

            # filename from URL path
            p = urlparse(file_url).path
            name = _safe_filename(os.path.basename(p)) or f"image_{i}"
            # ensure extension present
            if not os.path.splitext(name)[1]:
                # fallback: infer from path
                ext = os.path.splitext(p)[1]
                if ext.lower() in EXTS:
                    name += ext.lower()

            save_path = _unique_path(out_dir, name)

            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        f.write(chunk)

            log_cb(f"[{i}/{total}] OK  {file_url}\n         -> {os.path.basename(save_path)}")
        except Exception as e:
            log_cb(f"[{i}/{total}] FAIL {file_url}\n         -> {e}")

        prog_cb(i, total)

    log_cb("Done.")


# --- Simple GUI (Tkinter) ---
def main():
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox
    except Exception:
        # Fallback: terminal
        page_url = input("Paste a page URL: ").strip()
        if not page_url:
            print("No URL provided.")
            return

        def log_cb(s): print(s)
        def prog_cb(i, t): print(f"Progress: {i}/{t}")
        download_from_page(page_url, log_cb, prog_cb)
        return

    root = tk.Tk()
    root.title("WEBP/GIF Grabber (max 100)")
    root.geometry("760x520")

    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="Paste a page link:").pack(anchor="w")
    url_var = tk.StringVar()
    url_entry = ttk.Entry(frm, textvariable=url_var)
    url_entry.pack(fill="x", pady=(4, 10))
    url_entry.focus()

    btn_row = ttk.Frame(frm)
    btn_row.pack(fill="x")

    start_btn = ttk.Button(btn_row, text="Start")
    start_btn.pack(side="left")

    prog = ttk.Progressbar(btn_row, mode="determinate")
    prog.pack(side="left", fill="x", expand=True, padx=10)

    prog_lbl = ttk.Label(btn_row, text="0/0")
    prog_lbl.pack(side="right")

    ttk.Separator(frm).pack(fill="x", pady=10)

    log = tk.Text(frm, height=18, wrap="word")
    log.pack(fill="both", expand=True)

    def log_cb(msg: str):
        def _ui():
            log.insert("end", msg + "\n")
            log.see("end")
        root.after(0, _ui)

    def prog_cb(i: int, total: int):
        def _ui():
            prog["maximum"] = max(total, 1)
            prog["value"] = i
            prog_lbl.config(text=f"{i}/{total}")
        root.after(0, _ui)

    def set_running(running: bool):
        start_btn.config(state=("disabled" if running else "normal"))
        url_entry.config(state=("disabled" if running else "normal"))

    def on_start():
        page_url = url_var.get().strip()
        if not page_url:
            messagebox.showerror("Missing URL", "Paste a link first.")
            return

        set_running(True)
        log.delete("1.0", "end")
        prog_cb(0, 0)

        def worker():
            try:
                download_from_page(page_url, log_cb, prog_cb)
            except Exception as e:
                log_cb(f"ERROR: {e}")
            finally:
                root.after(0, lambda: set_running(False))

        threading.Thread(target=worker, daemon=True).start()

    start_btn.config(command=on_start)
    root.mainloop()


if __name__ == "__main__":
    main()
