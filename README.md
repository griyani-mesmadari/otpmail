# 🤖 Bot Telegram OTP Email (Gmail & Outlook) — SmailPro Premium

Bot Telegram otomatis untuk penjualan dan penyewaan email sementara (**Gmail & Outlook**) dari **SmailPro Premium**, dilengkapi sistem saldo, approval top up admin, auto-polling inbox, ekstraksi kode OTP otomatis, dan **siap di-deploy 24 jam gratis di Render / GitHub via Docker**.

---

## 🌟 Fitur Utama

1. **📧 Pesan OTP Gmail & 📬 Pesan OTP Outlook:**
   - Otomatis membuka `https://smailpro.com/temporary-email` menggunakan sesi akun Premium Anda.
   - Menghasilkan 1 alamat email Gmail atau Outlook baru secara instan.
   - Alamat email diformat monospace agar pembeli bisa menyalin dengan 1 klik (*1-tap copy*).
2. **💰 Sistem Saldo & Pemotongan Adil (Rp 200 / OTP):**
   - Saldo pembeli **HANYA DIPOTONG Rp 200 JIKA KODE OTP / INBOX SUDAH MASUK**.
   - Jika belum ada email masuk, saldo tidak akan berkurang sepeserpun.
   - Minimal order adalah Rp 200, dan minimal top up adalah Rp 5.000.
3. **📩 Request Inbox & Ekstraksi OTP Otomatis:**
   - Tombol **`[📩 Cek Inbox / OTP]`** untuk cek manual.
   - Tombol **`[🔄 Auto Refresh (3 Menit)]`** yang otomatis memantau inbox setiap 5 detik tanpa perlu klik berulang kali.
   - Regex otomatis mem-parsing pengirim, subjek, dan mengisolasi **kode OTP (4 - 8 digit)**.
4. **💳 Top Up Saldo & Approval Admin 1-Klik:**
   - Pilihan nominal cepat (Rp 5.000, 10.000, 20.000, 50.000) atau nominal kustom.
   - Pengguna transfer dan mengirim foto bukti ke bot.
   - Admin mendapat notifikasi instan dengan tombol `[✅ Terima (Rp X)]` dan `[❌ Tolak]`. Saldo user otomatis masuk begitu disetujui.
5. **⚙️ Panel Admin Lengkap:**
   - Statistik bot (`/admin`): total user, total order, total omset, saldo mengendap, dan status akun Smailpro.
   - Perintah admin: `/tambahsaldo`, `/kurangsaldo`, `/broadcast <pesan>`.
6. **☁️ Siap Deploy 24 Jam Gratis di Render:**
   - Dilengkapi `Dockerfile` resmi Playwright Chromium (semua library Linux & Chromium sudah terpasang otomatis).
   - Built-in HTTP Healthcheck endpoint di port `$PORT` agar Render mengenali layanan Web Service.
   - Tool `export_session.py` untuk memindahkan sesi login Premium ke Render via Environment Variable tanpa membocorkan file rahasia ke GitHub.

---

## 📁 Struktur File Proyek

```
BOT OTP EMAIL/
├── Dockerfile            # Konfigurasi container untuk deploy ke Render (Playwright Pre-installed)
├── render.yaml           # Blueprint otomatis untuk Render
├── .gitignore            # Melindungi file .env dan data lokal agar tidak bocor ke GitHub
├── .env.example          # Template environment variable
├── config.py             # Loader konfigurasi
├── database.py           # Layer SQLite database (User, Order, Topup, Transaksi)
├── smailpro_engine.py    # Mesin Playwright otomatisasi SmailPro Premium
├── session_login.py      # Script login 1-kali ke akun Smailpro Premium (Lokal)
├── export_session.py     # Script ekspor session ke Base64 untuk disalin ke Render
├── bot.py                # Kode utama Telegram Bot + HTTP Health Server
├── login_smailpro.bat    # Pintasan Windows untuk login Smailpro
├── start_bot.bat         # Pintasan Windows untuk menjalankan bot lokal
├── requirements.txt      # Dependensi Python
└── README.md             # Panduan lengkap ini
```

---

## 🚀 PANDUAN DEPLOY KE GITHUB & RENDER (24 JAM GRATIS)

### Langkah 1: Login Akun SmailPro di Laptop/PC (1x Saja)
1. Klik ganda file **`login_smailpro.bat`** (atau jalankan `python session_login.py`).
2. Jendela browser akan terbuka. Silakan login ke akun SmailPro / Sonjj Premium Anda.
3. Setelah masuk ke `https://smailpro.com/temporary-email` dan status Premium aktif (ikon petir ⚡), kembali ke terminal dan tekan **ENTER**.
4. Sesi Anda sekarang sudah tersimpan di folder `data/`.

---

### Langkah 2: Ambil Kode Sesi untuk Render
Jalankan script ekspor sesi di terminal:
```powershell
python export_session.py
```
Script akan menampilkan kode panjang **`SMAILPRO_SESSION_B64`**.
*Biarkan terminal terbuka atau simpan kode ini di Notepad, karena akan dimasukkan ke Render nanti.*

---

### Langkah 3: Upload Project ke GitHub

1. Buka [GitHub.com](https://github.com) dan buat repository baru (misal: `bot-otp-email`). Pilih **Private** atau **Public** (Aman karena `.env` dan `data/` sudah otomatis diabaikan oleh `.gitignore`).
2. Jalankan perintah berikut di terminal folder ini:
   ```powershell
   git branch -M main
   git remote add origin https://github.com/USERNAME-ANDA/bot-otp-email.git
   git push -u origin main
   ```
*(Ganti `USERNAME-ANDA` dengan username GitHub Anda).*

---

### Langkah 4: Hubungkan ke Render.com (Gratis)

1. Buka [Render.com](https://render.com) dan login menggunakan akun GitHub Anda.
2. Di Dashboard Render, klik tombol **New +** ➡️ pilih **Web Service**.
3. Pilih repository GitHub Anda (`bot-otp-email`) dan klik **Connect**.
4. Isi pengaturan berikut:
   * **Name:** `telegram-otp-bot` (bebas)
   * **Region:** `Singapore` (terdekat dengan Indonesia & latensi tercepat)
   * **Language / Environment:** Pilih **Docker** *(Render akan otomatis mendeteksi file `Dockerfile`)*
   * **Instance Type:** Pilih **Free**
5. Gulir ke bawah ke bagian **Environment Variables**, klik **Add Environment Variable** dan masukkan:
   * `BOT_TOKEN` : *(Token bot dari @BotFather)*
   * `ADMIN_IDS` : *(ID Telegram Anda, misal: `12345678`)*
   * `OTP_PRICE` : `200`
   * `MIN_TOPUP` : `5000`
   * `SMAILPRO_HEADLESS` : `true`
   * `SMAILPRO_SESSION_B64` : *(Paste kode base64 panjang hasil Langkah 2)*
   * `PAYMENT_DANA` : `0812-3456-7890 (a.n Nama)`
   * `PAYMENT_GOPAY` : `0812-3456-7890 (a.n Nama)`
   * `PAYMENT_OVO` : `0812-3456-7890 (a.n Nama)`
   * `PAYMENT_BANK` : `BCA: 1234567890 a.n Nama`
6. Klik tombol **Create Web Service**.
7. Render akan otomatis mem-build Docker container dan menjalankan bot Anda!
   *Tunggu sekitar 2–3 menit sampai muncul log:*
   `🚀 BOT TELEGRAM OTP EMAIL AKTIF DAN SIAP DIGUNAKAN!`

---

### Langkah 5: Trik Agar Bot Tidak Tidur / Tetap Aktif 24 Jam Nonstop

Render Free Web Service akan masuk mode *sleep* setelah 15 menit jika tidak ada HTTP request masuk.
Karena bot ini sudah dilengkapi **Built-in HTTP Healthcheck** di alamat web service Render Anda (contoh: `https://telegram-otp-bot.onrender.com`), Anda dapat membuatnya aktif 24 jam gratis selamanya:

1. Salin URL web service Anda dari Dashboard Render (misal: `https://telegram-otp-bot.onrender.com`).
2. Buka layanan pemantau gratis seperti [UptimeRobot.com](https://uptimerobot.com) atau [cron-job.org](https://cron-job.org).
3. Buat **New Monitor**:
   * **Monitor Type:** `HTTP(s)`
   * **Friendly Name:** `Bot OTP`
   * **URL:** `https://telegram-otp-bot.onrender.com`
   * **Monitoring Interval:** Setiap `5 minutes`
4. Selesai! UptimeRobot akan mengirim ping setiap 5 menit ke bot Anda, mencegah Render dari mode sleep. Bot Anda sekarang **berjalan 24 jam nonstop 100% gratis**!

---

## 🛠️ Perintah Khusus Admin
- `/admin` : Menampilkan ringkasan statistik omset, order, dan status sesi SmailPro.
- `/tambahsaldo <user_id> <jumlah>` : Menambahkan saldo manual ke pengguna.
- `/kurangsaldo <user_id> <jumlah>` : Mengurangi saldo manual pengguna.
- `/broadcast <pesan>` : Mengirim pengumuman ke seluruh pengguna bot secara serentak.
