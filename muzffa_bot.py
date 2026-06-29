"""
muzffabot.py — v8 FINAL
- Qidiruv: 1-10 raqamli inline tugmalar + Keyingi/Oldingi
- Doiraviy video: Windows+Linux mos subprocess.run via executor
- Ega paneli: Statistika + ⭕ Doiraviy videolar inline tugma
- SQLite3 DB: channels ustuni = channel_id (eski DB bilan mos)
- Barcha xatolar handle qilingan
"""
import asyncio, logging, os, re, tempfile, shutil
import sqlite3, hashlib, time, sys, subprocess
from pathlib import Path
from typing import Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ReplyKeyboardRemove, FSInputFile
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import yt_dlp
# ══════════════════════════════════════
TOKEN     = "8993206431:AAEFdUQqHpbVb4h0WHXrfMlYqhb9Y8Zypbk"
DB_FILE   = "muzffa_bot.db"
VIDEO_DIR = "saved_videos"
PER_PAGE  = 10
TG_MAX    = 49 * 1024 * 1024
# ══════════════════════════════════════

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger(__name__)
os.makedirs(VIDEO_DIR, exist_ok=True)

YDL_BASE = {
    "nocheckcertificate": True,
    "socket_timeout": 60,
    "retries": 5,
    "fragment_retries": 5,
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    "http_headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "*/*",
    },
}

# ════════════════ DB ════════════════
def _db() -> sqlite3.Connection:
    c = sqlite3.connect(DB_FILE, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def db_init():
    with _db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS admins(
            user_id INTEGER PRIMARY KEY,
            role    TEXT DEFAULT 'admin',
            added   TEXT DEFAULT(datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS channels(
            channel_id TEXT PRIMARY KEY,
            title      TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS ads(
            id     INTEGER PRIMARY KEY AUTOINCREMENT,
            text   TEXT NOT NULL,
            active INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS settings(
            k TEXT PRIMARY KEY,
            v TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS users(
            uid       INTEGER PRIMARY KEY,
            username  TEXT DEFAULT '',
            name      TEXT DEFAULT '',
            msg_count INTEGER DEFAULT 0,
            joined    TEXT DEFAULT(datetime('now','localtime'))
        );
        CREATE TABLE IF NOT EXISTS circle_videos(
            vid_key  TEXT PRIMARY KEY,
            uid      INTEGER,
            username TEXT DEFAULT '',
            name     TEXT DEFAULT '',
            filepath TEXT,
            created  TEXT DEFAULT(datetime('now','localtime'))
        );
        INSERT OR IGNORE INTO settings VALUES('ads_on','0');
        INSERT OR IGNORE INTO settings VALUES('ads_interval','5');
        INSERT OR IGNORE INTO settings VALUES('owner_id','');
        """)

db_init()

def cfg(k: str, d: str = "") -> str:
    with _db() as c:
        r = c.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
    return r["v"] if r else d

def set_cfg(k: str, v: str):
    with _db() as c:
        c.execute("INSERT OR REPLACE INTO settings(k,v) VALUES(?,?)", (k, v))

def get_owner_id() -> Optional[int]:
    v = cfg("owner_id", "").strip()
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
        c.execute("INSERT OR REPLACE INTO admins(user_id,role) VALUES(?,?)", (uid, role))

def del_admin(uid: int):
    with _db() as c:
        c.execute("DELETE FROM admins WHERE user_id=? AND role!='owner'", (uid,))

def all_admins() -> list:
    with _db() as c:
        return c.execute("SELECT user_id,role FROM admins").fetchall()

def all_channels() -> list:
    with _db() as c:
        return c.execute("SELECT channel_id,title FROM channels").fetchall()

def add_channel(cid: str, title: str = ""):
    with _db() as c:
        c.execute("INSERT OR IGNORE INTO channels(channel_id,title) VALUES(?,?)", (cid, title))

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
            (uid, username or "", name or "")
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
            """INSERT OR REPLACE INTO circle_videos(vid_key,uid,username,name,filepath)
               VALUES(?,?,?,?,?)""",
            (vid_key, uid, username or "", name or "", filepath)
        )

def all_circles() -> list:
    with _db() as c:
        return c.execute(
            "SELECT * FROM circle_videos ORDER BY created DESC LIMIT 50"
        ).fetchall()

def get_circle(vid_key: str) -> Optional[sqlite3.Row]:
    with _db() as c:
        return c.execute(
            "SELECT * FROM circle_videos WHERE vid_key=?", (vid_key,)
        ).fetchone()

# ════════════════ FSM ════════════════
class St(StatesGroup):
    add_admin = State(); del_admin = State()
    add_ch    = State(); del_ch    = State()
    add_ad    = State(); del_ad    = State()
    broadcast = State()

# ════════════════ SUBSCRIPTION ════════════════
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
    btns = [[InlineKeyboardButton(
        text=f"📢 {ch['title'] or ch['channel_id']}",
        url="https://t.me/" + ch["channel_id"].lstrip("@")
    )] for ch in chs]
    btns.append([InlineKeyboardButton(text="✅ Tekshirish", callback_data="check_sub")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

# ════════════════ ADMIN KEYBOARD ════════════════
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
    "👤 Admin qo'shish","❌ Admin o'chirish",
    "📢 Kanal qo'shish","🗑 Kanal o'chirish",
    "📋 Kanallar","👥 Adminlar",
    "📣 Reklama qo'shish","🗑 Reklama o'chirish",
    "📊 Statistika","🔄 Reklama on/off",
    "📢 Xabar yuborish","⭕ Doiraviy videolar",
    "❌ Yopish",
}

# ════════════════ SEARCH + RAQAMLI TUGMALAR ════════════════
_cache: dict = {}

def fmt_dur(s) -> str:
    if not s: return ""
    s = int(s); m, s2 = divmod(s, 60); h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s2:02d}" if h else f"{m}:{s2:02d}"

def clean_artist(a: str) -> str:
    for sfx in [" - Topic", " - topic", "VEVO", " Official", " official"]:
        a = a.replace(sfx, "")
    return a.strip()

async def yt_search(query: str, limit: int = 50) -> list:
    loop = asyncio.get_event_loop()
    hold: dict = {}

    def _do():
        opts = {
            **YDL_BASE,
            "extract_flat": "in_playlist",
            "default_search": f"ytsearch{limit}",
            "skip_download": True,
            "ignoreerrors": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                hold["i"] = ydl.extract_info(query, download=False)
            except Exception as e:
                hold["err"] = str(e)

    try:
        await asyncio.wait_for(loop.run_in_executor(None, _do), timeout=30)
    except Exception:
        return []

    info    = hold.get("i") or {}
    entries = info.get("entries") or []
    res = []
    for e in entries:
        if not e or not e.get("id"): continue
        artist = clean_artist(e.get("uploader") or e.get("channel") or "")
        res.append({
            "title":  e.get("title", "?"),
            "artist": artist,
            "dur":    e.get("duration", 0),
            "url":    f"https://www.youtube.com/watch?v={e['id']}",
        })
    return res

def build_result_text(cid: str, page: int, total: int) -> str:
    """Qidiruv natijalari matni — rasmda ko'ringan ko'rinish"""
    data  = _cache.get(cid, {})
    res   = data.get("res", [])
    query = data.get("q", "")
    s     = page * PER_PAGE
    e     = min(s + PER_PAGE, total)
    pages = (total + PER_PAGE - 1) // PER_PAGE

    lines = [f"<b>{query}</b> bo'yicha natijalar:\n"]
    for i, r in enumerate(res[s:e]):
        idx   = s + i
        num   = idx + 1
        art   = r["artist"]
        ttl   = r["title"]
        dur   = fmt_dur(r["dur"])
        line  = f"{num}. {art} - {ttl}" if art else f"{num}. {ttl}"
        if dur: line += f"  {dur}"
        lines.append(line)

    lines.append(f"\n📄 Sahifa {page+1}/{pages}")
    return "\n".join(lines)

def build_result_kb(cid: str, page: int, total: int) -> InlineKeyboardMarkup:
    """
    Raqamli inline tugmalar:
    [ 1 ][ 2 ][ 3 ][ 4 ][ 5 ]
    [ 6 ][ 7 ][ 8 ][ 9 ][ 10]
    [⬅️ Oldingi]        [Keyingi ➡️]
    """
    data = _cache.get(cid, {})
    res  = data.get("res", [])
    s    = page * PER_PAGE
    e    = min(s + PER_PAGE, total)
    count = e - s   # joriy sahifadagi natijalar soni

    # Raqamli tugmalar — 5 tadan qator
    num_btns = []
    row1 = []
    row2 = []
    for i in range(count):
        idx    = s + i
        num    = idx + 1
        btn    = InlineKeyboardButton(text=str(num), callback_data=f"dl:{cid}:{idx}")
        if i < 5:
            row1.append(btn)
        else:
            row2.append(btn)
    if row1: num_btns.append(row1)
    if row2: num_btns.append(row2)

    # Navigatsiya
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️", callback_data=f"pg:{cid}:{page-1}"))
    nav.append(InlineKeyboardButton(text="✖️", callback_data="close"))
    if e < total:
        nav.append(InlineKeyboardButton(text="➡️", callback_data=f"pg:{cid}:{page+1}"))
    num_btns.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=num_btns)

async def _expire(cid: str, delay: int = 600):
    await asyncio.sleep(delay)
    _cache.pop(cid, None)

# ════════════════ FFMPEG — WINDOWS+LINUX ════════════════
def _ffmpeg_sync(cmd: list, timeout: int = 180) -> tuple:
    """subprocess.run orqali ffmpeg — Windows va Linux ikkalasida ishlaydi"""
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout
        )
        return r.returncode, r.stderr.decode(errors="ignore")
    except subprocess.TimeoutExpired:
        return -1, "timeout"
    except FileNotFoundError:
        return -2, "ffmpeg topilmadi"
    except Exception as ex:
        return -3, str(ex)

async def _ffprobe_val(path: str, select: str, entries: str, fmt: str) -> str:
    loop = asyncio.get_event_loop()
    def _do():
        r = subprocess.run(
            ["ffprobe", "-v", "error",
             "-select_streams", select,
             "-show_entries", entries,
             "-of", fmt, path],
            capture_output=True, timeout=15
        )
        return r.stdout.decode(errors="ignore").strip()
    try:
        return await asyncio.wait_for(loop.run_in_executor(None, _do), timeout=20)
    except Exception:
        return ""

async def get_duration(path: str) -> float:
    out = await _ffprobe_val(
        path, "", "format=duration",
        "default=noprint_wrappers=1:nokey=1"
    )
    try: return float(out)
    except Exception: return 0.0

async def has_audio(path: str) -> bool:
    out = await _ffprobe_val(
        path, "a:0", "stream=codec_name",
        "default=noprint_wrappers=1:nokey=1"
    )
    return bool(out)

async def make_circle(input_path: str, out_path: str) -> tuple:
    """
    Video → doiraviy video note (512x512, max 59s)
    subprocess.run — Windows va Linux uchun
    Returns: (ok: bool, error: str)
    """
    p = Path(input_path)
    if not p.exists():
        return False, "Fayl topilmadi"
    if p.stat().st_size < 100:
        return False, "Fayl bo'sh"

    dur  = await get_duration(str(p))
    haudio = await has_audio(str(p))
    t    = min(dur if dur > 0 else 30.0, 59.0)

    # r-string: ffmpeg crop filter sintaksisi
    vf = r"crop=min(iw\,ih):min(iw\,ih),scale=512:512,format=yuv420p"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
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
    cmd += ["-movflags", "+faststart", str(out_path)]

    loop = asyncio.get_event_loop()
    rc, err = await asyncio.wait_for(
        loop.run_in_executor(None, _ffmpeg_sync, cmd, 180),
        timeout=200
    )

    if rc == -2:
        return False, "Serverda ffmpeg o'rnatilmagan"
    if rc == -1:
        return False, "Doiraviy video yaratish vaqti tugadi"
    if rc != 0:
        log.warning(f"circle rc={rc}: {err[-200:]}")
        if "Invalid data" in err or "moov atom" in err:
            return False, "Video fayli o'qib bo'lmadi"
        if "codec not currently supported" in err:
            return False, "Bu video formati qo'llab-quvvatlanmaydi"
        return False, "Doiraviy video yaratishda xatolik"

    op = Path(out_path)
    if not op.exists() or op.stat().st_size < 500:
        return False, "Natija fayli bo'sh"
    return True, ""

# ════════════════ DOWNLOAD ════════════════
def _ydl_err(e: str) -> str:
    el = e.lower()
    if "private"     in el: return "Yopiq (private) kontent"
    if "unavailable" in el: return "Media mavjud emas"
    if "copyright"   in el: return "Mualliflik cheklovi"
    if "login"       in el: return "Login talab qilinadi"
    if "geo"         in el: return "Sizning mamlakatda mavjud emas"
    if "age"         in el: return "Yosh cheklovi bor"
    if "403"         in e  : return "Ruxsat yo'q. Qayta urinib ko'ring"
    if "404"         in e  : return "Sahifa topilmadi"
    return "Yuklab bo'lmadi"

async def dl_audio(url: str, tmp: str) -> tuple:
    tpl  = os.path.join(tmp, "%(title).80s.%(ext)s")
    opts = {
        **YDL_BASE,
        "format": "bestaudio[ext=mp3]/bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": tpl,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
    }
    loop = asyncio.get_event_loop()
    hold: dict = {}

    def _do():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            hold["i"] = info["entries"][0] if (info and "entries" in info) else info

    try:
        await asyncio.wait_for(loop.run_in_executor(None, _do), timeout=180)
    except asyncio.TimeoutError:
        return None, None, None, None, None, "Yuklab olish vaqti tugadi"
    except yt_dlp.utils.DownloadError as ex:
        return None, None, None, None, None, _ydl_err(str(ex))
    except Exception as ex:
        log.warning(f"dl_audio: {ex}")
        return None, None, None, None, None, "Xatolik yuz berdi"

    i     = hold.get("i") or {}
    files = [f for f in Path(tmp).iterdir() if f.is_file()]
    if not files:
        return None, None, None, None, None, "Fayl yuklanmadi"
    fp     = str(max(files, key=lambda f: f.stat().st_size))
    artist = clean_artist(i.get("artist") or i.get("uploader") or i.get("channel") or "")
    return fp, i.get("title","?"), artist, i.get("album",""), i.get("duration",0), None

async def dl_video(url: str, tmp: str, quality: str = "720") -> tuple:
    tpl = os.path.join(tmp, "%(title).80s.%(ext)s")
    if quality == "best":
        fmt = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    else:
        h   = quality
        fmt = (
            f"bestvideo[height<={h}][ext=mp4]+bestaudio[ext=m4a]/"
            f"best[height<={h}][ext=mp4]/best[height<={h}]/best[ext=mp4]/best"
        )
    opts = {
        **YDL_BASE,
        "format": fmt,
        "merge_output_format": "mp4",
        "outtmpl": tpl,
        "concurrent_fragment_downloads": 4,
    }
    loop = asyncio.get_event_loop()
    hold: dict = {}

    def _do():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            hold["i"] = info["entries"][0] if (info and "entries" in info) else info

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
        return None, None, None, None, "Xatolik"

    i     = hold.get("i") or {}
    files = [f for f in Path(tmp).iterdir() if f.is_file()]
    if not files:
        return None, None, None, None, "Fayl yuklanmadi"
    fp     = str(max(files, key=lambda f: f.stat().st_size))
    artist = clean_artist(i.get("uploader") or i.get("channel") or "")
    return fp, i.get("title","Video"), artist, i.get("duration",0), None

# ════════════════ SEND HELPERS ════════════════
def _acap(title, artist, album, dur):
    lines = [f"<b>{title}</b>"]
    if artist: lines.append(f"<i>{artist}</i>")
    if album:  lines.append(f"💿 {album}")
    d = fmt_dur(dur)
    if d: lines.append(f"⏱ {d}")
    lines.append("\n🎵 @muzffabot")
    return "\n".join(lines)

def _vcap(title, artist, dur):
    lines = [f"<b>{title}</b>"]
    if artist: lines.append(f"<i>{artist}</i>")
    d = fmt_dur(dur)
    if d: lines.append(f"⏱ {d}")
    lines.append("\n🎬 @muzffabot")
    return "\n".join(lines)

def _circle_kb(vid_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="⭕ Doiraviy videoga aylantirish",
            callback_data=f"circle:{vid_key}"
        )
    ]])

async def send_audio(msg: Message, fp: str, title: str, artist: str, album: str, dur: int):
    cap = _acap(title, artist, album, dur)
    ext = Path(fp).suffix.lower()
    inp = FSInputFile(fp)
    if ext in (".mp3",".m4a",".ogg",".wav",".flac",".aac",".opus"):
        await msg.answer_audio(
            audio=inp, caption=cap, parse_mode=ParseMode.HTML,
            title=title, performer=artist or None,
            duration=int(dur) if dur else None
        )
    else:
        await msg.answer_document(document=inp, caption=cap, parse_mode=ParseMode.HTML)

async def send_video_with_kb(msg: Message, fp: str, title: str, artist: str,
                             dur: int, uid: int, username: str, name: str):
    vid_key  = hashlib.md5(f"{uid}{time.time()}".encode()).hexdigest()[:16]
    saved_fp = os.path.join(VIDEO_DIR, f"{vid_key}.mp4")
    try:
        shutil.copy2(fp, saved_fp)
        save_circle(vid_key, uid, username, name, saved_fp)
    except Exception:
        saved_fp = fp

    cap = _vcap(title, artist, dur)
    inp = FSInputFile(fp)
    kb  = _circle_kb(vid_key)
    ext = Path(fp).suffix.lower()
    if ext in (".mp4",".mov",".m4v"):
        await msg.answer_video(
            video=inp, caption=cap, parse_mode=ParseMode.HTML,
            duration=int(dur) if dur else None,
            reply_markup=kb
        )
    else:
        await msg.answer_document(
            document=inp, caption=cap, parse_mode=ParseMode.HTML, reply_markup=kb
        )

async def maybe_ad(bot: Bot, uid: int):
    if cfg("ads_on") != "1": return
    ads = all_ads()
    if not ads: return
    cnt      = bump_msg(uid)
    interval = int(cfg("ads_interval","5"))
    if cnt % interval == 0:
        import random
        ad = random.choice(ads)
        try:
            await bot.send_message(uid, f"📣 <b>Reklama:</b>\n\n{ad['text']}",
                                   parse_mode=ParseMode.HTML)
        except Exception: pass

# ════════════════ ANIMATSIYA ════════════════
_DOTS = ["⠋","⠙","⠹","⠸","⠼","⠴","⠦","⠧","⠇","⠏"]

async def _animate(msg: Message, frames: int = 14):
    for i in range(frames):
        f = "●" * (i % 5 + 1); e = "○" * (4 - i % 5)
        try:
            await msg.edit_text(
                f"{_DOTS[i%len(_DOTS)]} <b>Qo'shiq tanilmoqda...</b>\n\n"
                f"{f}{e}\n\n<i>Shazam uslubida qidirilmoqda</i>",
                parse_mode=ParseMode.HTML
            )
        except Exception: pass
        await asyncio.sleep(0.45)

# ════════════════ ROUTER ════════════════
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
            reply_markup=sub_kb(ns), parse_mode=ParseMode.HTML
        ); return
    name = u.first_name or "do'stim"
    await message.answer(
        f"👋 Salom, <b>{name}</b>!\n\n"
        "🎵 <b>Yuklaydi bot</b> — professional musiqa va video yuklovchi!\n\n"
        "<b>Imkoniyatlar:</b>\n"
        "🔍 Qo'shiq nomi yozing → 10 ta tanlov + sahifalash\n"
        "🎙 Audio jo'nating → Shazam uslubida taniydi\n"
        "🎬 Video jo'nating → Doiraviy videoga aylantiradi\n"
        "🔗 Link tashlang → yuklab beradi\n\n"
        "✅ YouTube · Instagram · TikTok · Facebook · Twitter\n\n"
        "🚀 Boshlang!",
        parse_mode=ParseMode.HTML
    )

# ── /setowner ──
@router.message(Command("setowner"))
async def on_setowner(message: Message):
    with _db() as c:
        existing = c.execute("SELECT 1 FROM admins WHERE role='owner'").fetchone()
    if existing and not is_owner(message.from_user.id):
        await message.answer("❌ Ega allaqachon belgilangan."); return
    uid = message.from_user.id
    add_admin(uid, "owner")
    set_cfg("owner_id", str(uid))
    await message.answer(
        f"✅ Siz botning <b>egasisiz</b>!\nID: <code>{uid}</code>\n\n/admin — panel",
        parse_mode=ParseMode.HTML
    )

# ── /admin ──
@router.message(Command("admin"))
async def on_admin(message: Message):
    if not is_admin(message.from_user.id):
        await message.answer("❌ Admin huquqingiz yo'q."); return
    await message.answer("🛠 <b>Admin paneli</b>",
                         reply_markup=admin_kb(), parse_mode=ParseMode.HTML)

@router.message(F.text == "❌ Yopish")
async def on_close(message: Message):
    if not is_admin(message.from_user.id): return
    await message.answer("Yopildi.", reply_markup=ReplyKeyboardRemove())

# ── Obuna ──
@router.callback_query(F.data == "check_sub")
async def on_check_sub(cb: CallbackQuery, bot: Bot):
    ns = await check_sub(bot, cb.from_user.id)
    if ns:
        await cb.answer("❌ Hali obuna bo'lmadingiz!", show_alert=True)
        try: await cb.message.edit_reply_markup(reply_markup=sub_kb(ns))
        except Exception: pass
    else:
        try: await cb.message.delete()
        except Exception: pass
        u = cb.from_user
        upsert_user(u.id, u.username or "", u.first_name or "")
        await cb.message.answer(
            "✅ <b>Obuna tasdiqlandi!</b>\n\nQo'shiq nomi yozing yoki link tashlang 🎵",
            parse_mode=ParseMode.HTML
        )

@router.callback_query(F.data == "close")
async def on_close_cb(cb: CallbackQuery):
    try: await cb.message.delete()
    except Exception: pass
    await cb.answer()

# ── Sahifalash ──
@router.callback_query(F.data.startswith("pg:"))
async def on_page(cb: CallbackQuery):
    _, cid, pg_s = cb.data.split(":")
    pg   = int(pg_s)
    data = _cache.get(cid)
    if not data:
        await cb.answer("Qidiruv eskirdi, qayta yozing.", show_alert=True); return
    data["page"] = pg
    total = len(data["res"])
    try:
        await cb.message.edit_text(
            build_result_text(cid, pg, total),
            reply_markup=build_result_kb(cid, pg, total),
            parse_mode=ParseMode.HTML
        )
    except Exception: pass
    await cb.answer()

# ── Raqam bosilganda yuklab olish ──
@router.callback_query(F.data.startswith("dl:"))
async def on_dl(cb: CallbackQuery, bot: Bot):
    _, cid, idx_s = cb.data.split(":")
    idx  = int(idx_s)
    data = _cache.get(cid)
    if not data:
        await cb.answer("Eskirgan, qayta qidiring.", show_alert=True); return
    res = data["res"]
    if idx >= len(res):
        await cb.answer("Topilmadi.", show_alert=True); return

    r = res[idx]
    await cb.answer(f"⬇️ {r['title'][:40]}...")
    tmp = tempfile.mkdtemp()
    sm  = await cb.message.answer(
        f"⬇️ <b>Yuklanmoqda...</b>\n"
        f"<i>{r['artist']} — {r['title']}</i>",
        parse_mode=ParseMode.HTML
    )
    try:
        fp, title, artist, album, dur, err = await dl_audio(r["url"], tmp)
        if err or not fp:
            await sm.edit_text(f"⚠️ {err or 'Yuklab bo\'lmadi.'}"); return
        if Path(fp).stat().st_size > TG_MAX:
            await sm.edit_text("⚠️ Fayl 50 MB dan katta."); return
        await sm.edit_text("📤 Yuborilmoqda...")
        await send_audio(cb.message, fp, title, artist, album or "", dur or 0)
        await sm.delete()
    except Exception as ex:
        log.warning(f"on_dl: {ex}")
        try: await sm.edit_text("⚠️ Xatolik. Qayta urinib ko'ring.")
        except Exception: pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    await maybe_ad(bot, cb.from_user.id)

# ════ DOIRAVIY VIDEO CALLBACK ════
@router.callback_query(F.data.startswith("circle:"))
async def on_circle_cb(cb: CallbackQuery, bot: Bot):
    vid_key = cb.data.split(":", 1)[1]
    row     = get_circle(vid_key)
    if not row or not Path(row["filepath"]).exists():
        await cb.answer("⚠️ Video topilmadi. Qayta yuboring.", show_alert=True); return

    await cb.answer("⭕ Tayorlanmoqda...")
    sm  = await cb.message.answer(
        "⭕ <b>Doiraviy video yaratilmoqda...</b>", parse_mode=ParseMode.HTML
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
                    u = cb.from_user
                    un = f" (@{u.username})" if u.username else ""
                    await bot.send_message(
                        owner_id,
                        f"⭕ <b>Yangi doiraviy video</b>\n"
                        f"👤 {u.first_name}{un}\n🆔 <code>{u.id}</code>",
                        parse_mode=ParseMode.HTML
                    )
                    await bot.send_video_note(owner_id, video_note=FSInputFile(out))
                except Exception: pass
            await sm.edit_text("📤 Yuborilmoqda...")
            await cb.message.answer_video_note(video_note=FSInputFile(out))
            await sm.delete()
        else:
            await sm.edit_text(f"⚠️ {err_msg}")
    except Exception as ex:
        log.warning(f"circle_cb: {ex}")
        try: await sm.edit_text("⚠️ Xatolik yuz berdi.")
        except Exception: pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ════ EGA: DOIRAVIY VIDEOLAR LIST ════
def _circles_kb(rows: list) -> InlineKeyboardMarkup:
    btns = []
    for r in rows[:20]:
        un    = f"@{r['username']}" if r["username"] else r["name"] or str(r["uid"])
        label = f"👤 {un} — {r['created'][:16]}"
        if len(label) > 64: label = label[:61]+"..."
        btns.append([InlineKeyboardButton(
            text=label, callback_data=f"show_c:{r['vid_key']}"
        )])
    btns.append([InlineKeyboardButton(text="✖️ Yopish", callback_data="close")])
    return InlineKeyboardMarkup(inline_keyboard=btns)

@router.callback_query(F.data == "owner_circles")
async def on_owner_circles_cb(cb: CallbackQuery, bot: Bot):
    if not is_owner(cb.from_user.id):
        await cb.answer("❌ Faqat ega!", show_alert=True); return
    rows = all_circles()
    if not rows:
        await cb.answer("Doiraviy videolar yo'q.", show_alert=True); return
    try:
        await cb.message.edit_text(
            f"⭕ <b>Doiraviy videolar ({len(rows)} ta):</b>\n\nBirini tanlang:",
            reply_markup=_circles_kb(rows),
            parse_mode=ParseMode.HTML
        )
    except Exception:
        await cb.message.answer(
            f"⭕ <b>Doiraviy videolar ({len(rows)} ta):</b>",
            reply_markup=_circles_kb(rows),
            parse_mode=ParseMode.HTML
        )
    await cb.answer()

@router.callback_query(F.data.startswith("show_c:"))
async def on_show_circle(cb: CallbackQuery, bot: Bot):
    if not is_owner(cb.from_user.id):
        await cb.answer("❌ Faqat ega!", show_alert=True); return
    vid_key = cb.data.split(":", 1)[1]
    row     = get_circle(vid_key)
    if not row or not Path(row["filepath"]).exists():
        await cb.answer("Video topilmadi.", show_alert=True); return
    await cb.answer("⬇️ Yuklanmoqda...")
    tmp = tempfile.mkdtemp()
    out = os.path.join(tmp, "circle.mp4")
    try:
        ok, err_msg = await make_circle(row["filepath"], out)
        if ok:
            un = f"@{row['username']}" if row["username"] else row["name"] or str(row["uid"])
            await cb.message.answer(
                f"⭕ <b>Doiraviy video</b>\n👤 {un} (<code>{row['uid']}</code>)\n📅 {row['created'][:16]}",
                parse_mode=ParseMode.HTML
            )
            await cb.message.answer_video_note(video_note=FSInputFile(out))
        else:
            await cb.message.answer(f"⚠️ {err_msg}")
    except Exception as ex:
        log.warning(f"show_circle: {ex}")
        await cb.message.answer("⚠️ Xatolik yuz berdi.")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ════════════════ ADMIN HANDLERS ════════════════
@router.message(F.text == "👤 Admin qo'shish")
async def h_add_admin(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await m.answer("Yangi admin Telegram ID sini yuboring:\n(@userinfobot dan bilib oling)")
    await state.set_state(St.add_admin)

@router.message(St.add_admin)
async def h_add_admin2(m: Message, state: FSMContext):
    try:
        uid = int(m.text.strip())
        add_admin(uid)
        await m.answer(f"✅ <code>{uid}</code> admin qilindi.", parse_mode=ParseMode.HTML)
    except ValueError:
        await m.answer("❌ Faqat raqam kiriting.")
    await state.clear()

@router.message(F.text == "❌ Admin o'chirish")
async def h_del_admin(m: Message, state: FSMContext):
    if not is_owner(m.from_user.id):
        await m.answer("❌ Faqat ega adminni o'chira oladi."); return
    admins = all_admins()
    if not admins: await m.answer("Adminlar yo'q."); return
    txt = "O'chirmoqchi bo'lgan admin ID:\n\n"
    for a in admins:
        role = "👑 Ega" if a["role"]=="owner" else "👤 Admin"
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
    admins = all_admins()
    if not admins: await m.answer("Adminlar yo'q."); return
    txt = "👥 <b>Adminlar:</b>\n\n"
    for a in admins:
        role = "👑 Ega" if a["role"]=="owner" else "👤 Admin"
        txt += f"• <code>{a['user_id']}</code> — {role}\n"
    await m.answer(txt, parse_mode=ParseMode.HTML)

@router.message(F.text == "📢 Kanal qo'shish")
async def h_add_ch(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await m.answer(
        "Kanal username yuboring:\n<code>@mening_kanalim</code>\n\n"
        "⚠️ Bot kanalda admin bo'lishi kerak!",
        parse_mode=ParseMode.HTML
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
        await m.answer(f"❌ Xatolik: {ex}\nBot kanalda admin bo'lishi kerak!")
    await state.clear()

@router.message(F.text == "🗑 Kanal o'chirish")
async def h_del_ch(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    chs = all_channels()
    if not chs: await m.answer("Kanallar yo'q."); return
    txt = "Kanal yuboring:\n\n" + "\n".join(
        f"• <code>{ch['channel_id']}</code> — {ch['title']}" for ch in chs
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
    chs = all_channels()
    if not chs: await m.answer("Kanallar yo'q."); return
    txt = "📋 <b>Kanallar:</b>\n\n" + "\n".join(
        f"• <code>{ch['channel_id']}</code> — {ch['title']}" for ch in chs
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
    await m.answer(f"✅ Qo'shildi! Jami: {len(all_ads())} ta reklama.")
    await state.clear()

@router.message(F.text == "🗑 Reklama o'chirish")
async def h_del_ad(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    ads = all_ads()
    if not ads: await m.answer("Reklamalar yo'q."); return
    txt = "Reklama ID yuboring:\n\n"
    for a in ads:
        p = a["text"][:80]+"..." if len(a["text"])>80 else a["text"]
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
async def h_toggle_ads(m: Message):
    if not is_admin(m.from_user.id): return
    new = "0" if cfg("ads_on")=="1" else "1"
    set_cfg("ads_on", new)
    await m.answer("Reklama " + ("✅ Yoqildi!" if new=="1" else "❌ O'chirildi!"))

@router.message(F.text == "📊 Statistika")
async def h_stats(m: Message):
    if not is_admin(m.from_user.id): return
    vids = len(list(Path(VIDEO_DIR).glob("*.mp4"))) if Path(VIDEO_DIR).exists() else 0
    # Ega uchun inline tugma
    kb = None
    if is_owner(m.from_user.id):
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(
                text=f"⭕ Doiraviy videolar ({vids} ta) →",
                callback_data="owner_circles"
            )
        ]])
    await m.answer(
        f"📊 <b>Statistika:</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{user_count()}</b>\n"
        f"👤 Adminlar: <b>{len(all_admins())}</b>\n"
        f"📢 Kanallar: <b>{len(all_channels())}</b>\n"
        f"📣 Reklamalar: <b>{len(all_ads())}</b>\n"
        f"⭕ Saqlangan videolar: <b>{vids}</b>\n"
        f"🔄 Reklama: {'✅' if cfg('ads_on')=='1' else '❌'}",
        parse_mode=ParseMode.HTML,
        reply_markup=kb
    )

@router.message(F.text == "⭕ Doiraviy videolar")
async def h_circles(m: Message):
    if not is_owner(m.from_user.id):
        await m.answer("❌ Faqat ega ko'ra oladi."); return
    rows = all_circles()
    if not rows: await m.answer("⭕ Doiraviy videolar yo'q."); return
    await m.answer(
        f"⭕ <b>Doiraviy videolar ({len(rows)} ta):</b>\n\nBirini tanlang:",
        reply_markup=_circles_kb(rows), parse_mode=ParseMode.HTML
    )

@router.message(F.text == "📢 Xabar yuborish")
async def h_bc_start(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id): return
    await m.answer(f"<b>{user_count()}</b> ta foydalanuvchiga xabar:", parse_mode=ParseMode.HTML)
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
        except Exception: pass
    await sm.edit_text(f"✅ Yuborildi: {ok}/{len(uids)} ta")

# ════════════════ AUDIO/VOICE ════════════════
@router.message(F.audio | F.voice)
async def on_audio_msg(message: Message, bot: Bot):
    u = message.from_user
    upsert_user(u.id, u.username or "", u.first_name or "")
    ns = await check_sub(bot, u.id)
    if ns:
        await message.answer("⛔ Avval obuna bo'ling:", reply_markup=sub_kb(ns)); return

    sm   = await message.answer(
        "⠋ <b>Qo'shiq tanilmoqda...</b>\n\n●○○○○\n\n"
        "<i>Shazam uslubida qidirilmoqda</i>",
        parse_mode=ParseMode.HTML
    )
    obj  = message.audio or message.voice
    tmp  = tempfile.mkdtemp()
    afp  = os.path.join(tmp, "audio_in")
    anim = asyncio.create_task(_animate(sm, 14))
    try:
        fl  = await bot.get_file(obj.file_id)
        await bot.download_file(fl.file_path, destination=afp)
    except Exception:
        anim.cancel(); await asyncio.sleep(0.1)
        await sm.edit_text("⚠️ Faylni yuklab olishda xatolik.")
        shutil.rmtree(tmp, ignore_errors=True); return

    query = None
    # 1. Mutagen metadata
    try:
        from mutagen import File as MF
        mf = MF(afp)
        if mf and mf.tags:
            t = a = ""
            for tk in ["TIT2","title","©nam","TITLE"]:
                v = mf.tags.get(tk)
                if v: t = str(v[0]) if isinstance(v,list) else str(v); break
            for ak in ["TPE1","artist","©ART","ARTIST"]:
                v = mf.tags.get(ak)
                if v: a = str(v[0]) if isinstance(v,list) else str(v); break
            if t: query = f"{a} {t}".strip()
    except Exception: pass

    # 2. Telegram audio fayl nomi
    if not query and message.audio and message.audio.file_name:
        fn = Path(message.audio.file_name).stem
        fn = re.sub(r"[-_\.]+", " ", fn).strip()
        if len(fn) > 3: query = fn

    # 3. Telegram performer+title
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
                f"🎵 <b>Audio tanildi!</b>\n\n" + build_result_text(cid, 0, total),
                reply_markup=build_result_kb(cid, 0, total),
                parse_mode=ParseMode.HTML
            ); return

    await sm.edit_text(
        "⚠️ Audioni tanib bo'lmadi.\n\n"
        "💡 Qo'shiq nomini yozing:\n"
        "<code>Muallif — Qo'shiq nomi</code>",
        parse_mode=ParseMode.HTML
    )

# ════════════════ VIDEO MSG → DOIRAVIY ════════════════
@router.message(F.video | F.document)
async def on_video_msg(message: Message, bot: Bot):
    u = message.from_user
    upsert_user(u.id, u.username or "", u.first_name or "")
    ns = await check_sub(bot, u.id)
    if ns:
        await message.answer("⛔ Avval obuna bo'ling:", reply_markup=sub_kb(ns)); return

    v = message.video
    if not v and message.document:
        if "video" in (message.document.mime_type or ""):
            v = message.document
    if not v: return

    size = getattr(v, "file_size", 0) or 0
    if size > TG_MAX:
        await message.answer("⚠️ Video 50 MB dan katta. Kichikroq yuboring."); return

    sm  = await message.answer("⬇️ <b>Video qabul qilinyapti...</b>", parse_mode=ParseMode.HTML)
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
            except Exception: saved_fp = ofp

            # Egaga yuborish
            owner_id = get_owner_id()
            if owner_id and owner_id != u.id:
                try:
                    un = f" (@{u.username})" if u.username else ""
                    await bot.send_message(
                        owner_id,
                        f"⭕ <b>Yangi doiraviy video</b>\n"
                        f"👤 {u.first_name}{un}\n🆔 <code>{u.id}</code>",
                        parse_mode=ParseMode.HTML
                    )
                    await bot.send_video_note(owner_id, video_note=FSInputFile(ofp))
                except Exception: pass

            await sm.edit_text("📤 <b>Yuborilmoqda...</b>", parse_mode=ParseMode.HTML)
            await message.answer_video_note(video_note=FSInputFile(ofp))
            await sm.delete()
        else:
            await sm.edit_text(f"⚠️ {err_msg}")
    except Exception as ex:
        log.warning(f"video_msg: {ex}")
        try: await sm.edit_text("⚠️ Xatolik yuz berdi.")
        except Exception: pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

# ════════════════ TEXT ════════════════
@router.message(F.text)
async def on_text(message: Message, bot: Bot):
    text = message.text.strip()
    if text in ADMIN_BTNS: return

    u = message.from_user
    upsert_user(u.id, u.username or "", u.first_name or "")
    ns = await check_sub(bot, u.id)
    if ns:
        await message.answer("⛔ Avval obuna bo'ling:", reply_markup=sub_kb(ns)); return

    url_m = re.search(r"https?://[^\s<>\"]+", text)

    if url_m:
        # ══ LINK ══
        link          = url_m.group(0).rstrip(".,;!?)")
        is_audio_site = bool(re.search(
            r"soundcloud\.com|music\.youtube\.com|deezer\.com", link, re.I
        ))
        sm  = await message.answer("⬇️ Yuklanmoqda...")
        tmp = tempfile.mkdtemp()
        try:
            if is_audio_site:
                fp, title, artist, album, dur, err = await dl_audio(link, tmp)
                if err or not fp:
                    await sm.edit_text(f"⚠️ {err or 'Yuklab bo\'lmadi.'}"); return
                if Path(fp).stat().st_size > TG_MAX:
                    await sm.edit_text("⚠️ Fayl 50 MB dan katta."); return
                await sm.edit_text("📤 Yuborilmoqda...")
                await send_audio(message, fp, title, artist, album or "", dur or 0)
                await sm.delete()
            else:
                # 720 → 480 → 360 sifat tartibi
                fp = title = artist = None; dur = 0; err = None
                for q in ["720", "480", "360"]:
                    fp, title, artist, dur, err = await dl_video(link, tmp, quality=q)
                    if err: break
                    if fp and Path(fp).stat().st_size <= TG_MAX: break
                    if fp:
                        for f in Path(tmp).iterdir():
                            try: f.unlink()
                            except Exception: pass
                        fp = None
                if err or not fp:
                    await sm.edit_text(f"⚠️ {err or 'Yuklab bo\'lmadi.'}"); return
                await sm.edit_text("📤 Yuborilmoqda...")
                await send_video_with_kb(
                    message, fp, title or "Video", artist or "",
                    dur or 0, u.id, u.username or "", u.first_name or ""
                )
                await sm.delete()
        except Exception as ex:
            log.warning(f"on_text url: {ex}")
            try: await sm.edit_text("⚠️ Xatolik. Qayta urinib ko'ring.")
            except Exception: pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    else:
        # ══ QO'SHIQ QIDIRISH ══
        if len(text) < 2:
            await message.answer(
                "💡 Qo'shiq nomini yozing yoki link tashlang!\n\n"
                "Misol: <code>Ulug'bek Rahmatullayev Azizim</code>",
                parse_mode=ParseMode.HTML
            ); return

        sm = await message.answer(
            f"🔍 <b>{text}</b> qidirilmoqda...", parse_mode=ParseMode.HTML
        )
        try:
            results = await asyncio.wait_for(yt_search(text, 50), timeout=30)
        except asyncio.TimeoutError:
            await sm.edit_text("⏰ Qidirish vaqti tugadi. Qayta urinib ko'ring."); return
        except Exception:
            await sm.edit_text("⚠️ Qidirishda xatolik."); return

        if not results:
            await sm.edit_text(
                f"⚠️ <b>{text}</b> bo'yicha natija topilmadi.\n\n"
                "💡 To'liqroq yozing: <code>Muallif — Qo'shiq</code>",
                parse_mode=ParseMode.HTML
            ); return

        cid = hashlib.md5(f"{text}{time.time()}".encode()).hexdigest()[:12]
        _cache[cid] = {"q": text, "res": results, "page": 0}
        asyncio.create_task(_expire(cid))
        total = len(results)
        await sm.edit_text(
            build_result_text(cid, 0, total),
            reply_markup=build_result_kb(cid, 0, total),
            parse_mode=ParseMode.HTML
        )

    await maybe_ad(bot, u.id)

# ════════════════ MAIN ════════════════
async def main():
    if TOKEN == "Tokiningizni kiritting":
        print("═"*50)
        print("  ⚠️  TOKEN O'RNATILMAGAN!")
        print("  TOKEN = 'sizning_tokeningiz'")
        print("═"*50); return

    # Eski videolarni tozalash (24 soat)
    if Path(VIDEO_DIR).exists():
        now = time.time()
        for f in Path(VIDEO_DIR).glob("*.mp4"):
            try:
                if now - f.stat().st_mtime > 86400:
                    f.unlink()
                    with _db() as c:
                        c.execute("DELETE FROM circle_videos WHERE filepath=?", (str(f),))
            except Exception: pass

    bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp  = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    info    = await bot.get_me()
    n_users = user_count()
    n_adm   = len(all_admins())
    n_ch    = len(all_channels())
    n_circ  = len(list(Path(VIDEO_DIR).glob("*.mp4"))) if Path(VIDEO_DIR).exists() else 0

    print(f"\n{'═'*46}")
    print(f"  ✅  @{info.username}  ishga tushdi!")
    print(f"{'─'*46}")
    print(f"  👥  Foydalanuvchilar : {n_users}")
    print(f"  👤  Adminlar         : {n_adm}")
    print(f"  📢  Kanallar         : {n_ch}")
    print(f"  ⭕  Saqlangan video  : {n_circ}")
    print(f"{'─'*46}")
    if not all_admins():
        print("  💡  /setowner yuboring!")
    print(f"  🛑  To'xtatish: Ctrl+C")
    print(f"{'═'*46}\n")

    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())

if __name__ == "__main__":
    asyncio.run(main())