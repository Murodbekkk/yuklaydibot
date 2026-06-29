"""
muzffa_bot.py — YUKLOVCHI BOT (Server Edition)
Always Data / VPS uchun optimizatsiya qilingan
- ffmpeg bo'lmasa ham ishlaydi (auto-detect)
- Instagram, YouTube, TikTok, Facebook va 1000+ sayt
- Millisekund tezlikda audio/video
- SQLite3 ma'lumotlar bazasi
- Doiraviy video, qidiruv sahifalash, admin panel
"""

import asyncio
import hashlib
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
import yt_dlp

# ═══════════════════════════════════════════════
#  SOZLAMALAR — faqat shu yerni o'zgartiring
# ═══════════════════════════════════════════════
TOKEN     = "Tokiningizni kiritting"
DB_FILE   = "muzffa.db"
VIDEO_DIR = "saved_videos"
PER_PAGE  = 10
TG_MAX    = 49 * 1024 * 1024   # 49 MB Telegram limiti

# ═══════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)

# Papka yaratish
os.makedirs(VIDEO_DIR, exist_ok=True)

# ═══════════════════════════════════════════════
#  FFMPEG AUTO-DETECT
# ═══════════════════════════════════════════════
def _find_ffmpeg() -> Optional[str]:
    """ffmpeg yo'lini topish — system, home, yoki static binary"""
    # 1. System PATH
    p = shutil.which("ffmpeg")
    if p:
        return p
    # 2. Home dir static binary
    for candidate in [
        os.path.expanduser("~/bin/ffmpeg"),
        os.path.expanduser("~/.local/bin/ffmpeg"),
        "/usr/local/bin/ffmpeg",
        "/opt/ffmpeg/ffmpeg",
    ]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def _find_ffprobe() -> Optional[str]:
    p = shutil.which("ffprobe")
    if p:
        return p
    for candidate in [
        os.path.expanduser("~/bin/ffprobe"),
        os.path.expanduser("~/.local/bin/ffprobe"),
        "/usr/local/bin/ffprobe",
        "/opt/ffmpeg/ffprobe",
    ]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


FFMPEG  = _find_ffmpeg()
FFPROBE = _find_ffprobe()
HAS_FFMPEG = bool(FFMPEG and FFPROBE)

# ═══════════════════════════════════════════════
#  YT-DLP UMUMIY SOZLAMALAR
# ═══════════════════════════════════════════════
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "*/*",
}

_INSTAGRAM_HEADERS = {
    "User-Agent": "Instagram 269.0.0.18.75 Android",
    "Accept-Language": "en-US",
    "X-IG-App-ID": "936619743392459",
}

YDL_COMMON = {
    "nocheckcertificate": True,
    "socket_timeout": 30,
    "retries": 3,
    "fragment_retries": 3,
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "ignoreerrors": False,
    "http_headers": _HEADERS,
}

# ═══════════════════════════════════════════════
#  SQLite3
# ═══════════════════════════════════════════════
def _db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_FILE, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    return c


def db_init():
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY,
            role    TEXT    DEFAULT 'admin',
            added   TEXT    DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS channels (
            channel_id TEXT PRIMARY KEY,
            title      TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS ads (
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            text   TEXT    NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings (
            k TEXT PRIMARY KEY,
            v TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS users (
            uid       INTEGER PRIMARY KEY,
            username  TEXT DEFAULT '',
            name      TEXT DEFAULT '',
            msg_count INTEGER DEFAULT 0,
            joined    TEXT DEFAULT (datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS circles (
            vid_key  TEXT PRIMARY KEY,
            uid      INTEGER,
            username TEXT DEFAULT '',
            name     TEXT DEFAULT '',
            filepath TEXT,
            created  TEXT DEFAULT (datetime('now','localtime'))
        );
        INSERT OR IGNORE INTO settings VALUES ('ads_on',      '0');
        INSERT OR IGNORE INTO settings VALUES ('ads_interval','5');
        INSERT OR IGNORE INTO settings VALUES ('owner_id',    '');
        """)


db_init()

# ── DB yordamchi funksiyalar ──
def cfg(k: str, d: str = "") -> str:
    with _db() as c:
        r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r else d


def set_cfg(k: str, v: str):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k, v))


def get_owner_id() -> Optional[int]:
    v = cfg("owner_id").strip()
    return int(v) if v.isdigit() else None


def is_owner(uid: int) -> bool:
    with _db() as c:
        return bool(c.execute(
            "SELECT 1 FROM admins WHERE user_id=? AND role='owner'", (uid,)
        ).fetchone())


def is_admin(uid: int) -> bool:
    with _db() as c:
        return bool(c.execute(
            "SELECT 1 FROM admins WHERE user_id=?", (uid,)
        ).fetchone())


def add_admin(uid: int, role: str = "admin"):
    with _db() as c:
        c.execute(
            "INSERT OR REPLACE INTO admins(user_id,role) VALUES(?,?)", (uid, role)
        )


def del_admin(uid: int):
    with _db() as c:
        c.execute(
            "DELETE FROM admins WHERE user_id=? AND role!='owner'", (uid,)
        )


def all_admins() -> list:
    with _db() as c:
        return c.execute("SELECT user_id,role FROM admins").fetchall()


def all_channels() -> list:
    with _db() as c:
        return c.execute("SELECT channel_id,title FROM channels").fetchall()


def add_channel(cid: str, title: str = ""):
    with _db() as c:
        c.execute(
            "INSERT OR IGNORE INTO channels(channel_id,title) VALUES(?,?)",
            (cid, title),
        )


def del_channel(cid: str):
    with _db() as c:
        c.execute("DELETE FROM channels WHERE channel_id=?", (cid,))


def all_ads() -> list:
    with _db() as c:
        return c.execute("SELECT id,text FROM ads WHERE active=1").fetchall()


def add_ad(text: str):
    with _db() as c:
        c.execute("INSERT INTO ads(text) VALUES(?)", (text,))


def del_ad(aid: int):
    with _db() as c:
        c.execute("DELETE FROM ads WHERE id=?", (aid,))


def upsert_user(uid: int, username: str, name: str):
    with _db() as c:
        c.execute(
            """INSERT INTO users(uid,username,name) VALUES(?,?,?)
               ON CONFLICT(uid) DO UPDATE SET
               username=excluded.username, name=excluded.name""",
            (uid, username or "", name or ""),
        )


def bump_msg(uid: int) -> int:
    with _db() as c:
        c.execute("UPDATE users SET msg_count=msg_count+1 WHERE uid=?", (uid,))
        r = c.execute("SELECT msg_count FROM users WHERE uid=?", (uid,)).fetchone()
    return r["msg_count"] if r else 1


def user_count() -> int:
    with _db() as c:
        return c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]


def all_user_ids() -> list:
    with _db() as c:
        return [r["uid"] for r in c.execute("SELECT uid FROM users").fetchall()]


def save_circle(vid_key: str, uid: int, username: str, name: str, filepath: str):
    with _db() as c:
        c.execute(
            """INSERT OR REPLACE INTO circles(vid_key,uid,username,name,filepath)
               VALUES(?,?,?,?,?)""",
            (vid_key, uid, username or "", name or "", filepath),
        )


def all_circles() -> list:
    with _db() as c:
        return c.execute(
            "SELECT * FROM circles ORDER BY created DESC LIMIT 50"
        ).fetchall()


def get_circle(vid_key: str) -> Optional[sqlite3.Row]:
    with _db() as c:
        return c.execute(
            "SELECT * FROM circles WHERE vid_key=?", (vid_key,)
        ).fetchone()


# ═══════════════════════════════════════════════
#  FSM
# ═══════════════════════════════════════════════
class St(StatesGroup):
    add_admin = State()
    del_admin = State()
    add_ch    = State()
    del_ch    = State()
    add_ad    = State()
    del_ad    = State()
    broadcast = State()


# ═══════════════════════════════════════════════
#  SUBSCRIPTION
# ═══════════════════════════════════════════════
async def check_sub(bot: Bot, uid: int) -> list:
    ns = []
    for ch in all_channels():
        try:
            m = await bot.get_chat_member(ch["channel_id"], uid)
            if m.status in ("left", "kicked", "banned"):
                ns.append(ch)
        except Exception:
            ns.append(ch)
    return ns


def sub_kb(chs: list) -> InlineKeyboardMarkup:
    btns = [
        [InlineKeyboardButton(
            text=f"📢 {ch['title'] or ch['channel_id']}",
            url="https://t.me/" + ch["channel_id"].lstrip("@"),
        )]
        for ch in chs
    ]
    btns.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


# ═══════════════════════════════════════════════
#  KLAVIATURALAR
# ═══════════════════════════════════════════════
def admin_kb() -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="👤 Admin qo'shish"),  KeyboardButton(text="❌ Admin o'chirish")],
        [KeyboardButton(text="📢 Kanal qo'shish"),  KeyboardButton(text="🗑 Kanal o'chirish")],
        [KeyboardButton(text="📋 Kanallar"),         KeyboardButton(text="👥 Adminlar")],
        [KeyboardButton(text="📣 Reklama qo'shish"), KeyboardButton(text="🗑 Reklama o'chirish")],
        [KeyboardButton(text="📊 Statistika"),       KeyboardButton(text="🔄 Reklama on/off")],
        [KeyboardButton(text="📢 Xabar yuborish"),   KeyboardButton(text="⭕ Doiraviy videolar")],
        [KeyboardButton(text="❌ Yopish")],
    ]
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


ADMIN_BTNS = {
    "👤 Admin qo'shish", "❌ Admin o'chirish",
    "📢 Kanal qo'shish", "🗑 Kanal o'chirish",
    "📋 Kanallar",        "👥 Adminlar",
    "📣 Reklama qo'shish","🗑 Reklama o'chirish",
    "📊 Statistika",      "🔄 Reklama on/off",
    "📢 Xabar yuborish",  "⭕ Doiraviy videolar",
    "❌ Yopish",
}

# ═══════════════════════════════════════════════
#  QIDIRUV + SAHIFALASH (raqamli tugmalar)
# ═══════════════════════════════════════════════
_cache: dict = {}


def fmt_dur(s) -> str:
    if not s:
        return ""
    s = int(s)
    m, s2 = divmod(s, 60)
    h, m  = divmod(m, 60)
    return f"{h}:{m:02d}:{s2:02d}" if h else f"{m}:{s2:02d}"


def clean_name(a: str) -> str:
    for sfx in [" - Topic", " - topic", "VEVO", " Official", " official", " - Official"]:
        a = a.replace(sfx, "")
    return a.strip()


async def yt_search(query: str, limit: int = 50) -> list:
    loop = asyncio.get_event_loop()
    hold: dict = {}

    def _do():
        opts = {
            **YDL_COMMON,
            "extract_flat": "in_playlist",
            "default_search": f"ytsearch{limit}",
            "skip_download": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                hold["i"] = ydl.extract_info(query, download=False)
            except Exception as ex:
                hold["err"] = str(ex)

    try:
        await asyncio.wait_for(loop.run_in_executor(None, _do), timeout=30)
    except Exception:
        return []

    entries = (hold.get("i") or {}).get("entries") or []
    res = []
    for e in entries:
        if not e or not e.get("id"):
            continue
        artist = clean_name(e.get("uploader") or e.get("channel") or "")
        res.append({
            "title":  e.get("title", "?"),
            "artist": artist,
            "dur":    e.get("duration", 0),
            "url":    f"https://www.youtube.com/watch?v={e['id']}",
        })
    return res


def _result_text(cid: str, page: int, total: int) -> str:
    data  = _cache.get(cid, {})
    res   = data.get("res", [])
    query = data.get("q", "")
    s     = page * PER_PAGE
    e     = min(s + PER_PAGE, total)
    pages = (total + PER_PAGE - 1) // PER_PAGE

    lines = [f"<b>{query}</b> bo'yicha natijalar:\n"]
    for i, r in enumerate(res[s:e]):
        num  = s + i + 1
        art  = r["artist"]
        line = f"{num}. {art} - {r['title']}" if art else f"{num}. {r['title']}"
        dur  = fmt_dur(r["dur"])
        if dur:
            line += f"  {dur}"
        lines.append(line)
    lines.append(f"\n📄 Sahifa {page+1}/{pages}")
    return "\n".join(lines)


def _result_kb(cid: str, page: int, total: int) -> InlineKeyboardMarkup:
    data = _cache.get(cid, {})
    s    = page * PER_PAGE
    e    = min(s + PER_PAGE, total)
    cnt  = e - s

    # Raqamli tugmalar: 5 + 5
    row1, row2 = [], []
    for i in range(cnt):
        idx = s + i
        btn = InlineKeyboardButton(
            text=str(idx + 1),
            callback_data=f"dl:{cid}:{idx}",
        )
        (row1 if i < 5 else row2).append(btn)

    rows = []
    if row1: rows.append(row1)
    if row2: rows.append(row2)

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"pg:{cid}:{page-1}"))
    nav.append(InlineKeyboardButton(text="✖️", callback_data="close"))
    if e < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"pg:{cid}:{page+1}"))
    rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _expire(cid: str, delay: int = 600):
    await asyncio.sleep(delay)
    _cache.pop(cid, None)


# ═══════════════════════════════════════════════
#  FFMPEG — subprocess.run (Windows+Linux mos)
# ═══════════════════════════════════════════════
def _run(cmd: list, timeout: int = 180) -> tuple:
    """(returncode, stderr_text)"""
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return r.returncode, r.stderr.decode(errors="ignore")
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except FileNotFoundError:
        return -2, f"{cmd[0]} topilmadi"
    except Exception as ex:
        return -3, str(ex)


async def _run_async(cmd: list, timeout: int = 180) -> tuple:
    loop = asyncio.get_event_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, _run, cmd, timeout),
        timeout=timeout + 10,
    )


async def _probe_duration(path: str) -> float:
    if not FFPROBE:
        return 30.0
    rc, out = await _run_async(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path], 15
    )
    try:
        return float(out.strip().split("\n")[0])
    except Exception:
        return 30.0


async def _has_audio(path: str) -> bool:
    if not FFPROBE:
        return True
    rc, out = await _run_async(
        [FFPROBE, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name",
         "-of", "default=noprint_wrappers=1:nokey=1", path], 15
    )
    return bool(out.strip())


async def make_circle(src: str, dst: str) -> tuple:
    """
    Video → doiraviy video (512×512, ≤59s)
    Returns: (ok, error_msg)
    """
    if not HAS_FFMPEG:
        return False, "Serverda ffmpeg o'rnatilmagan"

    p = Path(src)
    if not p.exists() or p.stat().st_size < 100:
        return False, "Video fayli topilmadi"

    dur    = await _probe_duration(src)
    haudio = await _has_audio(src)
    t      = min(dur if dur > 0 else 30.0, 59.0)
    vf     = r"crop=min(iw\,ih):min(iw\,ih),scale=512:512,format=yuv420p"

    cmd = [
        FFMPEG, "-y",
        "-i", src,
        "-t", f"{t:.2f}",
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-pix_fmt", "yuv420p",
        "-profile:v", "baseline",
        "-level", "3.0",
        "-r", "30",
    ]
    if haudio:
        cmd += ["-c:a", "aac", "-b:a", "64k", "-ac", "1", "-ar", "44100"]
    else:
        cmd += ["-an"]
    cmd += ["-movflags", "+faststart", dst]

    rc, err = await _run_async(cmd, 180)

    if rc == -2:
        return False, "Serverda ffmpeg o'rnatilmagan"
    if rc == -1:
        return False, "Doiraviy video yaratish vaqti tugadi"
    if rc != 0:
        log.warning(f"circle ffmpeg rc={rc}: {err[-150:]}")
        if "Invalid data" in err or "moov atom" in err:
            return False, "Video fayli o'qib bo'lmadi"
        return False, "Doiraviy video yaratishda xatolik"

    op = Path(dst)
    if not op.exists() or op.stat().st_size < 500:
        return False, "Natija fayli bo'sh"
    return True, ""


# ═══════════════════════════════════════════════
#  YDL XATO XABARLARI
# ═══════════════════════════════════════════════
def _ydl_err(e: str) -> str:
    el = e.lower()
    if "private"      in el: return "Yopiq (private) kontent"
    if "unavailable"  in el: return "Media mavjud emas"
    if "copyright"    in el: return "Mualliflik cheklovi"
    if "login"        in el: return "Login talab qilinadi"
    if "geo"          in el: return "Sizning mamlakatda mavjud emas"
    if "age"          in el: return "Yosh cheklovi"
    if "403"          in e : return "Ruxsat yo'q (403) — qayta urinib ko'ring"
    if "404"          in e : return "Sahifa topilmadi (404)"
    if "ssl"          in el: return "Ulanish xatoligi — qayta urinib ko'ring"
    return "Yuklab bo'lmadi"


# ═══════════════════════════════════════════════
#  AUDIO YUKLAB OLISH
# ═══════════════════════════════════════════════
def _audio_opts(tmp: str, url: str) -> dict:
    """URL ga qarab audio opts"""
    base = {
        **YDL_COMMON,
        "outtmpl": os.path.join(tmp, "%(title).80s.%(ext)s"),
    }

    is_insta = "instagram.com" in url
    is_tiktok = "tiktok.com" in url

    if is_insta:
        base["http_headers"] = _INSTAGRAM_HEADERS

    if HAS_FFMPEG:
        base["format"] = "bestaudio/best"
        base["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        # ffmpeg yo'q — mp3/m4a/webm to'g'ridan yuklash
        base["format"] = (
            "bestaudio[ext=mp3]/bestaudio[ext=m4a]/"
            "bestaudio[ext=webm]/bestaudio/best"
        )
    return base


async def dl_audio(url: str, tmp: str) -> tuple:
    """(fp, title, artist, album, dur, err)"""
    loop = asyncio.get_event_loop()
    hold: dict = {}

    def _do():
        opts = _audio_opts(tmp, url)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            hold["i"] = (
                info["entries"][0] if (info and "entries" in info) else info
            )

    try:
        await asyncio.wait_for(loop.run_in_executor(None, _do), timeout=180)
    except asyncio.TimeoutError:
        return None, None, None, None, None, "Yuklab olish vaqti tugadi"
    except yt_dlp.utils.DownloadError as ex:
        return None, None, None, None, None, _ydl_err(str(ex))
    except Exception as ex:
        log.warning(f"dl_audio: {ex}")
        return None, None, None, None, None, "Xatolik yuz berdi"

    i = hold.get("i") or {}
    files = [f for f in Path(tmp).iterdir() if f.is_file()]
    if not files:
        return None, None, None, None, None, "Fayl yuklanmadi"

    fp     = str(max(files, key=lambda f: f.stat().st_size))
    artist = clean_name(i.get("artist") or i.get("uploader") or i.get("channel") or "")
    return (
        fp,
        i.get("title", "Noma'lum"),
        artist,
        i.get("album", ""),
        i.get("duration", 0),
        None,
    )


# ═══════════════════════════════════════════════
#  VIDEO YUKLAB OLISH
# ═══════════════════════════════════════════════
def _video_opts(tmp: str, url: str, quality: str = "720") -> dict:
    base = {
        **YDL_COMMON,
        "outtmpl": os.path.join(tmp, "%(title).80s.%(ext)s"),
        "concurrent_fragment_downloads": 4,
    }

    is_insta  = "instagram.com" in url
    is_tiktok = "tiktok.com"    in url

    if is_insta:
        base["http_headers"] = _INSTAGRAM_HEADERS

    h = quality
    if HAS_FFMPEG:
        if quality == "best":
            fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        else:
            fmt = (
                f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
                f"best[height<={h}][ext=mp4]/best[height<={h}]/best[ext=mp4]/best"
            )
        base["merge_output_format"] = "mp4"
    else:
        # ffmpeg yo'q — tayyor mp4
        if quality == "best":
            fmt = "best[ext=mp4]/best"
        else:
            fmt = f"best[height<={h}][ext=mp4]/best[height<={h}]/best[ext=mp4]/best"

    base["format"] = fmt
    return base


async def dl_video(url: str, tmp: str, quality: str = "720") -> tuple:
    """(fp, title, artist, dur, err)"""
    loop = asyncio.get_event_loop()
    hold: dict = {}

    def _do():
        opts = _video_opts(tmp, url, quality)
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            hold["i"] = (
                info["entries"][0] if (info and "entries" in info) else info
            )

    try:
        await asyncio.wait_for(loop.run_in_executor(None, _do), timeout=600)
    except asyncio.TimeoutError:
        return None, None, None, None, "Yuklab olish vaqti tugadi"
    except yt_dlp.utils.DownloadError as ex:
        shutil.rmtree(tmp, ignore_errors=True)
        return None, None, None, None, _ydl_err(str(ex))
    except Exception as ex:
        log.warning(f"dl_video: {ex}")
        shutil.rmtree(tmp, ignore_errors=True)
        return None, None, None, None, "Xatolik yuz berdi"

    i = hold.get("i") or {}
    files = [f for f in Path(tmp).iterdir() if f.is_file()]
    if not files:
        return None, None, None, None, "Fayl yuklanmadi"

    fp     = str(max(files, key=lambda f: f.stat().st_size))
    artist = clean_name(i.get("uploader") or i.get("channel") or "")
    return fp, i.get("title", "Video"), artist, i.get("duration", 0), None


# ═══════════════════════════════════════════════
#  SEND HELPERS
# ═══════════════════════════════════════════════
def _acap(title: str, artist: str, album: str, dur: int) -> str:
    lines = [f"<b>{title}</b>"]
    if artist: lines.append(f"<i>{artist}</i>")
    if album:  lines.append(f"💿 {album}")
    d = fmt_dur(dur)
    if d:      lines.append(f"⏱ {d}")
    lines.append("\n🎵 @muzffabot")
    return "\n".join(lines)


def _vcap(title: str, artist: str, dur: int) -> str:
    lines = [f"<b>{title}</b>"]
    if artist: lines.append(f"<i>{artist}</i>")
    d = fmt_dur(dur)
    if d:      lines.append(f"⏱ {d}")
    lines.append("\n🎬 @muzffabot")
    return "\n".join(lines)


def _circle_kb(vid_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="⭕ Doiraviy videoga aylantirish",
            callback_data=f"circle:{vid_key}",
        )
    ]])


async def send_audio(
    msg: Message,
    fp: str,
    title: str,
    artist: str,
    album: str,
    dur: int,
):
    cap = _acap(title, artist, album, dur)
    ext = Path(fp).suffix.lower()
    inp = FSInputFile(fp)
    if ext in (".mp3", ".m4a", ".ogg", ".wav", ".flac", ".aac", ".opus", ".webm"):
        await msg.answer_audio(
            audio=inp,
            caption=cap,
            parse_mode=ParseMode.HTML,
            title=title,
            performer=artist or None,
            duration=int(dur) if dur else None,
        )
    else:
        await msg.answer_document(document=inp, caption=cap, parse_mode=ParseMode.HTML)


async def send_video(
    msg: Message,
    fp: str,
    title: str,
    artist: str,
    dur: int,
    uid: int,
    username: str,
    name: str,
):
    """Video yuborish + doiraviy video tugmasi"""
    vid_key  = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:16]
    saved_fp = os.path.join(VIDEO_DIR, f"{vid_key}.mp4")
    try:
        shutil.copy2(fp, saved_fp)
        save_circle(vid_key, uid, username, name, saved_fp)
    except Exception:
        saved_fp = fp

    cap = _vcap(title, artist, dur)
    inp = FSInputFile(fp)
    kb  = _circle_kb(vid_key) if HAS_FFMPEG else None
    ext = Path(fp).suffix.lower()

    if ext in (".mp4", ".mov", ".m4v"):
        await msg.answer_video(
            video=inp,
            caption=cap,
            parse_mode=ParseMode.HTML,
            duration=int(dur) if dur else None,
            reply_markup=kb,
        )
    else:
        await msg.answer_document(
            document=inp, caption=cap, parse_mode=ParseMode.HTML, reply_markup=kb
        )


async def maybe_ad(bot: Bot, uid: int):
    if cfg("ads_on") != "1":
        return
    ads = all_ads()
    if not ads:
        return
    cnt      = bump_msg(uid)
    interval = int(cfg("ads_interval", "5"))
    if cnt % interval == 0:
        import random
        ad = random.choice(ads)
        try:
            await bot.send_message(
                uid,
                f"📣 <b>Reklama:</b>\n\n{ad['text']}",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass


# ═══════════════════════════════════════════════
#  BRAILLE ANIMATSIYA
# ═══════════════════════════════════════════════
_DOTS = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]


async def _animate(msg: Message, frames: int = 12):
    for i in range(frames):
        f = "●" * (i % 5 + 1)
        e = "○" * (4 - i % 5)
        try:
            await msg.edit_text(
                f"{_DOTS[i % len(_DOTS)]} <b>Qo'shiq tanilmoqda...</b>\n\n"
                f"{f}{e}\n\n<i>Shazam uslubida qidirilmoqda</i>",
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            pass
        await asyncio.sleep(0.4)


# ═══════════════════════════════════════════════
#  ROUTER
# ═══════════════════════════════════════════════
router = Router()


# ── /start ──
@router.message(CommandStart())
async def on_start(message: Message, bot: Bot):
    u = message.from_user
    upsert_user(u.id, u.username or "", u.first_name or "")
    ns = await check_sub(bot, u.id)
    if ns:
        await message.answer(
            "⛔ <b>Botdan foydalanish uchun obuna bo'ling:</b>",
            reply_markup=sub_kb(ns),
            parse_mode=ParseMode.HTML,
        )
        return
    name = u.first_name or "do'stim"
    ffmpeg_note = "" if HAS_FFMPEG else "\n⚠️ <i>Doiraviy video hozircha mavjud emas</i>"
    await message.answer(
        f"👋 Salom, <b>{name}</b>!\n\n"
        "🎵 <b>Yuklovchi Bot</b> — tez va ishonchli!\n\n"
        "<b>Imkoniyatlar:</b>\n"
        "🔍 Qo'shiq nomi → 10 ta natija + sahifalash\n"
        "🎙 Audio jo'nating → Shazam uslubida taniydi\n"
        "🎬 Video jo'nating → ⭕ Doiraviy videoga aylantiradi\n"
        "🔗 Link tashlang → yuklab beradi\n\n"
        "✅ YouTube · Instagram · TikTok · Facebook · Twitter va boshqalar"
        f"{ffmpeg_note}\n\n"
        "🚀 Boshlang!",
        parse_mode=ParseMode.HTML,
    )


# ── /setowner ──
@router.message(Command("setowner"))
async def on_setowner(message: Message):
    with _db() as c:
        existing = c.execute("SELECT 1 FROM admins WHERE role='owner'").fetchone()
    if existing and not is_owner(message.from_user.id):
        await message.answer("❌ Ega allaqachon belgilangan.")
        return
    uid = message.from_user.id
    add_admin(uid, "owner")
    set_cfg("owner_id", str(uid))
    await message.answer(
        f"✅ Siz botning <b>egasisiz</b>!\n"
        f"ID: <code>{uid}</code>\n\n"
        "/admin — panel",
        parse_mode=ParseMode.HTML,
    )


# ── /admin ──
@router.message(Command("admin"))
async def on_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Admin huquqingiz yo'q.")
        return
    await message.answer(
        "🛠 <b>Admin paneli</b>",
        reply_markup=admin_kb(),
        parse_mode=ParseMode.HTML,
    )


@router.message(F.text == "❌ Yopish")
async def on_close_panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    await message.answer("Yopildi.", reply_markup=ReplyKeyboardRemove())


# ── Obuna callback ──
@router.callback_query(F.data == "check_sub")
async def on_check_sub(cb: CallbackQuery, bot: Bot):
    ns = await check_sub(bot, cb.from_user.id)
    if ns:
        await cb.answer("❌ Hali obuna bo'lmadingiz!", show_alert=True)
        try:
            await cb.message.edit_reply_markup(reply_markup=sub_kb(ns))
        except Exception:
            pass
    else:
        try:
            await cb.message.delete()
        except Exception:
            pass
        u = cb.from_user
        upsert_user(u.id, u.username or "", u.first_name or "")
        await cb.message.answer(
            "✅ <b>Obuna tasdiqlandi!</b>\n\n"
            "Qo'shiq nomi yozing yoki link tashlang 🎵",
            parse_mode=ParseMode.HTML,
        )


# ── Yopish ──
@router.callback_query(F.data == "close")
async def on_close_cb(cb: CallbackQuery):
    try:
        await cb.message.delete()
    except Exception:
        pass
    await cb.answer()


# ── Sahifalash ──
@router.callback_query(F.data.startswith("pg:"))
async def on_page(cb: CallbackQuery):
    _, cid, pg_s = cb.data.split(":")
    pg   = int(pg_s)
    data = _cache.get(cid)
    if not data:
        await cb.answer("Qidiruv eskirdi, qayta yozing.", show_alert=True)
        return
    data["page"] = pg
    total = len(data["res"])
    try:
        await cb.message.edit_text(
            _result_text(cid, pg, total),
            reply_markup=_result_kb(cid, pg, total),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        pass
    await cb.answer()


# ── Raqam bosilganda audio yuklash ──
@router.callback_query(F.data.startswith("dl:"))
async def on_dl(cb: CallbackQuery, bot: Bot):
    _, cid, idx_s = cb.data.split(":")
    idx  = int(idx_s)
    data = _cache.get(cid)
    if not data:
        await cb.answer("Eskirgan, qayta qidiring.", show_alert=True)
        return
    res = data["res"]
    if idx >= len(res):
        await cb.answer("Topilmadi.", show_alert=True)
        return

    r = res[idx]
    await cb.answer(f"⬇️ {r['title'][:40]}...")
    tmp = tempfile.mkdtemp()
    sm  = await cb.message.answer(
        f"⬇️ <b>Yuklanmoqda...</b>\n"
        f"<i>{r['artist']} — {r['title']}</i>",
        parse_mode=ParseMode.HTML,
    )
    try:
        fp, title, artist, album, dur, err = await dl_audio(r["url"], tmp)
        if err or not fp:
            await sm.edit_text(f"⚠️ {err or 'Yuklab bo\'lmadi.'}")
            return
        if Path(fp).stat().st_size > TG_MAX:
            await sm.edit_text("⚠️ Fayl 50 MB dan katta.")
            return
        await sm.edit_text("📤 Yuborilmoqda...")
        await send_audio(cb.message, fp, title, artist, album or "", dur or 0)
        await sm.delete()
    except Exception as ex:
        log.warning(f"on_dl: {ex}")
        try:
            await sm.edit_text("⚠️ Xatolik. Qayta urinib ko'ring.")
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    await maybe_ad(bot, cb.from_user.id)


# ── Doiraviy video callback ──
@router.callback_query(F.data.startswith("circle:"))
async def on_circle_cb(cb: CallbackQuery, bot: Bot):
    if not HAS_FFMPEG:
        await cb.answer("Serverda ffmpeg o'rnatilmagan.", show_alert=True)
        return

    vid_key = cb.data.split(":", 1)[1]
    row     = get_circle(vid_key)
    if not row or not Path(row["filepath"]).exists():
        await cb.answer("⚠️ Video topilmadi. Qayta yuboring.", show_alert=True)
        return

    await cb.answer("⭕ Tayorlanmoqda...")
    sm  = await cb.message.answer(
        "⭕ <b>Doiraviy video yaratilmoqda...</b>",
        parse_mode=ParseMode.HTML,
    )
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "circle.mp4")
    try:
        ok, err_msg = await make_circle(row["filepath"], out)
        if ok:
            # Egaga yuborish
            owner_id = get_owner_id()
            if owner_id and owner_id != cb.from_user.id:
                try:
                    u  = cb.from_user
                    un = f" (@{u.username})" if u.username else ""
                    await bot.send_message(
                        owner_id,
                        f"⭕ <b>Yangi doiraviy video</b>\n"
                        f"👤 {u.first_name}{un}\n🆔 <code>{u.id}</code>",
                        parse_mode=ParseMode.HTML,
                    )
                    await bot.send_video_note(owner_id, video_note=FSInputFile(out))
                except Exception:
                    pass
            await sm.edit_text("📤 Yuborilmoqda...")
            await cb.message.answer_video_note(video_note=FSInputFile(out))
            await sm.delete()
        else:
            await sm.edit_text(f"⚠️ {err_msg}")
    except Exception as ex:
        log.warning(f"circle_cb: {ex}")
        try:
            await sm.edit_text("⚠️ Xatolik yuz berdi.")
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ── Ega: doiraviy videolar ro'yxati ──
def _circles_kb(rows: list) -> InlineKeyboardMarkup:
    btns = []
    for r in rows[:20]:
        un    = f"@{r['username']}" if r["username"] else r["name"] or str(r["uid"])
        label = f"👤 {un} — {r['created'][:16]}"
        if len(label) > 64:
            label = label[:61] + "..."
        btns.append([InlineKeyboardButton(
            text=label, callback_data=f"show_c:{r['vid_key']}"
        )])
    btns.append([InlineKeyboardButton(text="✖️ Yopish", callback_data="close")])
    return InlineKeyboardMarkup(inline_keyboard=btns)


@router.callback_query(F.data == "owner_circles")
async def on_owner_circles(cb: CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("❌ Faqat ega!", show_alert=True)
        return
    rows = all_circles()
    if not rows:
        await cb.answer("Doiraviy videolar yo'q.", show_alert=True)
        return
    try:
        await cb.message.edit_text(
            f"⭕ <b>Doiraviy videolar ({len(rows)} ta):</b>\n\nBirini tanlang:",
            reply_markup=_circles_kb(rows),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        await cb.message.answer(
            f"⭕ <b>Doiraviy videolar ({len(rows)} ta):</b>",
            reply_markup=_circles_kb(rows),
            parse_mode=ParseMode.HTML,
        )
    await cb.answer()


@router.callback_query(F.data.startswith("show_c:"))
async def on_show_circle(cb: CallbackQuery):
    if not is_owner(cb.from_user.id):
        await cb.answer("❌ Faqat ega!", show_alert=True)
        return
    if not HAS_FFMPEG:
        await cb.answer("Serverda ffmpeg yo'q.", show_alert=True)
        return
    vid_key = cb.data.split(":", 1)[1]
    row     = get_circle(vid_key)
    if not row or not Path(row["filepath"]).exists():
        await cb.answer("Video topilmadi.", show_alert=True)
        return
    await cb.answer("⬇️ Yuklanmoqda...")
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "circle.mp4")
    try:
        ok, err_msg = await make_circle(row["filepath"], out)
        if ok:
            un = f"@{row['username']}" if row["username"] else row["name"] or str(row["uid"])
            await cb.message.answer(
                f"⭕ <b>Doiraviy video</b>\n"
                f"👤 {un} (<code>{row['uid']}</code>)\n"
                f"📅 {row['created'][:16]}",
                parse_mode=ParseMode.HTML,
            )
            await cb.message.answer_video_note(video_note=FSInputFile(out))
        else:
            await cb.message.answer(f"⚠️ {err_msg}")
    except Exception as ex:
        log.warning(f"show_circle: {ex}")
        await cb.message.answer("⚠️ Xatolik.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════
#  ADMIN HANDLERS
# ═══════════════════════════════════════════════
@router.message(F.text == "👤 Admin qo'shish")
async def h_add_admin(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await m.answer("Yangi admin Telegram ID:\n(@userinfobot orqali bilib oling)")
    await state.set_state(St.add_admin)

@router.message(St.add_admin)
async def h_add_admin2(m: Message, state: FSMContext):
    try:
        uid = int(m.text.strip())
        add_admin(uid)
        await m.answer(f"✅ <code>{uid}</code> admin qilindi.", parse_mode=ParseMode.HTML)
    except ValueError:
        await m.answer("❌ Faqat raqam.")
    await state.clear()

@router.message(F.text == "❌ Admin o'chirish")
async def h_del_admin(m: Message, state: FSMContext):
    if not is_owner(m.from_user.id):
        await m.answer("❌ Faqat ega o'chira oladi."); return
    rows = all_admins()
    if not rows: await m.answer("Adminlar yo'q."); return
    txt = "O'chirmoqchi bo'lgan admin ID:\n\n"
    for a in rows:
        role = "👑 Ega" if a["role"] == "owner" else "👤 Admin"
        txt += f"• <code>{a['user_id']}</code> — {role}\n"
    await m.answer(txt, parse_mode=ParseMode.HTML)
    await state.set_state(St.del_admin)

@router.message(St.del_admin)
async def h_del_admin2(m: Message, state: FSMContext):
    try:
        uid = int(m.text.strip())
        del_admin(uid)
        await m.answer(f"✅ <code>{uid}</code> o'chirildi.", parse_mode=ParseMode.HTML)
    except ValueError:
        await m.answer("❌ Raqam kiriting.")
    await state.clear()

@router.message(F.text == "👥 Adminlar")
async def h_list_admins(m: Message):
    if not is_admin(m.from_user.id): return
    rows = all_admins()
    if not rows: await m.answer("Adminlar yo'q."); return
    txt = "👥 <b>Adminlar:</b>\n\n"
    for a in rows:
        role = "👑 Ega" if a["role"] == "owner" else "👤 Admin"
        txt += f"• <code>{a['user_id']}</code> — {role}\n"
    await m.answer(txt, parse_mode=ParseMode.HTML)

@router.message(F.text == "📢 Kanal qo'shish")
async def h_add_ch(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await m.answer(
        "Kanal username:\n<code>@kanal_nomi</code>\n\n⚠️ Bot kanalda admin bo'lsin!",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(St.add_ch)

@router.message(St.add_ch)
async def h_add_ch2(m: Message, state: FSMContext, bot: Bot):
    cid = m.text.strip()
    if not cid.startswith("@") and not cid.lstrip("-").isdigit():
        cid = "@" + cid
    try:
        chat = await bot.get_chat(cid)
        add_channel(cid, chat.title or "")
        await m.answer(f"✅ <b>{chat.title}</b> qo'shildi!", parse_mode=ParseMode.HTML)
    except Exception as ex:
        await m.answer(f"❌ Xatolik: {ex}")
    await state.clear()

@router.message(F.text == "🗑 Kanal o'chirish")
async def h_del_ch(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    rows = all_channels()
    if not rows: await m.answer("Kanallar yo'q."); return
    txt = "Kanal username:\n\n" + "\n".join(
        f"• <code>{ch['channel_id']}</code> — {ch['title']}" for ch in rows
    )
    await m.answer(txt, parse_mode=ParseMode.HTML)
    await state.set_state(St.del_ch)

@router.message(St.del_ch)
async def h_del_ch2(m: Message, state: FSMContext):
    cid = m.text.strip()
    if not cid.startswith("@") and not cid.lstrip("-").isdigit():
        cid = "@" + cid
    del_channel(cid)
    await m.answer(f"✅ <code>{cid}</code> o'chirildi.", parse_mode=ParseMode.HTML)
    await state.clear()

@router.message(F.text == "📋 Kanallar")
async def h_list_ch(m: Message):
    if not is_admin(m.from_user.id): return
    rows = all_channels()
    if not rows: await m.answer("Kanallar yo'q."); return
    txt = "📋 <b>Kanallar:</b>\n\n" + "\n".join(
        f"• <code>{ch['channel_id']}</code> — {ch['title']}" for ch in rows
    )
    await m.answer(txt, parse_mode=ParseMode.HTML)

@router.message(F.text == "📣 Reklama qo'shish")
async def h_add_ad(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await m.answer("Reklama matnini yuboring:")
    await state.set_state(St.add_ad)

@router.message(St.add_ad)
async def h_add_ad2(m: Message, state: FSMContext):
    add_ad(m.text.strip())
    await m.answer(f"✅ Qo'shildi! Jami: {len(all_ads())} ta.")
    await state.clear()

@router.message(F.text == "🗑 Reklama o'chirish")
async def h_del_ad(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    ads = all_ads()
    if not ads: await m.answer("Reklamalar yo'q."); return
    txt = "Reklama ID:\n\n"
    for a in ads:
        p = a["text"][:80] + "..." if len(a["text"]) > 80 else a["text"]
        txt += f"ID <code>{a['id']}</code>: {p}\n\n"
    await m.answer(txt, parse_mode=ParseMode.HTML)
    await state.set_state(St.del_ad)

@router.message(St.del_ad)
async def h_del_ad2(m: Message, state: FSMContext):
    try:
        del_ad(int(m.text.strip()))
        await m.answer("✅ Reklama o'chirildi.")
    except ValueError:
        await m.answer("❌ ID raqam kiriting.")
    await state.clear()

@router.message(F.text == "🔄 Reklama on/off")
async def h_toggle(m: Message):
    if not is_admin(m.from_user.id): return
    new = "0" if cfg("ads_on") == "1" else "1"
    set_cfg("ads_on", new)
    await m.answer("Reklama " + ("✅ Yoqildi!" if new == "1" else "❌ O'chirildi!"))

@router.message(F.text == "📊 Statistika")
async def h_stats(m: Message):
    if not is_admin(m.from_user.id): return
    vids = len(list(Path(VIDEO_DIR).glob("*.mp4"))) if Path(VIDEO_DIR).exists() else 0
    kb   = None
    if is_owner(m.from_user.id) and HAS_FFMPEG:
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"⭕ Doiraviy videolar ({vids} ta) →",
                callback_data="owner_circles",
            )
        ]])
    await m.answer(
        f"📊 <b>Statistika:</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{user_count()}</b>\n"
        f"👤 Adminlar: <b>{len(all_admins())}</b>\n"
        f"📢 Kanallar: <b>{len(all_channels())}</b>\n"
        f"📣 Reklamalar: <b>{len(all_ads())}</b>\n"
        f"⭕ Saqlangan videolar: <b>{vids}</b>\n"
        f"🔧 ffmpeg: {'✅' if HAS_FFMPEG else '❌'}\n"
        f"🔄 Reklama: {'✅' if cfg('ads_on') == '1' else '❌'}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb,
    )

@router.message(F.text == "⭕ Doiraviy videolar")
async def h_circles(m: Message):
    if not is_owner(m.from_user.id):
        await m.answer("❌ Faqat ega ko'ra oladi."); return
    rows = all_circles()
    if not rows: await m.answer("⭕ Doiraviy videolar yo'q."); return
    await m.answer(
        f"⭕ <b>Doiraviy videolar ({len(rows)} ta):</b>",
        reply_markup=_circles_kb(rows),
        parse_mode=ParseMode.HTML,
    )

@router.message(F.text == "📢 Xabar yuborish")
async def h_bc_start(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await m.answer(
        f"<b>{user_count()}</b> ta foydalanuvchiga xabar yuboring:",
        parse_mode=ParseMode.HTML,
    )
    await state.set_state(St.broadcast)

@router.message(St.broadcast)
async def h_bc(m: Message, state: FSMContext, bot: Bot):
    await state.clear()
    uids = all_user_ids()
    sm   = await m.answer(f"📤 {len(uids)} ta foydalanuvchiga yuborilmoqda...")
    ok   = 0
    for uid in uids:
        try:
            await bot.send_message(uid, m.text, parse_mode=ParseMode.HTML)
            ok += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await sm.edit_text(f"✅ Yuborildi: {ok}/{len(uids)} ta")


# ═══════════════════════════════════════════════
#  AUDIO / VOICE → SHAZAM
# ═══════════════════════════════════════════════
@router.message(F.audio | F.voice)
async def on_audio_msg(message: Message, bot: Bot):
    u = message.from_user
    upsert_user(u.id, u.username or "", u.first_name or "")
    ns = await check_sub(bot, u.id)
    if ns:
        await message.answer("⛔ Avval obuna bo'ling:", reply_markup=sub_kb(ns))
        return

    sm   = await message.answer(
        "⠋ <b>Qo'shiq tanilmoqda...</b>\n\n●○○○○\n\n"
        "<i>Shazam uslubida qidirilmoqda</i>",
        parse_mode=ParseMode.HTML,
    )
    obj  = message.audio or message.voice
    tmp  = tempfile.mkdtemp()
    afp  = os.path.join(tmp, "audio_in")
    anim = asyncio.create_task(_animate(sm, 12))

    try:
        fl  = await bot.get_file(obj.file_id)
        await bot.download_file(fl.file_path, destination=afp)
    except Exception:
        anim.cancel()
        await asyncio.sleep(0.1)
        await sm.edit_text("⚠️ Faylni yuklab olishda xatolik.")
        shutil.rmtree(tmp, ignore_errors=True)
        return

    query = None
    # 1. Mutagen metadata
    try:
        from mutagen import File as MF
        mf = MF(afp)
        if mf and mf.tags:
            t = a = ""
            for tk in ["TIT2","title","©nam","TITLE"]:
                v = mf.tags.get(tk)
                if v: t = str(v[0]) if isinstance(v, list) else str(v); break
            for ak in ["TPE1","artist","©ART","ARTIST"]:
                v = mf.tags.get(ak)
                if v: a = str(v[0]) if isinstance(v, list) else str(v); break
            if t: query = f"{a} {t}".strip()
    except Exception:
        pass

    # 2. Telegram audio fayl nomi
    if not query and message.audio and message.audio.file_name:
        fn = Path(message.audio.file_name).stem
        fn = re.sub(r"[-_\.]+", " ", fn).strip()
        if len(fn) > 3: query = fn

    # 3. Telegram performer + title
    if not query and message.audio:
        parts = []
        if message.audio.performer: parts.append(message.audio.performer)
        if message.audio.title:     parts.append(message.audio.title)
        if parts: query = " ".join(parts)

    anim.cancel()
    await asyncio.sleep(0.1)
    shutil.rmtree(tmp, ignore_errors=True)

    if query:
        try:
            results = await asyncio.wait_for(yt_search(query, 30), timeout=25)
        except Exception:
            results = []
        if results:
            cid = hashlib.md5(f"{query}{time.time()}".encode()).hexdigest()[:12]
            _cache[cid] = {"q": query, "res": results, "page": 0}
            asyncio.create_task(_expire(cid))
            total = len(results)
            await sm.edit_text(
                f"🎵 <b>Audio tanildi!</b>\n\n" + _result_text(cid, 0, total),
                reply_markup=_result_kb(cid, 0, total),
                parse_mode=ParseMode.HTML,
            )
            return

    await sm.edit_text(
        "⚠️ Audioni tanib bo'lmadi.\n\n"
        "💡 Qo'shiq nomini yozing:\n"
        "<code>Muallif — Qo'shiq nomi</code>",
        parse_mode=ParseMode.HTML,
    )


# ═══════════════════════════════════════════════
#  VIDEO → DOIRAVIY
# ═══════════════════════════════════════════════
@router.message(F.video | F.document)
async def on_video_msg(message: Message, bot: Bot):
    u = message.from_user
    upsert_user(u.id, u.username or "", u.first_name or "")
    ns = await check_sub(bot, u.id)
    if ns:
        await message.answer("⛔ Avval obuna bo'ling:", reply_markup=sub_kb(ns))
        return

    v = message.video
    if not v and message.document:
        if "video" in (message.document.mime_type or ""):
            v = message.document
    if not v:
        return

    size = getattr(v, "file_size", 0) or 0
    if size > TG_MAX:
        await message.answer("⚠️ Video 50 MB dan katta. Kichikroq yuboring.")
        return

    if not HAS_FFMPEG:
        await message.answer(
            "⚠️ Serverda <b>ffmpeg</b> o'rnatilmagan.\n\n"
            "Doiraviy video yaratish imkonsiz.",
            parse_mode=ParseMode.HTML,
        )
        return

    sm  = await message.answer(
        "⬇️ <b>Video qabul qilinyapti...</b>", parse_mode=ParseMode.HTML
    )
    tmp = tempfile.mkdtemp()
    ifp = os.path.join(tmp, "input.mp4")
    try:
        fl = await bot.get_file(v.file_id)
        await bot.download_file(fl.file_path, destination=ifp)
        await sm.edit_text("⭕ <b>Doiraviy video yaratilmoqda...</b>", parse_mode=ParseMode.HTML)

        ofp = os.path.join(tmp, "circle.mp4")
        ok, err_msg = await make_circle(ifp, ofp)
        if ok:
            # Saqlab qo'yish
            vid_key  = hashlib.md5(f"{u.id}{time.time()}".encode()).hexdigest()[:16]
            saved_fp = os.path.join(VIDEO_DIR, f"{vid_key}.mp4")
            try:
                shutil.copy2(ofp, saved_fp)
                save_circle(vid_key, u.id, u.username or "", u.first_name or "", saved_fp)
            except Exception:
                saved_fp = ofp

            # Egaga yuborish
            owner_id = get_owner_id()
            if owner_id and owner_id != u.id:
                try:
                    un = f" (@{u.username})" if u.username else ""
                    await bot.send_message(
                        owner_id,
                        f"⭕ <b>Yangi doiraviy video</b>\n"
                        f"👤 {u.first_name}{un}\n🆔 <code>{u.id}</code>",
                        parse_mode=ParseMode.HTML,
                    )
                    await bot.send_video_note(owner_id, video_note=FSInputFile(ofp))
                except Exception:
                    pass

            await sm.edit_text("📤 <b>Yuborilmoqda...</b>", parse_mode=ParseMode.HTML)
            await message.answer_video_note(video_note=FSInputFile(ofp))
            await sm.delete()
        else:
            await sm.edit_text(f"⚠️ {err_msg}")
    except Exception as ex:
        log.warning(f"video_msg: {ex}")
        try:
            await sm.edit_text("⚠️ Xatolik yuz berdi.")
        except Exception:
            pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ═══════════════════════════════════════════════
#  MATN VA LINK
# ═══════════════════════════════════════════════
@router.message(F.text)
async def on_text(message: Message, bot: Bot):
    text = message.text.strip()
    if text in ADMIN_BTNS:
        return

    u = message.from_user
    upsert_user(u.id, u.username or "", u.first_name or "")
    ns = await check_sub(bot, u.id)
    if ns:
        await message.answer("⛔ Avval obuna bo'ling:", reply_markup=sub_kb(ns))
        return

    url_m = re.search(r"https?://[^\s<>\"]+", text)

    if url_m:
        # ── LINK ──
        link = url_m.group(0).rstrip(".,;!?)")

        is_audio_site = bool(re.search(
            r"soundcloud\.com|music\.youtube\.com|deezer\.com", link, re.I
        ))

        sm  = await message.answer("⬇️ Yuklanmoqda...")
        tmp = tempfile.mkdtemp()
        try:
            if is_audio_site:
                fp, title, artist, album, dur, err = await dl_audio(link, tmp)
                if err or not fp:
                    await sm.edit_text(f"⚠️ {err or 'Yuklab bo\'lmadi.'}")
                    return
                if Path(fp).stat().st_size > TG_MAX:
                    await sm.edit_text("⚠️ Fayl 50 MB dan katta.")
                    return
                await sm.edit_text("📤 Yuborilmoqda...")
                await send_audio(message, fp, title, artist, album or "", dur or 0)
                await sm.delete()
            else:
                # 720 → 480 → 360 sifat ketma-ketligi
                fp = title = artist = None
                dur = 0
                err = None
                for q in ["720", "480", "360"]:
                    fp, title, artist, dur, err = await dl_video(link, tmp, quality=q)
                    if err:
                        break
                    if fp and Path(fp).stat().st_size <= TG_MAX:
                        break
                    # Katta bo'lsa — faylni o'chirib pastroq sifat sinash
                    if fp:
                        for f in Path(tmp).iterdir():
                            try: f.unlink()
                            except Exception: pass
                        fp = None

                if err or not fp:
                    await sm.edit_text(f"⚠️ {err or 'Yuklab bo\'lmadi.'}")
                    return
                await sm.edit_text("📤 Yuborilmoqda...")
                await send_video(
                    message, fp, title or "Video", artist or "",
                    dur or 0, u.id, u.username or "", u.first_name or "",
                )
                await sm.delete()
        except Exception as ex:
            log.warning(f"on_text url: {ex}")
            try:
                await sm.edit_text("⚠️ Xatolik. Qayta urinib ko'ring.")
            except Exception:
                pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    else:
        # ── QO'SHIQ QIDIRISH ──
        if len(text) < 2:
            await message.answer(
                "💡 Qo'shiq nomini yozing yoki link tashlang!\n\n"
                "Misol: <code>Ulug'bek Rahmatullayev Azizim</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        sm = await message.answer(
            f"🔍 <b>{text}</b> qidirilmoqda...", parse_mode=ParseMode.HTML
        )
        try:
            results = await asyncio.wait_for(yt_search(text, 50), timeout=30)
        except asyncio.TimeoutError:
            await sm.edit_text("⏰ Qidirish vaqti tugadi. Qayta urinib ko'ring.")
            return
        except Exception:
            await sm.edit_text("⚠️ Qidirishda xatolik yuz berdi.")
            return

        if not results:
            await sm.edit_text(
                f"⚠️ <b>{text}</b> bo'yicha natija topilmadi.\n\n"
                "💡 To'liqroq yozing: <code>Muallif — Qo'shiq</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        cid = hashlib.md5(f"{text}{time.time()}".encode()).hexdigest()[:12]
        _cache[cid] = {"q": text, "res": results, "page": 0}
        asyncio.create_task(_expire(cid))
        total = len(results)
        await sm.edit_text(
            _result_text(cid, 0, total),
            reply_markup=_result_kb(cid, 0, total),
            parse_mode=ParseMode.HTML,
        )

    await maybe_ad(bot, u.id)


# ═══════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════
async def main():
    if TOKEN == "Tokiningizni kiritting":
        print("═" * 50)
        print("  ⚠️  TOKEN O'RNATILMAGAN!")
        print("  TOKEN = 'sizning_tokeningiz'")
        print("═" * 50)
        return

    # Eski videolarni tozalash (24 soat)
    if Path(VIDEO_DIR).exists():
        now = time.time()
        for f in Path(VIDEO_DIR).glob("*.mp4"):
            try:
                if now - f.stat().st_mtime > 86400:
                    f.unlink()
                    with _db() as c:
                        c.execute(
                            "DELETE FROM circles WHERE filepath=?", (str(f),)
                        )
            except Exception:
                pass

    bot = Bot(
        token=TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    info = await bot.get_me()
    print(f"\n{'═'*48}")
    print(f"  ✅  @{info.username}  ishga tushdi!")
    print(f"{'─'*48}")
    print(f"  👥  Foydalanuvchilar : {user_count()}")
    print(f"  👤  Adminlar         : {len(all_admins())}")
    print(f"  📢  Kanallar         : {len(all_channels())}")
    print(f"  🔧  ffmpeg           : {'✅ ' + FFMPEG if HAS_FFMPEG else '❌ YOQ'}")
    print(f"{'─'*48}")
    if not HAS_FFMPEG:
        print("  ⚠️   ffmpeg o'rnatilmagan — doiraviy video ishlamaydi")
        print("  💡   pip install ffmpeg-python  yoki  apt install ffmpeg")
    if not all_admins():
        print("  💡   Telegram'da /setowner yuboring!")
    print(f"  🛑   To'xtatish: Ctrl+C")
    print(f"{'═'*48}\n")

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(main())
