#!/usr/bin/env python3
"""
generate_and_upload_short.py

- Picks the first row in riddles.xlsx where Uploaded is empty/False.
- Generates a 9:16 short (1080x1920) with:
    * background (assets/backgrounds/*.mp4 or assets/bg.jpg fallback)
    * animated text slides (hook -> body -> options -> answer reveal)
    * voiceover (gTTS by default)
    * background music (assets/music/*.mp3 optional)
    * small logo overlay (assets/logo.png optional)
- Uploads to YouTube using OAuth refresh token
- Marks the row's Uploaded column TRUE and saves riddles.xlsx
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
BG_VIDEO_GLOB = ASSETS_DIR / "backgrounds" / "*.mp4"   # optional loopable vertical backgrounds
BG_IMAGE = ASSETS_DIR / "bg.jpg"                       # fallback
MUSIC_GLOB = ASSETS_DIR / "music" / "*.mp3"            # optional
LOGO_PATH = ASSETS_DIR / "logo.png"                    # optional
FONT_PATH = ASSETS_DIR / "fonts" / "Roboto-Bold.ttf"   # optional (better typography)
EXCEL_PATH = Path("riddles.xlsx")
OUTPUT_DIR = Path("outputs")
OUTPUT_DIR.mkdir(exist_ok=True)
VOICE_LANG = "en"

# YouTube OAuth
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Slide durations (seconds)
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
    # Prefer video backgrounds, else static image
    vids = list(glob.glob(str(BG_VIDEO_GLOB)))
    if vids:
        path = random.choice(vids)
        clip = VideoFileClip(path).resize(height=HEIGHT)
        # crop/center to WIDTH if needed
        clip = clip.fx(lambda c: c.crop(x1=0, y1=0, x2=c.w, y2=c.h)) if clip.w != WIDTH else clip
        # ensure long enough by looping
        if clip.duration < duration:
            loops = math.ceil(duration / clip.duration)
            clips = [clip] * loops
            clip = concatenate_videoclips(clips).set_duration(duration)
        else:
            clip = clip.subclip(0, duration)
        clip = clip.resize((WIDTH, HEIGHT))
        return clip
    # fallback to static image
    if BG_IMAGE.exists():
        img_clip = ImageClip(str(BG_IMAGE)).set_duration(duration).resize((WIDTH, HEIGHT))
        return img_clip
    # fallback to plain background
    img = Image.new("RGB", (WIDTH, HEIGHT), color=(20, 20, 25))
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    tmp.close()
    return ImageClip(tmp.name).set_duration(duration).resize((WIDTH, HEIGHT))

def pick_music_clip(duration):
    files = list(glob.glob(str(MUSIC_GLOB)))
    if not files:
        return None
    path = random.choice(files)
    music = AudioFileClip(path)
    # loop music if needed
    if music.duration < duration:
        loops = math.ceil(duration / music.duration)
        clips = [music] * loops
        from moviepy.editor import concatenate_audioclips
        music = concatenate_audioclips(clips).set_duration(duration)
    else:
        music = music.subclip(0, duration)
    # lower volume
    return music.volumex(0.12)

def render_text_image(text, fontsize, size=(WIDTH, HEIGHT), align="center"):
    # Renders text to transparent image and returns path
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    # font
    try:
        if Path(FONT_PATH).exists():
            font = ImageFont.truetype(str(FONT_PATH), fontsize)
        else:
            font = ImageFont.truetype("arial.ttf", fontsize)
    except Exception:
        font = ImageFont.load_default()

    maxw = size[0] - 2 * TEXT_MARGIN
    words = str(text).split()
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        wsize = draw.textsize(test, font=font)[0]
        if wsize <= maxw:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)

    line_h = draw.textsize("Ay", font=font)[1] + 8
    total_h = line_h * len(lines)
    y = (size[1] - total_h) // 2

    for line in lines:
        w, h = draw.textsize(line, font=font)
        x = (size[0] - w) // 2 if align == "center" else TEXT_MARGIN
        # draw outline for legibility
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
    creds = Credentials(
        token=token,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=TOKEN_URI,
        scopes=SCOPES,
    )
    return creds

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
    # Normalize column names
    df.columns = [c.strip() for c in df.columns]
    # Ensure columns exist
    needed = ["Title", "Hook", "Body", "Option 1", "Option 2", "Option 3", "Answer", "Uploaded"]
    for n in needed:
        if n not in df.columns:
            raise ValueError(f"Missing column in Excel: {n}")
    # If Uploaded column missing, create it
    if "Uploaded" not in df.columns:
        df["Uploaded"] = False

    # find first not uploaded
    for idx, row in df.iterrows():
        v = row.get("Uploaded")
        if not (str(v).strip().lower() in ["true", "1"]):
            return df, idx, row
    return df, None, None

def mark_uploaded_and_save(df, idx):
    df.at[idx, "Uploaded"] = True
    df.to_excel(EXCEL_PATH, index=False, engine="openpyxl")

def build_short_and_upload(riddle_row, idx, creds, privacy="public"):
    # Compose text slides
    hook = str(riddle_row["Hook"])
    body = str(riddle_row["Body"])
    opt1 = str(riddle_row["Option1"])
    opt2 = str(riddle_row["Option2"])
    opt3 = str(riddle_row["Option3"])
    answer = str(riddle_row["Answer"])
    title_text = str(riddle_row.get("Title", f"Riddle #{idx}"))

    # Duration estimate: sum durations
    total_audio_length = HOOK_DURATION + BODY_DURATION + OPTIONS_DURATION + ANSWER_DURATION + PADDING_AFTER_AUDIO
    bg_clip = pick_background_clip(total_audio_length)

    # Render image slides
    hook_img = render_text_image(hook, HOOK_FONT_SIZE)
    body_img = render_text_image(body, BODY_FONT_SIZE)
    options_text = f"A) {opt1}\nB) {opt2}\nC) {opt3}"
    options_img = render_text_image(options_text, OPTION_FONT_SIZE)
    answer_img = render_text_image("Answer: " + answer, ANSWER_FONT_SIZE)

    hook_clip = ImageClip(hook_img).set_duration(HOOK_DURATION)
    body_clip = ImageClip(body_img).set_duration(BODY_DURATION)
    options_clip = ImageClip(options_img).set_duration(OPTIONS_DURATION)
    answer_clip = ImageClip(answer_img).set_duration(ANSWER_DURATION)

    # Composite each slide over the background
    slides = []
    for slide_clip in [hook_clip, body_clip, options_clip, answer_clip]:
        sub_bg = bg_clip.subclip(0, slide_clip.duration).set_duration(slide_clip.duration)
        comp = CompositeVideoClip([sub_bg, slide_clip.set_position(("center", "center"))]).fadein(FADE).fadeout(FADE)
        slides.append(comp)

    video = concatenate_videoclips(slides, method="compose")
    # Add logo
    if Path(LOGO_PATH).exists():
        logo = ImageClip(str(LOGO_PATH)).set_duration(video.duration).resize(width=140).set_pos(("right", "top")).margin(right=32, top=32)
        video = CompositeVideoClip([video, logo])

    # TTS voiceover
    tts_text = f"{hook}. {body}. Option A: {opt1}. Option B: {opt2}. Option C: {opt3}. The answer is {answer}."
    tts_tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tts_save(tts_text, tts_tmp.name)
    tts_tmp.close()
    voice_audio = AudioFileClip(tts_tmp.name)

    # Optionally pick music and mix
    music = pick_music_clip(voice_audio.duration + PADDING_AFTER_AUDIO)
    if music:
        from moviepy.editor import CompositeAudioClip
        voice_audio = voice_audio.set_duration(voice_audio.duration)
        composed = CompositeAudioClip([voice_audio, music.set_duration(voice_audio.duration + PADDING_AFTER_AUDIO)])
        composed = composed.set_duration(voice_audio.duration + PADDING_AFTER_AUDIO)
        final_audio = composed
    else:
        final_audio = voice_audio

    # attach audio and ensure video length covers audio
    if video.duration < final_audio.duration:
        video = video.set_duration(final_audio.duration)
    video = video.set_audio(final_audio)

    # final file
    out_path = OUTPUT_DIR / f"riddle_short_{idx}_{int(time.time())}.mp4"
    video.write_videofile(str(out_path), fps=24, codec="libx264", audio_codec="aac", threads=0, remove_temp=True)
    # cleanup temp images & audio
    for f in [hook_img, body_img, options_img, answer_img, tts_tmp.name]:
        try:
            os.remove(f)
        except Exception:
            pass

    # Upload to YouTube
    vid_title = f"{title_text} — Quick Riddle"
    description = (
        f"{hook}\n\n{body}\n\nOptions:\nA) {opt1}\nB) {opt2}\nC) {opt3}\n\nAnswer: {answer}\n\n#riddle #shorts"
    )
    resp = upload_to_youtube(str(out_path), vid_title, description, creds, tags=["riddle", "shorts", "puzzle"], privacy=privacy)
    return resp

def main():
    # read env
    YT_CLIENT_ID = os.environ.get("YT_CLIENT_ID")
    YT_CLIENT_SECRET = os.environ.get("YT_CLIENT_SECRET")
    YT_REFRESH_TOKEN = os.environ.get("YT_REFRESH_TOKEN")
    PRIVACY = os.environ.get("VIDEO_PRIVACY", "public")  # public/unlisted/private

    if not (YT_CLIENT_ID and YT_CLIENT_SECRET and YT_REFRESH_TOKEN):
        raise EnvironmentError("Set YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN env vars in workflow/secrets")

    # Load next riddle
    df, idx, row = load_next_riddle()
    if idx is None:
        print("No more unridden riddles found (Uploaded column all True). Exiting.")
        return

    print(f"Selected row {idx}: {row.get('Title', '')}")
    creds = get_credentials_from_refresh_token(YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN)

    # Build, upload
    resp = build_short_and_upload(row, idx, creds, privacy=PRIVACY)
    print("Upload response:", resp)

    # Mark uploaded and save Excel
    mark_uploaded_and_save(df, idx)
    print(f"Marked row {idx} as Uploaded and saved {EXCEL_PATH}")

if __name__ == "__main__":
    main()
