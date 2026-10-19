
import os
import random
import subprocess
import requests
from pathlib import Path

API_KEY = os.environ["PIXABAY_API_KEY"]
OUT = Path("output")
OUT.mkdir(exist_ok=True)

QUERIES = [
    "cooking food",
    "food frying",
    "vegetables chopping",
    "baking dessert",
    "cooking close up"
]

def run(command):
    subprocess.run(command, check=True)

query = random.choice(QUERIES)

response = requests.get(
    "https://pixabay.com/api/videos/",
    params={
        "key": API_KEY,
        "q": query,
        "per_page": 20
    },
    timeout=30
)
response.raise_for_status()
videos = response.json().get("hits", [])

if not videos:
    raise RuntimeError("No videos found for this search.")

video = random.choice(videos)
video_files = video.get("videos", {})
source = (
    video_files.get("medium")
    or video_files.get("small")
    or video_files.get("large")
)

if not source or not source.get("url"):
    raise RuntimeError("No downloadable video found.")

download = requests.get(source["url"], timeout=90)
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
    f"Pixabay video ID: {video.get('id', '')}\n"
    f"Page: https://pixabay.com/videos/id-{video.get('id', '')}/\n",
    encoding="utf-8"
)

print("Short created:", OUT / "cooking_asmr_short.mp4")
