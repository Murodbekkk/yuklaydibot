#!/bin/bash
# muzffa_bot o'rnatish skripti

echo "======================================"
echo "  muzffa_bot o'rnatish boshlandi...   "
echo "======================================"

# Python tekshirish
if ! command -v python3 &> /dev/null; then
    echo "❌ Python3 topilmadi. Avval Python3 o'rnating."
    exit 1
fi

echo "✅ Python3 mavjud"

# pip tekshirish
if ! command -v pip3 &> /dev/null; then
    echo "pip3 o'rnatilmoqda..."
    sudo apt-get install python3-pip -y
fi

# Kutubxonalar o'rnatish
echo ""
echo "📦 Kerakli kutubxonalar o'rnatilmoqda..."
pip3 install aiogram yt-dlp aiohttp aiofiles

echo ""
echo "======================================"
echo "  ✅ O'rnatish tugadi!               "
echo "======================================"
echo ""
echo "📌 FOYDALANISH YO'RIQNOMASI:"
echo ""
echo "1. muzffa_bot.py faylini oching"
echo "2. TOKEN = 'Tokiningizni kiritting' qatorini toping"
echo "3. Tokeningizni o'rnating, masalan:"
echo "   TOKEN = '1234567890:AAFxxxxxxxxxxxxxx'"
echo ""
echo "4. Birinchi adminni qo'shish:"
echo "   - bot_data.json faylini oching (agar yo'q bo'lsa yaratiladi)"
echo "   - Yoki bot ishga tushganidan keyin /setadmin buyrug'i bilan"
echo ""
echo "5. Botni ishga tushirish:"
echo "   python3 muzffa_bot.py"
echo ""
echo "6. Telegram'da /admin buyrug'i bilan admin paneliga kiring"
echo ""
echo "⚠️  BOT KANALDA ADMIN BO'LISHI KERAK!"
echo "======================================"
