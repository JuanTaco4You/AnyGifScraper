# AnyGifScraper
clipsglitched 
# WEBP/GIF Grabber (max 100)

A small Python tool that downloads up to **100** `.webp`, `.gif`, and `.webm` files found on a webpage.  
Paste a link → it scans the page HTML → downloads matching assets → shows progress in a simple **GUI** (Tkinter) with a **terminal fallback**.

## Features
- Downloads **.webp**, **.gif**, and **.webm** files from a given page URL
- Max download limit: **100**
- GUI progress bar + live log output
- Terminal fallback if Tkinter isn’t available
- Creates a timestamped output folder per run
- API support for BetterTTV, 7TV, FrankerFaceZ, and Giphy search URLs (Giphy needs `GIPHY_API_KEY`)

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
 
# Optional (Giphy search):
# export GIPHY_API_KEY=your_api_key





=================================================================================================================
pip install playwright
python -m playwright install chromium
python grab_webp_gif_browser.py










pip install playwright
python -m playwright install chromium
python script.py 
