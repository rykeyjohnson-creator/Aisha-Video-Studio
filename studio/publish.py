"""Publish kits for finished clips.

A kit is a ZIP with the rendered files plus the post text an automation can hand to a publisher:
title, hook, description, hashtags, platform limits, and relative paths inside the kit. The
format is deliberately plain JSON so Aisha does not need to understand PeakCut's internals.
"""
import json
import re
import time
import zipfile
from pathlib import Path

from . import config, ctx, platforms

SCHEMA = "aisha.publish-kit/v1"
MAX_BUNDLE_BYTES = 4 * 10 ** 9
MAX_HASHTAGS = 12


def _name(text: str, fallback: str) -> str:
    clean = "".join(ch if ch.isalnum() or ch in " -_" else "" for ch in str(text or "")).strip()
    return (clean[:70].strip() or fallback)


def _tags(clip: dict, platform) -> list:
    out = []
    for tag in list(clip.get("hashtags") or []) + list(platform.hashtags):
        tag = re.sub(r"[^\w]", "", str(tag).lstrip("#")).strip()
        if tag and tag.lower() not in {x.lower() for x in out}:
            out.append(tag)
        if len(out) >= MAX_HASHTAGS:
            break
    return out


def post_text(clip: dict, platform) -> str:
    title = str(clip.get("title") or "").strip()
    hook = str(clip.get("hook") or "").strip()
    description = str(clip.get("description") or "").strip()
    tags = _tags(clip, platform)
    parts = []
    if title:
        parts.append(title)
    if hook and hook.lower() not in {title.lower(), description.lower()}:
        parts.append(hook)
    if description and description.lower() not in {title.lower(), hook.lower()}:
        parts.append(description[:platform.description_max])
    if tags:
        parts.append(" ".join("#" + t for t in tags))
    return "\n\n".join(parts).strip()


def done_clips(job: dict) -> list:
    return [c for c in job.get("clips", []) if c.get("status") == "done" and c.get("file")]


def manifest(job: dict, platform=None, base_url: str = "", names: dict | None = None) -> dict:
    platform = platform or platforms.get(job.get("platform") or "vertical")
    base = base_url.rstrip("/")
    clips = []
    names = names or {}
    for c in done_clips(job):
        cid = c["id"]
        base_name = names.get(cid) or _name(c.get("title") or c.get("hook"), cid)
        tags = _tags(c, platform)
        clips.append({
            "id": cid,
            "title": c.get("title") or "",
            "hook": c.get("hook") or "",
            "description": c.get("description") or "",
            "hashtags": tags,
            "post_text": post_text(c, platform),
            "score": c.get("score", 0),
            "start": c.get("start"),
            "end": c.get("end"),
            "duration": round(float(c.get("end", 0)) - float(c.get("start", 0)), 2),
            "files": {"video": c.get("file"), "cover": c.get("cover"), "captions": f"{cid}.ass"},
            "kit_name": base_name,
            "download_urls": {
                "video": f"{base}/api/aisha/v1/jobs/{job['id']}/clips/{cid}/video",
                "cover": f"{base}/api/aisha/v1/jobs/{job['id']}/clips/{cid}/cover",
                "kit": f"{base}/api/aisha/v1/jobs/{job['id']}/bundle",
            } if base else {},
        })
    source = job.get("source") or {}
    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "job_id": job["id"],
        "platform": platform.public(),
        "source": {
            "title": job.get("title") or source.get("name") or "",
            "uploader": job.get("uploader") or "",
            "url": source.get("url") or "",
            "duration": job.get("duration") or 0,
        },
        "clips": clips,
    }


def build_bundle(job: dict, platform=None) -> Path:
    """Build a ZIP for one finished video. The caller owns the job/context and must delete the file."""
    items = done_clips(job)
    if not items:
        raise ValueError("No finished clips to package yet.")
    platform = platform or platforms.get(job.get("platform") or "vertical")
    root = Path(job["dir"]) / "clips"
    tmp = config.user_dir(ctx.uid()) / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    for old in tmp.glob("aisha-kit-*.zip"):
        if time.time() - old.stat().st_mtime > 3600:
            old.unlink(missing_ok=True)
    out = tmp / f"aisha-kit-{job['id']}-{int(time.time() * 1000)}.zip"
    used, total, names = set(), 0, {}
    for c in items:
        base = _name(c.get("title") or c.get("hook"), c["id"])
        name, n = base, 2
        while name.lower() in used:
            name, n = f"{base} ({n})", n + 1
        used.add(name.lower())
        names[c["id"]] = name
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED, allowZip64=True) as z:
        for c in items:
            name = names[c["id"]]
            for kind, ext in (("file", ".mp4"), ("cover", ".jpg"), ("captions", ".ass")):
                key = c.get("file") if kind == "file" else c.get("cover") if kind == "cover" else f"{c['id']}.ass"
                if not key:
                    continue
                f = root / Path(str(key)).name
                if f.is_file():
                    total += f.stat().st_size
                    if total > MAX_BUNDLE_BYTES:
                        out.unlink(missing_ok=True)
                        raise ValueError("That kit would be more than 4 GB. Download the clips in smaller groups.")
                    z.write(f, f"clips/{name}{ext}")
        meta = manifest(job, platform, names=names)
        for c in meta["clips"]:
            base = c["kit_name"]
            c["kit_files"] = {
                "video": f"clips/{base}.mp4",
                "cover": f"clips/{base}.jpg",
                "captions": f"clips/{base}.ass",
                "post_text": f"posts/{base}.txt",
            }
        z.writestr("metadata.json", json.dumps(meta, indent=2, ensure_ascii=False))
        readme = (
            "Aisha Video Studio publish kit\n\n"
            f"Platform: {platform.label}\n"
            f"Video: {meta['source']['title']}\n\n"
            "metadata.json is the source of truth. Each clips/*.mp4 is ready to upload; the matching\n"
            "clips/*.jpg is the cover and posts/*.txt is the post text. Keep the files together.\n"
        )
        z.writestr("README.txt", readme)
        for c in meta["clips"]:
            z.writestr(f"posts/{c['kit_name']}.txt", c["post_text"] + "\n")
    return out
