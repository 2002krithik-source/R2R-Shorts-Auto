#!/usr/bin/env python3
"""
generate_and_upload_short.py
Fully updated to fix Pillow ANTIALIAS and Excel column issues.
"""

import os
import math
import glob
import time
import tempfile
import random
from pathlib import Path

import pandas as pd
from PIL import Image, ImageDraw, ImageFont
from gtts import gTTS
from moviepy.editor import (
    VideoFileClip,
    ImageClip,
    AudioFileClip,
    CompositeVideoClip,
    concatenate_videoclips,
)
import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ---------------- CONFIG ----------------
WIDTH = 1080
HEIGHT = 1920
ASSETS_DIR = Path("assets")
BG_VIDEO_GLOB = ASSETS_DIR / "backgrounds" / "*.mp4"
BG_IMAGE = ASSETS_DIR / "bg.jpg"
MUSIC_GLOB = ASSETS_DIR / "music" / "*.mp3"
LOGO_PATH = ASSETS_DIR / "logo.png"
FONT_PATH = ASSETS_DIR / "fonts" / "Roboto-Bold.ttf"
EXCEL_PATH = Path("riddles.xlsx")
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)
VOICE_LANG = "en"

TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Slide durations
HOOK_DURATION = 3.0
BODY_DURATION = 5.0
OPTIONS_DURATION = 5.0
ANSWER_DURATION = 3.0
FADE = 0.35
PADDING_AFTER_AUDIO = 0.5

# Text styling
TITLE_FONT_SIZE = 72
HOOK_FONT_SIZE = 64
BODY_FONT_SIZE = 56
OPTION_FONT_SIZE = 48
ANSWER_FONT_SIZE = 64
TEXT_COLOR = "white"
TEXT_MARGIN = 80

# ---------------- Helpers ----------------
def pick_background_clip(duration):
    vids = list(glob.glob(str(BG_VIDEO_GLOB)))
    if vids:
        path = random.choice(vids)
        clip = VideoFileClip(path).resize(height=HEIGHT)
        clip = clip.fx(lambda c: c.crop(x1=0, y1=0, x2=c.w, y2=c.h)) if clip.w != WIDTH else clip
        if clip.duration < duration:
            loops = math.ceil(duration / clip.duration)
            clips = [clip] * loops
            clip = concatenate_videoclips(clips).set_duration(duration)
        else:
            clip = clip.subclip(0, duration)
        return clip.resize((WIDTH, HEIGHT))

    if BG_IMAGE.exists():
        img = Image.open(BG_IMAGE)
        img = img.resize((WIDTH, HEIGHT), Image.Resampling.LANCZOS)
        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        img.save(tmp.name)
        tmp.close()
        return ImageClip(tmp.name).set_duration(duration)

    img = Image.new("RGB", (WIDTH, HEIGHT), color=(20, 20, 25))
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    tmp.close()
    return ImageClip(tmp.name).set_duration(duration)

def pick_music_clip(duration):
    files = list(glob.glob(str(MUSIC_GLOB)))
    if not files:
        return None
    path = random.choice(files)
    music = AudioFileClip(path)
    if music.duration < duration:
        loops = math.ceil(duration / music.duration)
        from moviepy.editor import concatenate_audioclips
        music = concatenate_audioclips([music] * loops).set_duration(duration)
    else:
        music = music.subclip(0, duration)
    return music.volumex(0.12)

def text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    return width, height

def render_text_image(text, fontsize, size=(WIDTH, HEIGHT), align="center"):
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype(str(FONT_PATH), fontsize) if Path(FONT_PATH).exists() else ImageFont.truetype("arial.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    maxw = size[0] - 2 * TEXT_MARGIN
    words = str(text).split()
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        wsize, _ = text_size(draw, test, font)
        if wsize <= maxw:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    _, line_h = text_size(draw, "Ay", font)
    line_h += 8
    total_h = line_h * len(lines)
    y = (size[1] - total_h) // 2

    for line in lines:
        w, h = text_size(draw, line, font)
        x = (size[0] - w) // 2 if align == "center" else TEXT_MARGIN
        # outline
        draw.text((x-2, y-2), line, font=font, fill="black")
        draw.text((x+2, y-2), line, font=font, fill="black")
        draw.text((x-2, y+2), line, font=font, fill="black")
        draw.text((x+2, y+2), line, font=font, fill="black")
        draw.text((x, y), line, font=font, fill=TEXT_COLOR)
        y += line_h

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    tmp.close()
    return tmp.name

def tts_save(text, out_path):
    tts = gTTS(text=text, lang=VOICE_LANG)
    tts.save(out_path)

def get_credentials_from_refresh_token(client_id, client_secret, refresh_token):
    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    resp = requests.post(TOKEN_URI, data=data)
    resp.raise_for_status()
    info = resp.json()
    token = info.get("access_token")
    if not token:
        raise RuntimeError("Failed to refresh access token.")
    return Credentials(
        token=token,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )

def upload_to_youtube(video_file, title, description, credentials, tags=None, privacy="public"):
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    media = MediaFileUpload(video_file, chunksize=-1, resumable=True, mimetype="video/*")
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {"title": title, "description": description, "tags": tags or [], "categoryId": "24"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
        },
        media_body=media,
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"Upload progress: {int(status.progress() * 100)}%")
    return response

# ---------------- Core flow ----------------
def load_next_riddle():
    if not EXCEL_PATH.exists():
        raise FileNotFoundError(f"{EXCEL_PATH} not found.")
    df = pd.read_excel(EXCEL_PATH, engine="openpyxl")
    df.columns = [c.strip().replace(" ", "_").lower() for c in df.columns]
    # Ensure required columns exist
    for n in ["title", "hook", "body", "option_1", "option_2", "option_3", "answer"]:
        if n not in df.columns:
            raise ValueError(f"Missing column in Excel: {n}")
    if "uploaded" not in df.columns:
        df["uploaded"] = False

    for idx, row in df.iterrows():
        v = row.get("uploaded")
        if not (str(v).strip().lower() in ["true", "1"]):
            return df, idx, row
    return df, None, None

def mark_uploaded_and_save(df, idx):
    df.at[idx, "uploaded"] = True
    df.to_excel(EXCEL_PATH, index=False, engine="openpyxl")

def build_short_and_upload(riddle_row, idx, creds, privacy="public"):
    hook = str(riddle_row.get("hook", ""))
    body = str(riddle_row.get("body", ""))
    opt1 = str(riddle_row.get("option_1", ""))
    opt2 = str(riddle_row.get("option_2", ""))
    opt3 = str(riddle_row.get("option_3", ""))
    answer = str(riddle_row.get("answer", ""))
    title_text = str(riddle_row.get("title", f"Riddle #{idx}"))

    total_audio_length = HOOK_DURATION + BODY_DURATION + OPTIONS_DURATION + ANSWER_DURATION + PADDING_AFTER_AUDIO
    bg_clip = pick_background_clip(total_audio_length)

    # Render slides
    hook_img = render_text_image(hook, HOOK_FONT_SIZE)
    body_img = render_text_image(body, BODY_FONT_SIZE)
    options_img = render_text_image(f"A) {opt1}\nB) {opt2}\nC) {opt3}", OPTION_FONT_SIZE)
    answer_img = render_text_image("Answer: " + answer, ANSWER_FONT_SIZE)

    hook_clip = ImageClip(hook_img).set_duration(HOOK_DURATION)
    body_clip = ImageClip(body_img).set_duration(BODY_DURATION)
    options_clip = ImageClip(options_img).set_duration(OPTIONS_DURATION)
    answer_clip = ImageClip(answer_img).set_duration(ANSWER_DURATION)

    slides = []
    for slide_clip in [hook_clip, body_clip, options_clip, answer_clip]:
        sub_bg = bg_clip.subclip(0, slide_clip.duration).set_duration(slide_clip.duration)
        comp = CompositeVideoClip([sub_bg, slide_clip.set_position(("center", "center"))]).fadein(FADE).fadeout(FADE)
        slides.append(comp)

    video = concatenate_videoclips(slides, method="compose")

    if Path(LOGO_PATH).exists():
        # Resize logo manually using Pillow
        if Path(LOGO_PATH).exists():
            logo_img = Image.open(LOGO_PATH)
            # Calculate new height to maintain aspect ratio
            w_percent = 140 / float(logo_img.width)
            h_size = int(float(logo_img.height) * w_percent)
            logo_img = logo_img.resize((140, h_size), Image.Resampling.LANCZOS)
            
            tmp_logo = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            logo_img.save(tmp_logo.name)
            tmp_logo.close()
        
            logo = ImageClip(tmp_logo.name).set_duration(video.duration).set_pos(("right", "top")).margin(right=32, top=32)

        video = CompositeVideoClip([video, logo])

    tts_text = f"{hook}. {body}. Option A: {opt1}. Option B: {opt2}. Option C: {opt3}. The answer is {answer}."
    tts_tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tts_save(tts_text, tts_tmp.name)
    tts_tmp.close()
    voice_audio = AudioFileClip(tts_tmp.name)

    music = pick_music_clip(voice_audio.duration + PADDING_AFTER_AUDIO)
    if music:
        from moviepy.editor import CompositeAudioClip
        final_audio = CompositeAudioClip([voice_audio, music.set_duration(voice_audio.duration + PADDING_AFTER_AUDIO)]).set_duration(voice_audio.duration + PADDING_AFTER_AUDIO)
    else:
        final_audio = voice_audio

    if video.duration < final_audio.duration:
        video = video.set_duration(final_audio.duration)
    video = video.set_audio(final_audio)

    out_path = OUTPUT_DIR / f"riddle_short_{idx}_{int(time.time())}.mp4"
    video.write_videofile(str(out_path), fps=24, codec="libx264", audio_codec="aac", threads=0, remove_temp=True)

    for f in [hook_img, body_img, options_img, answer_img, tts_tmp.name]:
        try: os.remove(f)
        except Exception: pass

    vid_title = f"{title_text} — Quick Riddle"
    description = f"{hook}\n\n{body}\n\nOptions:\nA) {opt1}\nB) {opt2}\nC) {opt3}\n\nAnswer: {answer}\n\n#riddle #shorts"
    resp = upload_to_youtube(str(out_path), vid_title, description, creds, tags=["riddle", "shorts", "puzzle"], privacy=privacy)
    return resp

def main():
    YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID")
    YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET")
    YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN")
    PRIVACY = os.environ.get("VIDEO_PRIVACY", "public")

    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise EnvironmentError("Set YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN env vars.")

    df, idx, row = load_next_riddle()
    if idx is None:
        print("No more unridden riddles found. Exiting.")
        return

    print(f"Selected row {idx}: {row.get('title', '')}")
    creds = get_credentials_from_refresh_token(YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN)

    resp = build_short_and_upload(row, idx, creds, privacy=PRIVACY)
    print("Upload response:", resp)

    mark_uploaded_and_save(df, idx)
    print(f"Marked row {idx} as Uploaded and saved {EXCEL_PATH}")

if __name__ == "__main__":
    main()
