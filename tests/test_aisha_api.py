"""The automation API: token gate, platform presets, job shape, and publish kits. No network or renderer."""
import io
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

os.environ["PEAKCUT_DATA"] = tempfile.mkdtemp(prefix="aisha-api-")
os.environ.update(
    PEAKCUT_ACCOUNTS="0", PEAKCUT_AISHA_TOKEN="test-token-1234567890",
    PEAKCUT_PUBLIC_URL="http://testserver", PEAKCUT_FETCH_MODEL="0", PEAKCUT_UPDATE_CHECK="0",
    DATABASE_URL="",
)
sys.path.insert(0, ".")
from fastapi.testclient import TestClient
from studio import config, jobs, platforms, publish, render, server

fails = []
def check(name, got, want=True):
    ok = want(got) if callable(want) else got == want
    print(("PASS " if ok else "FAIL ") + name + ("" if ok else f"   got {got!r}"))
    ok or fails.append(name)

T = {"Authorization": "Bearer test-token-1234567890"}

check("aliases resolve", platforms.get("instagram-reels").id, "reels")
check("unknown platform falls back", platforms.get("nope").id, "vertical")
check("shorts preset is vertical and short", (platforms.get("shorts").layout, platforms.get("shorts").max_len), ("crop", 60))
check("caller cannot exceed platform/Pipeline max", platforms.options_for("shorts", {"max_seconds": 999})["max_len"], 180)
check("caller can set a range", platforms.options_for("tiktok", {"range_start": 10, "range_end": 20})["range_end"], 20)
check("X uses a landscape frame", (platforms.get("x").aspect, platforms.get("x").out_w, platforms.get("x").out_h), ("16:9", 1920, 1080))
check("custom dimensions stay editable", platforms.options_for("custom", {"out_w": 1081, "out_h": 607})["out_w"], 1080)
check("caption script follows the output size", "PlayResY: 1080" in render.build_ass(
    [{"w": "hello", "s": 0, "e": 1}], 0, 2, "Hook", "bounce", [], 100, 500, 1920, 1080), True)
check("blur filter uses the output size", "scale=1920:1080" in render._filter("blur", 1080, None, 1920, 1080), True)
clip = {"id": "clip01", "title": "A title", "hook": "A hook", "description": "Body", "hashtags": ["#one", "two words"],
        "score": 9, "start": 1, "end": 31, "file": "clip01.mp4", "cover": "clip01.jpg", "status": "done"}
job = {"id": "20260101-000000-abcdef1234", "source": {"url": "https://youtu.be/x"}, "title": "Source",
       "uploader": "U", "duration": 90, "platform": "tiktok", "clips": [clip]}
m = publish.manifest(job, platforms.get("tiktok"), base_url="http://testserver")
check("manifest has schema", m["schema"], "aisha.publish-kit/v1")
check("manifest post text has platform tag", "#fyp" in m["clips"][0]["post_text"], True)
check("manifest gives a safe download URL", m["clips"][0]["download_urls"]["video"].startswith("http://testserver/api/aisha/v1/jobs/"), True)

# The startup worker is not needed: this test only checks the API contract and packaging.
jobs.start = lambda: None
jobs.enqueue = lambda job: None

with TestClient(server.app, base_url="http://testserver") as c:
    app_html = c.get("/app").text
    check("the studio opens with the animated Aisha splash", 'id="aishaSplash"' in app_html, True)
    check("the studio carries only Aisha branding", "PeakCut" not in app_html, True)
    splash_css = c.get("/assets/aisha-splash.css")
    check("the splash stylesheet is served as CSS", (splash_css.status_code, splash_css.headers.get("content-type", "").split(";")[0]), (200, "text/css"))
    check("the splash respects reduced motion", "prefers-reduced-motion" in splash_css.text, True)
    check("no token is rejected", c.get("/api/aisha/v1/manifest").status_code, 401)
    check("bad token is rejected", c.get("/api/aisha/v1/manifest", headers={"X-Aisha-Token": "wrong-token-000000"}).status_code, 401)
    r = c.get("/api/aisha/v1/manifest", headers=T)
    check("manifest is readable", r.status_code, 200)
    check("manifest lists platforms", len(r.json()["platforms"]), 9)
    check("platform API includes X", next(x for x in c.get("/api/platforms").json() if x["id"] == "x")["aspect"], "16:9")
    check("health says ffmpeg is ready", c.get("/api/aisha/v1/health", headers=T).json()["ffmpeg"], True)
    r = c.post("/api/aisha/v1/jobs", headers=T, json={"video_url": "http://example.com/nope", "platform": "tiktok"})
    check("an outside link is refused", r.status_code, 400)
    r = c.post("/api/aisha/v1/jobs/upload", headers=T,
               files={"file": ("sample.mp4", b"fake video bytes", "video/mp4")},
               data={"platform": "tiktok", "options": json.dumps({"max_seconds": 45})})
    check("upload creates a job", r.status_code, 202)
    made = r.json()
    check("job remembers the platform", made["platform"]["id"], "tiktok")
    check("job uses the platform length limit", made["options"]["max_len"], 45)
    jid = made["id"]
    d = Path(jobs._jobs[jid]["dir"]) / "clips"
    d.mkdir(parents=True, exist_ok=True)
    (d / "clip01.mp4").write_bytes(b"fake mp4")
    (d / "clip01.jpg").write_bytes(b"fake jpg")
    (d / "clip01.ass").write_text("caption", encoding="utf-8")
    j = jobs._jobs[jid]
    j["status"] = "done"
    j["duration"] = 80
    j["title"] = "Source video"
    j["clips"] = [dict(clip, start=2, end=22)]
    jobs.save(j)
    r = c.get(f"/api/aisha/v1/jobs/{jid}/publish", headers=T)
    check("publish manifest is available", r.status_code, 200)
    check("publish manifest knows the platform", r.json()["platform"]["id"], "tiktok")
    r = c.get(f"/api/aisha/v1/jobs/{jid}/bundle", headers=T)
    check("bundle downloads", r.status_code, 200)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    check("bundle has metadata", "metadata.json" in names, True)
    check("bundle has a post file", any(n.startswith("posts/") and n.endswith(".txt") for n in names), True)
    check("bundle has the clip and cover", {"clips/A title.mp4", "clips/A title.jpg", "clips/A title.ass"} <= names, True)
    check("bundle metadata names the kit files", z.read("metadata.json").decode().find("posts/A title.txt") >= 0, True)
    r = c.get(f"/api/aisha/v1/jobs/{jid}/clips/clip01/video", headers=T)
    check("clip video is downloadable", (r.status_code, r.content), (200, b"fake mp4"))
    r = c.post("/api/campaigns", json={"name": "Launch", "creator": "", "platforms": ["tiktok", "shorts"]})
    check("campaign is created", r.status_code, 200)
    cmp = r.json()
    check("campaign keeps its platforms", cmp["platforms"], ["tiktok", "shorts"])
    check("campaign appears in the list", any(x["id"] == cmp["id"] for x in c.get("/api/campaigns").json()), True)
    r = c.put(f"/api/campaigns/{cmp['id']}", json={"name": "Launch v2", "status": "paused"})
    check("campaign can be edited", (r.status_code, r.json()["name"], r.json()["status"]), (200, "Launch v2", "paused"))
    check("campaign can be deleted", c.delete(f"/api/campaigns/{cmp['id']}").status_code, 200)
    r = c.get("/api/schedules")
    check("schedules endpoint is readable", (r.status_code, isinstance(r.json(), list)), (200, True))

shutil.rmtree(os.environ["PEAKCUT_DATA"], ignore_errors=True)
print("ALL PASSED" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
