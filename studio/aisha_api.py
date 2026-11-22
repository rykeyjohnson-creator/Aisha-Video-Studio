"""Token-protected automation API for Aisha.

The browser app and this API share PeakCut's job queue, renderer and storage. The API only adds a
small, stable surface: submit a link or an upload, poll one job, read the publish kit, download
files. It deliberately has no user management, settings or filesystem endpoints.
"""
import hmac
import json
import os
import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from . import auth, config, ctx, jobs, load, media, platforms, publish, tools

router = APIRouter(prefix="/api/aisha/v1", tags=["aisha"])

VIDEO_TYPES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".ts", ".flv"}
JID_RE = re.compile(r"[\w\-]{8,60}")
CID_RE = re.compile(r"[\w\-]{1,60}")


def _given_token(authorization: str | None, x_aisha_token: str | None) -> str:
    if x_aisha_token:
        return x_aisha_token.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def auth_user(authorization: str | None = Header(default=None),
              x_aisha_token: str | None = Header(default=None)) -> str:
    """Check the service token, then return the user id automation work belongs to."""
    wanted = config.aisha_token()
    if not wanted:
        raise HTTPException(503, "Automation API is off. Set PEAKCUT_AISHA_TOKEN or create data/aisha_token.")
    given = _given_token(authorization, x_aisha_token)
    if not given or not hmac.compare_digest(given, wanted):
        raise HTTPException(401, "Bad or missing Aisha token.", headers={"WWW-Authenticate": "Bearer"})
    if not config.ACCOUNTS:
        return auth.local_user().id
    uid = config.aisha_user()
    if not uid:
        raise HTTPException(503, "Set PEAKCUT_AISHA_USER to the account id automation should use.")
    if not auth.get_user(uid):
        raise HTTPException(404, "The configured PEAKCUT_AISHA_USER does not exist.")
    return uid


def _job(uid: str, jid: str) -> dict:
    if not JID_RE.fullmatch(jid or ""):
        raise HTTPException(404, "No such video")
    with ctx.as_user(uid):
        job = jobs.get(jid)
    if not job:
        raise HTTPException(404, "No such video")
    return job


def _slot(uid: str):
    if tools.missing():
        raise HTTPException(503, tools.HELP)
    if load.disk_full():
        raise HTTPException(507, "The disk is nearly full. Free some space and try again.")
    with ctx.as_user(uid):
        if jobs.busy_count() >= config.MAX_ACTIVE_JOBS_PER_USER:
            raise HTTPException(429, f"Already {config.MAX_ACTIVE_JOBS_PER_USER} videos in progress. Wait for one to finish.")


def _url(path: str) -> str:
    return f"{config.PUBLIC_URL.rstrip('/')}{path}"


def _clip_urls(job: dict, clip: dict) -> dict:
    base = f"/api/aisha/v1/jobs/{job['id']}/clips/{clip['id']}"
    return {"video": _url(base + "/video"), "cover": _url(base + "/cover")}


def shape(request: Request, job: dict) -> dict:
    out = jobs.public(job)
    p = platforms.get(job.get("platform") or "vertical")
    out["platform"] = p.public()
    out["bundle_url"] = _url(f"/api/aisha/v1/jobs/{job['id']}/bundle")
    out["publish_manifest_url"] = _url(f"/api/aisha/v1/jobs/{job['id']}/publish")
    for c in out.get("clips", []):
        c["download_urls"] = _clip_urls(job, c)
        c["post_text"] = publish.post_text(c, p)
    return out


def _options(body: dict, platform) -> dict:
    over = dict(body.get("options") or {})
    for key in ("min_len", "max_len", "min_seconds", "max_seconds", "max_clips", "clip_count",
                "style", "caption_style", "layout", "quality", "captions", "range_start", "range_end"):
        if key in body:
            over[key] = body[key]
    return platforms.options_for(platform.id, over)


def _check_options(options: dict):
    try:
        jobs.clean_options(options)
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e) or "Those options aren't valid.")


@router.get("/health")
def health(uid: str = Depends(auth_user)):
    with ctx.as_user(uid):
        return {
            "ok": True,
            "name": "Aisha Video Studio",
            "version": "1",
            "jobs_running": jobs.running_now(),
            "jobs_waiting": jobs.waiting_total(),
            "ffmpeg": not tools.missing(),
        }


@router.get("/manifest")
def manifest(uid: str = Depends(auth_user)):
    return {
        "name": "Aisha Video Studio",
        "api_version": "v1",
        "schema": publish.SCHEMA,
        "description": "Local video clipping and publish-kit service built on PeakCut, with AutoClip-style platform presets.",
        "auth": "Send Authorization: Bearer <token> or X-Aisha-Token: <token>.",
        "capabilities": ["submit_url", "submit_upload", "poll_job", "publish_kit", "download_clip"],
        "limits": {
            "max_active_jobs": config.MAX_ACTIVE_JOBS_PER_USER,
            "max_upload_mb": config.MAX_UPLOAD_MB,
            "ffmpeg_ready": not tools.missing(),
        },
        "platforms": platforms.all_public(),
        "endpoints": {
            "health": "GET /api/aisha/v1/health",
            "create_url_job": "POST /api/aisha/v1/jobs {video_url, platform, options}",
            "create_upload_job": "POST /api/aisha/v1/jobs/upload multipart(file, platform, options)",
            "list_jobs": "GET /api/aisha/v1/jobs",
            "get_job": "GET /api/aisha/v1/jobs/{id}",
            "publish_manifest": "GET /api/aisha/v1/jobs/{id}/publish",
            "publish_kit": "GET /api/aisha/v1/jobs/{id}/bundle",
            "clip_video": "GET /api/aisha/v1/jobs/{id}/clips/{clip}/video",
            "clip_cover": "GET /api/aisha/v1/jobs/{id}/clips/{clip}/cover",
            "delete_job": "DELETE /api/aisha/v1/jobs/{id}",
        },
    }


@router.get("/platforms")
def list_platforms(uid: str = Depends(auth_user)):
    return {"platforms": platforms.all_public()}


@router.get("/jobs")
def list_jobs(request: Request, uid: str = Depends(auth_user)):
    with ctx.as_user(uid):
        rows = jobs.list_jobs()[:50]
    return {"jobs": [shape(request, j) for j in rows]}


@router.get("/jobs/{jid}")
def get_job(request: Request, jid: str, uid: str = Depends(auth_user)):
    return shape(request, _job(uid, jid))


@router.post("/jobs", status_code=202)
def create_url_job(request: Request, body: dict = Body(...), uid: str = Depends(auth_user)):
    url = str(body.get("video_url") or body.get("url") or "").strip()[:500]
    if not media.site_allowed(url):
        raise HTTPException(400, "Paste a full link to a YouTube, Twitch or Kick video.")
    platform = platforms.get(str(body.get("platform") or "vertical"))
    opts = _options(body, platform)
    _check_options(opts)
    _slot(uid)
    creator = str(body.get("creator") or "") or None
    if creator:
        with ctx.as_user(uid):
            from . import creators
            if not creators.get(creator):
                raise HTTPException(404, "No such creator")
    try:
        media.check_youtube(url)
    except media.YouTubeBlocked as e:
        raise HTTPException(503, str(e))
    with ctx.as_user(uid):
        try:
            job = jobs.create({"type": "url", "url": url, "name": url}, opts, creator=creator)
        except (ValueError, TypeError) as e:
            raise HTTPException(400, str(e) if isinstance(e, ValueError) else "Those options aren't valid.")
        job["platform"] = platform.id
        job["aisha"] = {"request_id": str(body.get("request_id") or "")[:120]}
        jobs.save(job)
        return shape(request, job)


@router.post("/jobs/upload", status_code=202)
def create_upload_job(request: Request, file: UploadFile = File(...), platform: str = Form("vertical"),
                      options: str = Form("{}"), title: str = Form(""),
                      uid: str = Depends(auth_user)):
    p = platforms.get(platform)
    try:
        over = json.loads(options)
    except ValueError:
        raise HTTPException(400, "options must be a JSON object.")
    if not isinstance(over, dict):
        raise HTTPException(400, "options must be a JSON object.")
    opts = platforms.options_for(p.id, over)
    _check_options(opts)
    _slot(uid)
    name = re.sub(r"[^\w.\- ]", "_", Path(file.filename or "video.mp4").name)[:120]
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_TYPES:
        raise HTTPException(400, "Upload a video file (mp4, mov, mkv or webm).")
    updir = config.TMP_DIR / "uploads"
    updir.mkdir(parents=True, exist_ok=True)
    tmp = updir / f"{os.urandom(8).hex()}{ext}"
    limit, size = config.MAX_UPLOAD_MB * 1024 * 1024, 0
    try:
        with tmp.open("wb") as out:
            while chunk := file.file.read(4 * 1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise HTTPException(413, f"That file is too big. The limit is {config.MAX_UPLOAD_MB} MB.")
                out.write(chunk)
        with ctx.as_user(uid):
            try:
                job = jobs.create({"type": "file", "path": str(tmp), "name": name}, opts, enqueue=False)
            except (ValueError, TypeError) as e:
                raise HTTPException(400, str(e) if isinstance(e, ValueError) else "Those options aren't valid.")
            dest = jobs.job_dir(job["id"]) / ("source" + ext)
            shutil.move(str(tmp), dest)
            job["source"]["path"] = str(dest)
            job["title"] = (title or name)[:160]
            job["platform"] = p.id
            jobs.enqueue(job)
            return shape(request, job)
    finally:
        tmp.unlink(missing_ok=True)


@router.get("/jobs/{jid}/publish")
def publish_manifest(request: Request, jid: str, uid: str = Depends(auth_user)):
    job = _job(uid, jid)
    if not publish.done_clips(job):
        raise HTTPException(409, "No finished clips yet.")
    p = platforms.get(job.get("platform") or "vertical")
    with ctx.as_user(uid):
        return publish.manifest(job, p, base_url=config.PUBLIC_URL)


@router.get("/jobs/{jid}/bundle")
def publish_bundle(jid: str, uid: str = Depends(auth_user)):
    job = _job(uid, jid)
    p = platforms.get(job.get("platform") or "vertical")
    try:
        with ctx.as_user(uid):
            f = publish.build_bundle(job, p)
    except ValueError as e:
        raise HTTPException(409, str(e))
    return FileResponse(f, filename=f"aisha-kit-{jid}.zip", media_type="application/zip",
                        headers={"Cache-Control": "private, no-store"},
                        background=BackgroundTask(lambda: f.unlink(missing_ok=True)))


def _clip(job: dict, cid: str) -> dict:
    if not CID_RE.fullmatch(cid or ""):
        raise HTTPException(404, "No such clip")
    clip = next((c for c in job.get("clips", []) if c["id"] == cid), None)
    if not clip or clip.get("status") != "done":
        raise HTTPException(404, "No finished clip with that id.")
    return clip


@router.get("/jobs/{jid}/clips/{cid}/video")
def clip_video(jid: str, cid: str, uid: str = Depends(auth_user)):
    job = _job(uid, jid)
    clip = _clip(job, cid)
    f = Path(job["dir"]) / "clips" / Path(str(clip.get("file") or "")).name
    if not f.is_file():
        raise HTTPException(404, "Clip file is missing.")
    return FileResponse(f, filename=f"{cid}.mp4", media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@router.get("/jobs/{jid}/clips/{cid}/cover")
def clip_cover(jid: str, cid: str, uid: str = Depends(auth_user)):
    job = _job(uid, jid)
    clip = _clip(job, cid)
    f = Path(job["dir"]) / "clips" / Path(str(clip.get("cover") or "")).name
    if not f.is_file():
        raise HTTPException(404, "Cover file is missing.")
    return FileResponse(f, filename=f"{cid}.jpg", media_type="image/jpeg", headers={"Cache-Control": "private, no-store"})


@router.delete("/jobs/{jid}")
def delete_job(jid: str, uid: str = Depends(auth_user)):
    job = _job(uid, jid)
    if job.get("status") in ("queued", "running", "live"):
        raise HTTPException(409, "This video is still being worked on.")
    with ctx.as_user(uid):
        jobs.delete(jid)
    return {"ok": True}
