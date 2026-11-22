"""Platform presets for short clips.

The presets are adapted from AutoClip's PlatformStrategy idea (MIT). They describe the
output shape, caption style, clip length, and post metadata limits that Aisha Video Studio
uses when it renders and packages clips.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Platform:
    id: str
    label: str
    aliases: tuple = ()
    layout: str = "crop"
    style: str = "bounce"
    min_len: int = 15
    max_len: int = 90
    max_clips: int = 6
    description_max: int = 2200
    hashtags: tuple = ()
    notes: str = ""
    aspect: str = "9:16"
    out_w: int = 1080
    out_h: int = 1920
    extra: dict = field(default_factory=dict)

    @property
    def resolution(self) -> str:
        return f"{self.out_w}x{self.out_h}"

    def public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "aspect": self.aspect,
            "resolution": self.resolution,
            "out_w": self.out_w,
            "out_h": self.out_h,
            "layout": self.layout,
            "style": self.style,
            "min_seconds": self.min_len,
            "max_seconds": self.max_len,
            "recommended_clips": self.max_clips,
            "description_max": self.description_max,
            "suggested_hashtags": list(self.hashtags),
            "notes": self.notes,
        }


PLATFORMS = {
    "tiktok": Platform(
        "tiktok", "TikTok", ("tik", "tt"), layout="crop", style="hype", min_len=15, max_len=90,
        max_clips=6, description_max=2200, hashtags=("fyp", "foryou", "viral"),
        notes="Fast hook, bold captions. TikTok accepts longer videos, but 15-90 seconds performs best.",
    ),
    "reels": Platform(
        "reels", "Instagram Reels", ("instagram", "ig", "instagram-reels"), layout="crop", style="bounce",
        min_len=15, max_len=90, max_clips=6, description_max=2200,
        hashtags=("reels", "reelsinstagram", "explore"),
        notes="Keep the first second visual. Up to 3 minutes is accepted.",
    ),
    "shorts": Platform(
        "shorts", "YouTube Shorts", ("youtube", "yt", "youtube-shorts"), layout="crop", style="bounce",
        min_len=15, max_len=60, max_clips=6, description_max=5000,
        hashtags=("shorts", "youtubeshorts"),
        notes="Shorts must be 3 minutes or less. 30-60 seconds is the safest range.",
    ),
    "facebook": Platform(
        "facebook", "Facebook Reels", ("fb", "facebook-reels"), layout="crop", style="bounce",
        min_len=15, max_len=90, max_clips=6, description_max=2200,
        hashtags=("reels", "facebookreels"),
        notes="Vertical 9:16 avoids black bars in the Reels player.",
    ),
    "douyin": Platform(
        "douyin", "Douyin", ("dy",), layout="blur", style="hype", min_len=15, max_len=90,
        max_clips=6, description_max=2000, hashtags=("douyin", "fyp"),
        notes="Blurred fill keeps the whole frame visible; burn captions for sound-off viewing.",
    ),
    "xiaohongshu": Platform(
        "xiaohongshu", "Xiaohongshu", ("xhs", "rednote", "little-red-book"), layout="blur", style="clean",
        min_len=20, max_len=90, max_clips=6, description_max=1000,
        hashtags=("xiaohongshu", "rednote"),
        notes="A calmer caption style and a useful first line work better than a loud hook.",
    ),
    "x": Platform(
        "x", "X (Twitter)", ("twitter", "x-twitter"), layout="crop", style="bounce",
        min_len=15, max_len=90, max_clips=6, description_max=280,
        hashtags=(), aspect="16:9", out_w=1920, out_h=1080,
        notes="Landscape 16:9. Keep the hook and captions inside the middle safe area for the X player.",
    ),
    "custom": Platform(
        "custom", "Custom format", ("custom-format",), layout="crop", style="bounce",
        min_len=15, max_len=180, max_clips=10, description_max=5000,
        hashtags=(), aspect="custom", out_w=1080, out_h=1920,
        notes="Choose your own width and height. Even numbers work best with the renderer.",
    ),
    "vertical": Platform(
        "vertical", "Vertical (generic)", ("default", "auto", "9:16"), layout="crop", style="bounce",
        min_len=15, max_len=90, max_clips=6, description_max=2200,
        hashtags=(), notes="Generic vertical preset when the destination is not one of the known apps.",
    ),
}

ALIASES = {a: p.id for p in PLATFORMS.values() for a in (p.id, *p.aliases)}


def get(name: str = "vertical") -> Platform:
    key = str(name or "vertical").strip().lower().replace("_", "-").replace(" ", "-")
    return PLATFORMS[ALIASES.get(key, "vertical")]


def options_for(name: str = "vertical", overrides: dict | None = None) -> dict:
    """PeakCut job options for a platform, with a small set of caller overrides.

    Caller overrides cannot escape PeakCut's own limits. The same clean_options pass runs again
    when the job is created, so this function is a convenience, not a trust boundary.
    """
    p = get(name)
    out = {
        "layout": p.layout,
        "style": p.style,
        "min_len": p.min_len,
        "max_len": p.max_len,
        "max_clips": p.max_clips,
        "captions": True,
        "quality": 720,
        "out_w": p.out_w,
        "out_h": p.out_h,
        "range_start": None,
        "range_end": None,
    }
    over = overrides if isinstance(overrides, dict) else {}
    aliases = {
        "min_seconds": "min_len",
        "max_seconds": "max_len",
        "clip_count": "max_clips",
        "caption_style": "style",
    }
    for key, value in over.items():
        key = aliases.get(key, key)
        if key not in out:
            continue
        if value in (None, "") and key not in ("range_start", "range_end"):
            continue
        out[key] = value
    out["min_len"] = max(5, min(120, int(float(out["min_len"]))))
    out["max_len"] = max(10, min(180, int(float(out["max_len"]))))
    if out["min_len"] >= out["max_len"]:
        out["min_len"] = max(5, out["max_len"] - 5)
    out["max_clips"] = max(1, min(20, int(float(out["max_clips"]))))
    for key in ("out_w", "out_h"):
        if out.get(key) not in (None, ""):
            out[key] = max(240, min(2160, int(float(out[key])) // 2 * 2))
        else:
            out[key] = p.out_w if key == "out_w" else p.out_h
    if out["layout"] not in ("blur", "zoom", "crop", "gameplay"):
        out["layout"] = p.layout
    if out["style"] not in ("bounce", "hype", "clean"):
        out["style"] = p.style
    return out


def all_public() -> list:
    return [p.public() for p in PLATFORMS.values()]
