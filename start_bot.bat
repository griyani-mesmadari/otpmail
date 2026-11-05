@echo off
title Bot Telegram OTP Email (Smailpro Premium)
color 0b

echo ========================================================
echo       BOT TELEGRAM OTP EMAIL GMAIL & OUTLOOK
echo               Powered by SmailPro Premium
echo ========================================================
echo.

if not exist .env (
    echo [!] File .env tidak ditemukan!
    echo Menyalin .env.example menjadi .env...
    copy .env.example .env
    echo.
    echo Silakan edit file .env dan masukkan BOT_TOKEN Anda terlebih dahulu.
    pause
    exit /b
)

echo Memulai bot...
python bot.py
echo.
echo Bot telah berhenti.
pause
