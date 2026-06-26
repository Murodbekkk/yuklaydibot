# 🤖 muzffa_bot — Telegram Bot

## 📌 Funksiyalar

| Funksiya | Tavsif |
|---------|--------|
| 🎬 Video yuklab olish | YouTube, Instagram, Facebook, TikTok, Twitter/X, Vimeo va boshqalar |
| 🎵 Audio yuklab olish | SoundCloud, YouTube Music va boshqalar |
| 📢 Majburiy obuna | Kanallarni qo'shish/o'chirish |
| 📣 Reklama | Foydalanuvchilarga avtomatik reklama |
| 👤 Admin panel | Adminlarni boshqarish |

---

## ⚙️ O'rnatish

### 1. Kerakli kutubxonalarni o'rnatish:
```bash
pip3 install aiogram yt-dlp aiohttp aiofiles
```

### 2. Token o'rnatish:
`muzffa_bot.py` faylini oching va:
```python
TOKEN = "Tokiningizni kiritting"
```
Bu qatorga BotFather dan olgan tokeningizni kiriting:
```python
TOKEN = "1234567890:AAFxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

### 3. Birinchi adminni qo'shish:
`bot_data.json` faylini yarating:
```json
{
  "admins": [YOUR_TELEGRAM_ID],
  "channels": [],
  "ads": [],
  "ads_enabled": false,
  "ads_interval": 5,
  "message_counts": {}
}
```
> Telegram ID ni bilish uchun: [@userinfobot](https://t.me/userinfobot) ga `/start` yuboring

### 4. Botni ishga tushirish:
```bash
python3 muzffa_bot.py
```

---

## 👤 Admin Buyruqlari

Telegram'da `/admin` buyrug'ini yuboring:

| Tugma | Vazifa |
|-------|--------|
| 👤 Admin qo'shish | Yangi admin qo'shish (ID orqali) |
| ❌ Admin o'chirish | Adminni o'chirish |
| 👥 Adminlar ro'yxati | Barcha adminlarni ko'rish |
| 📢 Kanal qo'shish | Majburiy obuna kanalini qo'shish |
| 🗑 Kanal o'chirish | Kanalni o'chirish |
| 📋 Kanallar ro'yxati | Barcha kanallarni ko'rish |
| 📣 Reklama qo'shish | Yangi reklama matni qo'shish |
| 🗑 Reklama o'chirish | Reklamani o'chirish |
| 📊 Reklama holati | Reklama statistikasi |
| 🔄 Reklamani yoq/o'chir | Reklamani faollashtirish/o'chirish |

---

## 📢 Majburiy Obuna Qanday Ishlaydi?

1. Admin kanal qo'shadi (bot kanalda **admin** bo'lishi shart!)
2. Foydalanuvchi botga murojaat qilganda obuna tekshiriladi
3. Obuna bo'lmagan bo'lsa — kanal tugmalari ko'rsatiladi
4. "✅ Tekshirish" tugmasi bosilganda qayta tekshiriladi

---

## 🎬 Qo'llab Quvvatlanadigan Saytlar

- ✅ YouTube (video + shorts)
- ✅ Instagram (post, reel, stories)
- ✅ Facebook
- ✅ TikTok
- ✅ Twitter / X
- ✅ SoundCloud
- ✅ Vimeo
- ✅ Dailymotion
- ✅ Twitch clips
- ✅ Reddit video
- ✅ 1000+ sayt (yt-dlp yordamida)

---

## ⚠️ Muhim Eslatmalar

1. **Bot kanalda admin bo'lishi kerak** — majburiy obuna ishlashi uchun
2. **Fayl hajmi cheklovi** — 50 MB gacha (Telegram limiti)
3. **ffmpeg o'rnatish tavsiya etiladi** — audio konvertatsiya uchun:
   ```bash
   sudo apt-get install ffmpeg
   ```
4. **Serverda ishlatish** — uzluksiz ishlash uchun `screen` yoki `systemd` ishlatishingiz mumkin:
   ```bash
   screen -S muzffa_bot
   python3 muzffa_bot.py
   # Ctrl+A, D — fonda qoldirish
   ```

---

## 🛠 Texnik Ma'lumotlar

- **Kutubxona:** aiogram 3.x (asinxron, tez)
- **Video yuklab olish:** yt-dlp (1000+ sayt)
- **Ma'lumotlar saqlash:** JSON fayl (bot_data.json)
- **Arxitektura:** Asinxron (asyncio) — juda tez!

---

*@muzffa_bot — Barcha funksiyalar bir joyda! 🚀*
