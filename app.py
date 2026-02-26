from flask import Flask, request, send_file
from PIL import Image, ImageDraw, ImageFont
from datetime import datetime, timezone, timedelta
import io
from functools import lru_cache

app = Flask(__name__)

WIDTH, HEIGHT = 600, 140
FONT_SIZE = 80
FONT_PATH = "RobotoMono-Bold.ttf"

CACHE_SECONDS = 60

CTA_FRAMES = 5

SUPPORTED_LANG_TXT = {"en", "et"}
DEFAULT_LANG = "en"

LANG_TXT = {
    "en": {
        "cta": "START BETTING",
        "started": "MATCH HAS STARTED",
        "over": "MATCH IS OVER",
    },
    "et": {
        "cta": "ALUSTA PANUSTAMIST",
        "started": "MÄNG ON ALANUD",
        "over": "MÄNG ON LÄBI",
    },
}


def pick_lang() -> str:
    lang = (request.args.get("lang") or "").lower().strip()
    if lang in SUPPORTED_LANG_TXT:
        return lang

    best = request.accept_languages.best_match(list(SUPPORTED_LANG_TXT))
    return best or DEFAULT_LANG


def t(lang: str, key: str) -> str:
    return LANG_TXT.get(lang, LANG_TXT[DEFAULT_LANG]).get(key, LANG_TXT[DEFAULT_LANG][key])


@lru_cache(maxsize=64)
def get_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(FONT_PATH, size)


def render_timer_frame(text: str, lang: str) -> Image.Image:
    EMAIL_BG = "#ffffff"
    OUTER_COLOR = "#202027"
    INNER_COLOR = "#2C2C36"
    TIMER_COLOR = "#F69323"
    BORDER = 6

    img = Image.new("RGB", (WIDTH, HEIGHT), EMAIL_BG)
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=OUTER_COLOR)
    draw.rectangle((BORDER, BORDER, WIDTH - BORDER, HEIGHT - BORDER), fill=INNER_COLOR)

    PADDING_X = 18
    PADDING_Y = 10
    max_w = (WIDTH - 2 * BORDER) - 2 * PADDING_X
    max_h = (HEIGHT - 2 * BORDER) - 2 * PADDING_Y

    size = FONT_SIZE
    while size >= 18:
        font = get_font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        if text_w <= max_w and text_h <= max_h:
            break
        size -= 2

    font = get_font(size)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    inner_left = BORDER
    inner_top = BORDER
    inner_w = WIDTH - 2 * BORDER
    inner_h = HEIGHT - 2 * BORDER

    x = inner_left + (inner_w - text_w) // 2 - bbox[0]
    y = inner_top + (inner_h - text_h) // 2 - bbox[1]

    draw.text((x, y), text, fill=TIMER_COLOR, font=font)
    return img


@lru_cache(maxsize=512)
def render_frame_cached(lang: str, text: str) -> bytes:
    """
    Returns a PNG-encoded frame.
    PNG bytes are cheap to cache and fast to decode back into a PIL Image.
    """
    img = render_timer_frame(text, lang)
    b = io.BytesIO()
    img.save(b, format="PNG", optimize=True)
    return b.getvalue()


def frame_from_cache(lang: str, text: str) -> Image.Image:
    return Image.open(io.BytesIO(render_frame_cached(lang, text))).convert(
        "P", palette=Image.Palette.ADAPTIVE
    )


def get_timer_text(end_time, now, mode="gif", lang="en"):
    remaining = int((end_time - now).total_seconds())

    if remaining > 0:
        if mode == "png":
            minutes = remaining // 60
            h = minutes // 60
            m = minutes % 60
            return f"{h:02}:{m:02}"

        h = remaining // 3600
        m = (remaining % 3600) // 60
        s = remaining % 60
        return f"{h:02}:{m:02}:{s:02}"

    elapsed = abs(remaining)
    if elapsed <= 3600:
        return t(lang, "started")
    return t(lang, "over")


def get_cache_key(end_time, t_param=None, lang="en"):
    if t_param:
        try:
            rounded = int(t_param)
        except ValueError:
            rounded = int(datetime.now(timezone.utc).timestamp() // CACHE_SECONDS) * CACHE_SECONDS
    else:
        rounded = int(datetime.now(timezone.utc).timestamp() // CACHE_SECONDS) * CACHE_SECONDS

    return int(end_time.timestamp()), int(rounded), lang


@lru_cache(maxsize=256)
def generate_gif_cached(end_timestamp: int, rounded_timestamp: int, lang: str) -> bytes:
    end_time = datetime.fromtimestamp(end_timestamp, timezone.utc)
    now = datetime.fromtimestamp(rounded_timestamp, timezone.utc)

    if now > end_time + timedelta(hours=1):
        frame = frame_from_cache(lang, t(lang, "over"))
        buf = io.BytesIO()
        frame.save(
            buf,
            format="GIF",
            save_all=True,
            append_images=[],
            duration=1000,
            optimize=True,
            disposal=2,
        )
        return buf.getvalue()

    frames = []
    for i in range(60):
        frame_now = now + timedelta(seconds=i)
        remaining = int((end_time - frame_now).total_seconds())

        if remaining <= 0 and abs(remaining) <= 3600:
            text = t(lang, "started")
        elif remaining < -3600:
            text = t(lang, "over")
        else:
            if remaining > 0 and i >= (60 - CTA_FRAMES):
                text = t(lang, "cta")
            else:
                h = remaining // 3600
                m = (remaining % 3600) // 60
                s = remaining % 60
                text = f"{h:02}:{m:02}:{s:02}"

        frames.append(frame_from_cache(lang, text))

    buf = io.BytesIO()
    frames[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=frames[1:],
        duration=1000,
        optimize=True,
        disposal=2,
    )
    return buf.getvalue()


@app.route("/timer.gif")
def timer_gif():
    end = request.args.get("end")
    if not end:
        return "Missing 'end' parameter", 400

    lang = pick_lang()
    t_param = request.args.get("t")
    
    try:
        end_time = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return "Invalid end parameter", 400

    end_ts, rounded_ts, lang = get_cache_key(end_time, t_param, lang)
    gif_bytes = generate_gif_cached(end_ts, rounded_ts, lang)

    response = send_file(io.BytesIO(gif_bytes), mimetype="image/gif")
    response.headers["Cache-Control"] = f"public, max-age={CACHE_SECONDS}"

    response.headers["Vary"] = "Accept-Language"

    return response


@app.route("/timer.png")
    """
    Just for testing
    """
def timer_png():
    end = request.args.get("end")
    if not end:
        return "Missing 'end' parameter", 400

    lang = pick_lang()
    
    try:
        end_time = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return "Invalid end parameter", 400
        
    now = datetime.now(timezone.utc)

    text = get_timer_text(end_time, now, mode="png", lang=lang)
    img = render_timer_frame(text, lang)

    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)

    response = send_file(buf, mimetype="image/png")
    response.headers["Cache-Control"] = "public, max-age=60"
    response.headers["Vary"] = "Accept-Language"
    return response
