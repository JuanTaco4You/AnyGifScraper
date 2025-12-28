# AnyGifScraper
clipsglitched 
# WEBP/GIF Grabber (max 100)

A small Python tool that downloads up to **100** `.webp` and `.gif` files found on a webpage.  
Paste a link → it scans the page HTML → downloads matching assets → shows progress in a simple **GUI** (Tkinter) with a **terminal fallback**.

## Features
- Downloads **.webp** and **.gif** files from a given page URL
- Max download limit: **100**
- GUI progress bar + live log output
- Terminal fallback if Tkinter isn’t available
- Creates a timestamped output folder per run

## Requirements
- Python **3.9+** recommended
- Packages:
  - `requests`
  - `beautifulsoup4`

## Install
```bash
pip install requests beautifulsoup4

## RUN 
python3 script.py

