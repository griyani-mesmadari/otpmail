# 🤖 Bot Telegram OTP Email (Gmail & Outlook) — SmailPro Premium

Bot Telegram otomatis untuk penjualan dan penyewaan alamat email sementara (**Gmail & Outlook / Hotmail**) dari **SmailPro Premium**, dilengkapi sistem saldo / top up, approval admin, auto-polling inbox, dan ekstraksi kode OTP otomatis.

---

## 🌟 Fitur Utama

1. **📧 Pesan OTP Gmail & 📬 Pesan OTP Outlook:**
   - Otomatis membuka `https://smailpro.com/temporary-email` menggunakan sesi akun Premium Anda.
   - Menghasilkan 1 alamat email Gmail atau Outlook baru secara instan.
   - Mengirimkan alamat email ke pembeli dengan format monospace yang mudah disalin (*1-tap copy*).
2. **💰 Sistem Saldo & Pemotongan Adil (Rp 200 / OTP):**
   - Tiap user memiliki saldo di database SQLite lokal.
   - **Ketentuan Adil:** Saldo pembeli **HANYA DIPOTONG Rp 200 JIKA KODE OTP / INBOX SUDAH MASUK**.
   - Jika belum ada email masuk, saldo tidak akan berkurang sepeserpun.
   - Minimal saldo untuk memesan adalah Rp 200, dan minimal top up adalah Rp 5.000.
3. **📩 Request Inbox & Ekstraksi OTP Otomatis:**
   - Pembeli dapat menekan tombol **`[📩 Cek Inbox / OTP]`**.
   - Bot membaca isi email dari SmailPro, otomatis mem-parsing pengirim, subjek, serta mengekstrak **kode OTP (4 - 8 digit)** dengan regex cerdas.
   - Tersedia tombol **`[🔄 Auto Refresh (3 Menit)]`** yang otomatis memeriksa inbox setiap 5 detik tanpa perlu diklik manual berulang kali.
4. **💳 Top Up Saldo & Notifikasi Bukti Transfer ke Admin:**
   - Pembeli memilih nominal (Rp 5.000, 10.000, 20.000, 50.000, atau nominal kustom).
   - Bot menampilkan instruksi pembayaran (QRIS, Dana, GoPay, OVO, Bank Transfer).
   - Pembeli mengirimkan foto bukti transfer.
   - Bot meneruskan bukti pembayaran ke Telegram Admin secara otomatis dengan tombol inline interaktif:
     - `[✅ Terima (Rp X)]` ➡️ Saldo user langsung terisi otomatis dan user mendapat notifikasi.
     - `[❌ Tolak]` ➡️ Notifikasi penolakan dikirim ke user.
5. **⚙️ Panel Admin Lengkap:**
   - Statistik bot: total user, total order, total omset, saldo mengendap, dan status akun Smailpro.
   - Perintah admin: `/tambahsaldo <user_id> <jumlah>`, `/kurangsaldo`, `/broadcast <pesan>`.

---

## 📁 Struktur File Proyek

```
BOT OTP EMAIL/
├── .env.example          # Template konfigurasi environment
├── .env                  # File konfigurasi bot Anda (buat dari .env.example)
├── config.py             # Loader konfigurasi
├── database.py           # Layer SQLite database (User, Order, Topup, Transaksi)
├── smailpro_engine.py    # Mesin Playwright otomatisasi SmailPro Premium
├── session_login.py      # Script login 1-kali ke akun Smailpro Premium
├── bot.py                # Kode utama Telegram Bot
├── login_smailpro.bat    # Pintasan Windows untuk login Smailpro
├── start_bot.bat         # Pintasan Windows untuk menjalankan bot
├── requirements.txt      # Dependensi Python
└── README.md             # Panduan lengkap ini
```

---

## 🚀 Panduan Instalasi & Penggunaan

### 1. Buat Bot Telegram & Dapatkan Token
1. Buka Telegram dan cari **`@BotFather`**.
2. Kirim perintah `/newbot` dan ikuti petunjuk hingga mendapatkan **BOT_TOKEN**.
3. Dapatkan Telegram User ID Anda dengan mengirim pesan ke **`@userinfobot`** di Telegram.

### 2. Konfigurasi `.env`
Salin file `.env.example` menjadi `.env`:
```powershell
copy .env.example .env
```
Buka file `.env` menggunakan teks editor (Notepad, VSCode), lalu isi data Anda:
```env
BOT_TOKEN=7123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789
OTP_PRICE=200
MIN_TOPUP=5000

PAYMENT_DANA=0812-3456-7890 (a.n Nama Anda)
PAYMENT_GOPAY=0812-3456-7890 (a.n Nama Anda)
PAYMENT_OVO=0812-3456-7890 (a.n Nama Anda)
PAYMENT_BANK=BCA: 1234567890 a.n Nama Anda
PAYMENT_QRIS_URL=https://link-gambar-qris-anda.com/qris.jpg

SMAILPRO_HEADLESS=true
```

### 3. Login Akun SmailPro Premium (Dilakukan 1x Saja)
Agar bot dapat memakai akun Smailpro Premium Anda tanpa perlu memasukkan password tiap saat:
1. Klik ganda file **`login_smailpro.bat`** (atau jalankan `python session_login.py` di terminal).
2. Sebuah jendela browser Chromium akan terbuka mengarah ke halaman login Smailpro / Sonjj.
3. Silakan ketik email & kata sandi akun SmailPro Premium Anda, lalu login.
4. Setelah masuk dan halaman berganti ke `https://smailpro.com/temporary-email` dengan status Premium aktif (ikon petir ⚡), kembali ke terminal dan tekan **ENTER**.
5. Selesai! Sesi login Anda tersimpan di folder `data/` dan bot siap berjalan di background tanpa membuka jendela browser.

### 4. Menjalankan Bot
Cukup klik ganda file **`start_bot.bat`** atau jalankan:
```powershell
python bot.py
```
Bot akan aktif dan langsung melayani user di Telegram!

---

## 📱 Alur Pemakaian Pengguna

1. Pengguna membuka bot dan menekan `/start`.
2. Pengguna menekan **`💰 Top Up Saldo`** jika saldo baru/kurang dari Rp 200.
3. Pengguna mentransfer minimal Rp 5.000 dan mengirim foto bukti ke bot.
4. Admin menyetujui bukti transfer dengan 1 kali klik `[✅ Terima]`.
5. Pengguna menekan **`📧 Pesan OTP Gmail`** atau **`📬 Pesan OTP Outlook`**.
6. Bot memberikan alamat email sementara:
   ```
   ✨ EMAIL GMAIL BERHASIL DIBUAT! ✨
   📧 Email: random.user123@gmail.com
   💰 Biaya: Rp 200 (Hanya dipotong saat OTP masuk)
   ```
7. Pengguna memasukkan email tersebut ke aplikasi tujuan (misal: WhatsApp, TikTok, Shopee, Netflix, Google, dll).
8. Pengguna mengklik **`[📩 Cek Inbox / OTP]`** atau mengaktifkan **`[🔄 Auto Refresh]`**.
9. Saat email verifikasi masuk, bot mengirimkan kode OTP secara instan dan memotong saldo Rp 200:
   ```
   🎉 INBOX / KODE OTP BERHASIL DITERIMA! 🎉
   📧 Email: random.user123@gmail.com
   📬 Dari: TikTok Verification
   📌 Subjek: 582910 is your verification code

   🔑 KODE OTP: 👉 582910 👈

   💰 Biaya: Rp 200 (Terpotong)
   💳 Sisa Saldo Anda: Rp 4.800
   ```

---

## 🛠️ Perintah Khusus Admin
- `/admin` : Menampilkan panel statistik omset, total user, total order, dan status sesi SmailPro.
- `/tambahsaldo <user_id> <jumlah>` : Menambahkan saldo manual ke pengguna.
- `/kurangsaldo <user_id> <jumlah>` : Mengurangi saldo manual pengguna.
- `/broadcast <pesan>` : Mengirimkan pesan pengumuman ke seluruh pengguna bot secara serentak.
