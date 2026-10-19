

import os
import random
import subprocess
import requests
from pathlib import Path

API_KEY = os.environ["PEXELS_API_KEY"]
OUT = Path("output")
OUT.mkdir(exist_ok=True)

QUERIES = [
    "food frying close up",
    "vegetables chopping close up",
    "dough kneading cooking",
    "chocolate pouring dessert",
    "crispy food cooking pan"
]

def run(command):
    subprocess.run(command, check=True)

query = random.choice(QUERIES)

response = requests.get(
    "https://api.pexels.com/videos/search",
    headers={"Authorization": API_KEY},
    params={
        "query": query,
        "per_page": 20,
        "orientation": "portrait"
    },
    timeout=30
)
response.raise_for_status()
videos = response.json().get("videos", [])

if not videos:
    raise RuntimeError("No clips found.")

video = random.choice(videos)
files = [
    f for f in video.get("video_files", [])
    if f.get("file_type") == "video/mp4"
    and f.get("width", 0) > 0
]

if not files:
    raise RuntimeError("No MP4 found.")

source = min(
    files,
    key=lambda f: abs(f.get("width", 0) - 1080)
)

download = requests.get(source["link"], timeout=90)
download.raise_for_status()
Path("source.mp4").write_bytes(download.content)

run([
    "ffmpeg", "-y", "-i", "source.mp4",
    "-t", "25",
    "-vf",
    "scale=720:1280:force_original_aspect_ratio=increase,"
    "crop=720:1280,fps=30",
    "-af", "loudnorm",
    "-c:v", "libx264",
    "-preset", "veryfast",
    "-pix_fmt", "yuv420p",
    "-c:a", "aac",
    "-b:a", "128k",
    "-movflags", "+faststart",
    str(OUT / "cooking_asmr_short.mp4")
])

(OUT / "source.txt").write_text(
    f"Search: {query}\n"
    f"Source page: {video.get('url', '')}\n"
    f"Creator: {video.get('user', {}).get('name', '')}\n",
    encoding="utf-8"
)

print("Short created:", OUT / "cooking_asmr_short.mp4")
