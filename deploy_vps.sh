#!/usr/bin/env bash
# ==============================================================================
# Script Otomatis Deploy Telegram OTP Bot ke Ubuntu VPS (43.129.35.148)
# Repository: https://github.com/griyani-mesmadari/otpmail
# ==============================================================================

set -e

# Warna terminal untuk info
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}======================================================${NC}"
echo -e "${BLUE}🚀 MEMULAI DEPLOYMENT BOT OTP EMAIL KE UBUNTU VPS${NC}"
echo -e "${BLUE}======================================================${NC}"

# 1. Pastikan dijalankan sebagai root atau dengan sudo
if [ "$EUID" -ne 0 ]; then
  echo -e "${RED}❌ Script ini harus dijalankan sebagai root (atau gunakan sudo bash deploy_vps.sh)${NC}"
  exit 1
fi

# Tentukan APP_DIR (gunakan direktori saat ini jika sudah di dalam repo, atau default ke /root/otpmail)
if [ -f "$PWD/bot.py" ] && [ -d "$PWD/.git" ]; then
    APP_DIR="$PWD"
else
    APP_DIR="/root/otpmail"
fi
SERVICE_NAME="otpbot"

# 2. Update sistem dan install paket yang dibutuhkan
echo -e "\n${YELLOW}[1/7] Memperbarui paket sistem Ubuntu & install dependensi dasar...${NC}"
apt-get update -y
apt-get install -y python3 python3-pip python3-venv git curl ufw

# 3. Setup direktori project & clone/pull repository
echo -e "\n${YELLOW}[2/7] Menyiapkan repository di ${APP_DIR}...${NC}"
if [ "$APP_DIR" = "$PWD" ]; then
    echo -e "${GREEN}Menjalankan script dari dalam direktori repo saat ini: ${APP_DIR}${NC}"
    git fetch origin main || true
elif [ -d "$APP_DIR/.git" ]; then
    echo -e "${GREEN}Direktori repository sudah ada, melakukan git pull terbaru...${NC}"
    cd "$APP_DIR"
    git fetch origin main
    git reset --hard origin/main
else
    echo -e "${GREEN}Melakukan clone repository ke ${APP_DIR}...${NC}"
    rm -rf "$APP_DIR"
    git clone https://github.com/griyani-mesmadari/otpmail.git "$APP_DIR"
    cd "$APP_DIR"
fi

# 4. Setup Python Virtual Environment (venv) & Install Requirements
echo -e "\n${YELLOW}[3/7] Membuat virtual environment & install library Python...${NC}"
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 5. Install Playwright Chromium & dependensi Linux (libnss3, libatk, font, dsb)
echo -e "\n${YELLOW}[4/7] Menginstall browser Playwright Chromium beserta dependensi Linux...${NC}"
playwright install --with-deps chromium

# 6. Buat folder data dan file .env jika belum ada
echo -e "\n${YELLOW}[5/7] Konfigurasi environment (.env) dan file sesi...${NC}"
mkdir -p data

if [ ! -f .env ]; then
    echo -e "${GREEN}Menulis file .env default...${NC}"
    cat << 'EOF' > .env
# ==========================================
# KONFIGURASI TELEGRAM BOT OTP EMAIL (VPS)
# ==========================================
BOT_TOKEN=8797567637:AAGoMx_vNbYbKsiEI953JP5zmM8aLeqWzZ0
ADMIN_IDS=8403291265
OTP_PRICE=200
MIN_TOPUP=5000
PORT=10000

PAYMENT_DANA=0812-3456-7890 (a.n Ramba Store)
PAYMENT_GOPAY=0812-3456-7890 (a.n Ramba Store)
PAYMENT_OVO=0812-3456-7890 (a.n Ramba Store)
PAYMENT_BANK=BCA: 1234567890 a.n Ramba Store
STATIC_QRIS=00020101021126610014COM.GO-JEK.WWW01189360091434782190150210G4782190150303UMI51440014ID.CO.QRIS.WWW0215ID10253756120210303UMI5204581553033605802ID5925PT SERVER HANDAL, SUKOLIL6008SURABAYA61056011762070703A0163042EB1
SMAILPRO_HEADLESS=true
QRISHOOK_SECRET=
AUTOGOPAY_BASE_URL=https://v1-gateway.autogopay.site
AUTOGOPAY_API_KEY=
SMAILPRO_SESSION_B64=ewogICJjb29raWVzIjogWwogICAgewogICAgICAibmFtZSI6ICJYU1JGLVRPS0VOIiwKICAgICAgInZhbHVlIjogImV5SnBkaUk2SWxONWRsWTNOR1ZQTVZJeVp6WktlbWxEUzJScGVFRTlQU0lzSW5aaGJIVmxJam9pV0VaTlQzRjNhV28wU0hsWldpdG5Sa3NyUTFZdmQwVmlWWGx6Tm01V1JYSnRUV1JEVGsxQk1GRlBjbW96Y0U1NVZDdExNVWhEWWtObEsxcFhTbXRIWmxWblJUbEtkVGs1VldKVE1rTlJWM28zWmprMU5VTktTM2RVTURNMlV6SjFiVGRpUkN0V2FFUXdlVzg1Vmt0WldrWnFNVzk2Y0VSUmJscEJNV2xpVmtnaUxDSnRZV01pT2lJNVkySmpNalZqTkdObU5UWTFNemd3TWpnNE5HTXlPRFU1TkdKa05qQmhORGRqWVRsak5qZG1ZVFExWlROa09XWmhZemRtWlRrNFlXRTNPVFZoWVRVNUlpd2lkR0ZuSWpvaUluMCUzRCIsCiAgICAgICJkb21haW4iOiAibXkuc29uamouY29tIiwKICAgICAgInBhdGgiOiAiLyIsCiAgICAgICJleHBpcmVzIjogMTc5Mzk3MjUwMy40ODgzNSwKICAgICAgImh0dHBPbmx5IjogZmFsc2UsCiAgICAgICJzZWN1cmUiOiBmYWxzZSwKICAgICAgInNhbWVTaXRlIjogIkxheCIKICAgIH0sCiAgICB7CiAgICAgICJuYW1lIjogInNvbmpqX3Nlc3Npb24iLAogICAgICAidmFsdWUiOiAiZXlKcGRpSTZJbGxzTVRkTmQxY3pSbmt3WjJGc2VIRXJNMUZsV2tFOVBTSXNJblpoYkhWbElqb2lXazFMVFhOUlNHNTFlVTFxTW1ZNFRqZEJRMlZIZW1WRVREbHJTMGx3VUhOTlkyOU1jbm8zZUhBM2FpdDVWRUl4ZWtGNFRXRndSeXRKY21WMU5FWlFaelpMT1ZKdVpEWXpTV2hEYkM5MWVrVndibEZRYUZCMGNFMU5aa1o1UlVzME1tWjJhSGhtU2xsdFYwSnNURXhvZGsxV2JHcGlVRzlGVUZOVVEzSktXWFVpTENKdFlXTWlPaUl4TldKaFpHRTJOVFprTURVME5qTTRObVEzWldFek5tWTBOMkl3T1dNeU5ETXhaR1E0TW1KbU1HVXdORE0zTURNNE9EWXlZVEkwTldZMU16Wm1PV1JqSWl3aWRHRm5Jam9pSW4wJTNEIiwKICAgICAgImRvbWFpbiI6ICJteS5zb25qai5jb20iLAogICAgICAicGF0aCI6ICIvIiwKICAgICAgImV4cGlyZXMiOiAxNzkzOTcyNTAzLjQ4ODU2MSwKICAgICAgImh0dHBPbmx5IjogdHJ1ZSwKICAgICAgInNlY3VyZSI6IGZhbHNlLAogICAgICAic2FtZVNpdGUiOiAiTGF4IgogICAgfSwKICAgIHsKICAgICAgIm5hbWUiOiAiWFNSRi1UT0tFTiIsCiAgICAgICJ2YWx1ZSI6ICJleUpwZGlJNklsaHRNMDVIWXk5UFdIRmxSMmR6WjFSTEsxRTBUMUU5UFNJc0luWmhiSFZsSWpvaVJXSm5URTl3VFZOb2JISnpSM3BoWVhseWJEQjZWVGh3ZEVaU1NtZHNkVFp4UVVSbFFqTnVTVlZuT1dGVU5UQlFWSFJUZGpZclFWVnBaa3A1U0ZJMGNrdE5WRVZ3UzBSM2EzVk1UWFpIVDFORVkyZGFkaTh6ZDBNNFdYZEhNek00WnpoTGJHOWpRMUIyVlRsVlZteDBTR1ZGVVZrME0xcEtRWEZrVmxWSmVrMGlMQ0p0WVdNaU9pSmlNVEEwTURNNE1HTmlNV1F6TnprMFkyVTFaVGhtTkRFM1ltVTJNMlJsTldaaE1qTmpOemhoTjJRd1lUQmhZVGM1WlRGa09ESm1abVppTjJVek1qazRJaXdpZEdGbklqb2lJbjAlM0QiLAogICAgICAiZG9tYWluIjogInNtYWlscHJvLmNvbSIsCiAgICAgICJwYXRoIjogIi8iLAogICAgICAiZXhwaXJlcyI6IDE3OTM5NzI1NTEuOTUwMzEzLAogICAgICAiaHR0cE9ubHkiOiBmYWxzZSwKICAgICAgInNlY3VyZSI6IGZhbHNlLAogICAgICAic2FtZVNpdGUiOiAiTGF4IgogICAgfSwKICAgIHsKICAgICAgIm5hbWUiOiAic29uampfc2Vzc2lvbiIsCiAgICAgICJ2YWx1ZSI6ICJleUpwZGlJNkluSTFaR3RLWkdkVlZqSTRNbmxvZW5wc2NHRnhjWGM5UFNJc0luWmhiSFZsSWpvaU56WkVUSEY2YUdneWRrOVJVVU52TDNKNk9HdEhUelk0ZG00ck0yWjJWRlp4V1RCbVowbzRUSEJXVmtadlMxQnpaWHBGTDNFcmVqRk1Na3RJWVZwQ2RtOUdLMkpVYm1GdWRGWlZlbEk1TlZoVVVHbFdZbWxOWTI5eGQzUnFVRlUxVkRFM2NtNjBiV3hSV0ZJek56UkVNamgxY0hWQldIZGhUQ3N3VUZKQ09Xc2lMQ0p0WVdNaU9pSmtObU5pWW1ReFltVXpPR0UxTWpreFpHRTROREExWkdFNE9ERXpNelEyWTJVd1lUUXpNbVU0TmpJNVpUWXdaR1JsWm1FMk16bGhZVFkwTVRNME56SXdJaXdpZEdGbklqb2lJbjAlM0QiLAogICAgICAiZG9tYWluIjogInNtYWlscHJvLmNvbSIsCiAgICAgICJwYXRoIjogIi8iLAogICAgICAiZXhwaXJlcyI6IDE3OTM5NzI1NTEuOTUwNDU1LAogICAgICAiaHR0cE9ubHkiOiB0cnVlLAogICAgICAic2VjdXJlIjogZmFsc2UsCiAgICAgICJzYW1lU2l0ZSI6ICJMYXgiCiAgICB9CiAgXSwKICAic2F2ZWRfYXQiOiAiMjAyNi0xMS0wNSAyMDo0MjozOSIsCiAgInN0YXR1cyI6IHsKICAgICJpc0xvZ2dlZEluIjogZmFsc2UsCiAgICAiaXNQcmVtaXVtIjogZmFsc2UKICB9Cn0=
EOF
    echo -e "${GREEN}File .env berhasil dibuat.${NC}"
else
    echo -e "${GREEN}File .env sudah ada, mempertahankan konfigurasi yang ada.${NC}"
fi

# 7. Konfigurasi Firewall UFW (Pastikan port SSH 22 & Webhook 10000 terbuka)
echo -e "\n${YELLOW}[6/7] Membuka port firewall (Port 22 SSH & Port 10000 Webhook QRIS)...${NC}"
ufw allow 22/tcp || true
ufw allow 10000/tcp || true
if ! ufw status | grep -q "Status: active"; then
    echo "y" | ufw enable || true
fi

# 8. Buat dan daftarkan Systemd Service (Otomatis start saat boot & restart jika crash)
echo -e "\n${YELLOW}[7/7] Membuat systemd service '${SERVICE_NAME}' agar bot berjalan 24/7...${NC}"
cat << EOF > /etc/systemd/system/${SERVICE_NAME}.service
[Unit]
Description=Telegram OTP Email Bot & QRISHook Webhook Service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin"
ExecStart=${APP_DIR}/venv/bin/python3 ${APP_DIR}/bot.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME}
systemctl restart ${SERVICE_NAME}

echo -e "\n${GREEN}======================================================${NC}"
echo -e "${GREEN}🎉 DEPLOYMENT SELESAI & BOT BERHASIL DIAKTIFKAN!${NC}"
echo -e "${GREEN}======================================================${NC}"
echo -e "Status Service:"
systemctl status ${SERVICE_NAME} --no-pager -n 5

echo -e "\n${BLUE}📌 PANDUAN PENGATURAN AUTOGOPAY CALLBACK (OPSIONAL):${NC}"
echo -e "Sistem sudah dilengkapi Auto-Polling real-time ke AutoGoPay API."
echo -e "Jika ingin mengaktifkan Webhook Callback instan di dashboard AutoGoPay:"
echo -e "URL Callback Webhook: ${YELLOW}http://43.129.35.148:10000/webhook/qris${NC}"

echo -e "\n${BLUE}💡 PERINTAH PENTING UNTUK PEMELIHARAAN:${NC}"
echo -e "• Cek log live bot:    ${YELLOW}journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "• Restart bot:         ${YELLOW}systemctl restart ${SERVICE_NAME}${NC}"
echo -e "• Stop bot:            ${YELLOW}systemctl stop ${SERVICE_NAME}${NC}"
echo -e "• Status bot:          ${YELLOW}systemctl status ${SERVICE_NAME}${NC}"
echo -e "======================================================\n"
