import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = os.environ["PIXABAY_API_KEY"]
OUT = Path("output")
WORK = Path("work")
OUT.mkdir(exist_ok=True)
WORK.mkdir(exist_ok=True)

W, H, FPS = 1080, 1920, 30
TARGET_TOTAL = 28
MIN_SEC, MAX_SEC = 20, 35
MIN_MEAN_DB = -45.0
MAX_TRIES = 5

LOG = {"started": "", "recipe_id": None, "status": "running", "sources": [], "skipped_shots": [], "events": []}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


LOG["started"] = now()


def log(msg):
    print(msg, flush=True)
    LOG["events"].append(f"{now()} {msg}")


def save_log():
    (OUT / "log.json").write_text(json.dumps(LOG, indent=2, ensure_ascii=False), encoding="utf-8")


def fail(msg):
    log("FAILED: " + msg)
    LOG["status"] = "failed"
    LOG["error"] = msg
    save_log()
    sys.exit(1)


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def has_audio(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)])
    return r.returncode == 0 and "audio" in r.stdout


def duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def mean_volume(path):
    r = run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn",
             "-af", "volumedetect", "-f", "null", "-"])
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else None


def check_clip(path):
    if not has_audio(path):
        return False, "no audio stream"
    vol = mean_volume(path)
    if vol is None or vol < MIN_MEAN_DB:
        return False, f"silent or too quiet ({vol})"
    if duration(path) < 3:
        return False, "too short"
    return True, "ok"


def search(query):
    r = requests.get(
        "https://pixabay.com/api/videos/",
        params={"key": API_KEY, "q": query[:100], "per_page": 20, "safesearch": "true"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json().get("hits", [])


def download(url, dst):
    with requests.get(url, stream=True, timeout=90) as r:
        r.raise_for_status()
        with open(dst, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)


def make_segment(src, dst, seg):
    t = min(seg, duration(src))
    fade_start = max(0.0, t - 0.2)
    result = run([
        "ffmpeg", "-y", "-i", str(src), "-t", f"{t:.2f}",
        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS},format=yuv420p",
        "-af", f"aresample=44100,aformat=channel_layouts=stereo,afade=t=in:d=0.05,afade=t=out:st={fade_start:.2f}:d=0.2",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-ar", "44100", "-b:a", "160k", str(dst),
    ])
    if result.returncode != 0:
        fail("segment encode failed: " + result.stderr[-400:])


def validate(path):
    problems = []
    if not path.exists() or path.stat().st_size == 0:
        return ["file missing or empty"]
    r = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)])
    if r.stdout.strip() != f"{W}x{H}":
        problems.append(f"bad resolution: {r.stdout.strip()}")
    d = duration(path)
    if not (MIN_SEC <= d <= MAX_SEC):
        problems.append(f"duration {d:.1f}s outside {MIN_SEC}-{MAX_SEC}s")
    if not has_audio(path):
        problems.append("no audio stream")
    else:
        vol = mean_volume(path)
        if vol is None or vol < -50:
            problems.append(f"audio silent ({vol})")
    b = run(["ffmpeg", "-hide_banner", "-i", str(path), "-an",
             "-vf", "blackdetect=d=0.5:pix_th=0.10", "-f", "null", "-"])
    if "black_start" in b.stderr:
        problems.append("black screen detected")
    if path.stat().st_size > 100 * 1024 * 1024:
        problems.append("file larger than 100 MB")
    return problems


# ---- 1. pick recipe ----
if not Path("recipes.json").exists():
    fail("recipes.json not found in repository")

recipes = json.loads(Path("recipes.json").read_text(encoding="utf-8"))
wanted = os.environ.get("RECIPE_ID")
if wanted:
    recipe = next((r for r in recipes if r["id"] == wanted), None)
else:
    recipe = next((r for r in recipes if r.get("status") == "pending"), None)
if not recipe:
    fail("no matching pending recipe")

LOG["recipe_id"] = recipe["id"]
log(f"Recipe {recipe['id']}: {recipe['name']}")

# ---- 2. find a clip per shot (must have real audio) ----
clips, used = [], set()
for i, shot in enumerate(recipe["shots"], 1):
    found, tries = None, 0
    for q in (shot, f"{recipe['category']} cooking"):
        try:
            hits = search(q)
        except requests.RequestException as e:
            log(f"search error for '{q}': {e}")
            continue
        for hit in hits:
            if found or tries >= MAX_TRIES:
                break
            if hit["id"] in used:
                continue
            tries += 1
            files = hit.get("videos", {})
            src = files.get("medium") or files.get("large") or files.get("small")
            if not src or not src.get("url"):
                continue
            path = WORK / f"{hit['id']}.mp4"
            try:
                download(src["url"], path)
            except requests.RequestException as e:
                log(f"download error {hit['id']}: {e}")
                continue
            ok, why = check_clip(path)
            if ok:
                found = (hit, path)
            else:
                log(f"shot {i}: rejected {hit['id']} ({why})")
                path.unlink(missing_ok=True)
        if found:
            break
    if found:
        hit, path = found
        used.add(hit["id"])
        clips.append(path)
        LOG["sources"].append({"shot": shot, "pixabay_id": hit["id"],
                               "page": f"https://pixabay.com/videos/id-{hit['id']}/"})
        log(f"shot {i}: accepted {hit['id']}")
    else:
        LOG["skipped_shots"].append(shot)
        log(f"shot {i}: no suitable clip with audio")

if len(clips) < 3:
    fail(f"only {len(clips)} usable clips with audio found (need 3+)")

# ---- 3. edit ----
seg = max(4.0, min(8.0, TARGET_TOTAL / len(clips)))
segments = []
for n, clip in enumerate(clips):
    dst = WORK / f"seg{n}.mp4"
    make_segment(clip, dst, seg)
    segments.append(dst)

(WORK / "list.txt").write_text(
    "".join(f"file '{p.resolve()}'\n" for p in segments), encoding="utf-8")

joined = WORK / "joined.mp4"
r = run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(WORK / "list.txt"),
         "-c", "copy", str(joined)])
if r.returncode != 0:
    fail("concat failed: " + r.stderr[-400:])

final = OUT / "chefverse_short.mp4"
r = run(["ffmpeg", "-y", "-i", str(joined), "-c:v", "copy",
         "-af", "loudnorm=I=-18:TP=-2:LRA=7", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", str(final)])
if r.returncode != 0:
    fail("final encode failed: " + r.stderr[-400:])

# ---- 4. validate ----
problems = validate(final)
LOG["validation"] = problems or "passed"
if problems:
    final.unlink(missing_ok=True)
    fail("validation failed: " + "; ".join(problems))

# ---- 5. metadata (not uploaded yet) ----
tags = "#cookingasmr #asmr #shorts #recipe #chefverse"
(OUT / "metadata.json").write_text(json.dumps({
    "title": f"{recipe['name']} | Satisfying Cooking ASMR #Shorts",
    "description": f"{recipe['name']} - A Universe of Flavours.\n\n{tags}",
    "recipe_id": recipe["id"],
}, indent=2, ensure_ascii=False), encoding="utf-8")

LOG["status"] = "created_not_uploaded"
LOG["finished"] = now()
save_log()
log(f"Short created: {final}")
