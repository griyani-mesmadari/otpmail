import base64
import json
import sys
import config

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

def export_session():
    print("=" * 65)
    print("      📦 EXPORT SESSION SMAILPRO UNTUK RENDER / CLOUD")
    print("=" * 65)

    if not config.SESSION_STATE_FILE.exists():
        print("\n❌ File sesi belum ditemukan!")
        print("Silakan jalankan 'login_smailpro.bat' atau 'python session_login.py' terlebih dahulu")
        print("untuk login ke akun Smailpro Premium Anda.")
        print("=" * 65)
        return

    try:
        with open(config.SESSION_STATE_FILE, "r", encoding="utf-8") as f:
            raw_text = f.read()
            data = json.loads(raw_text)

        b64_str = base64.b64encode(raw_text.encode("utf-8")).decode("utf-8")

        print("\n✅ File sesi berhasil dibaca!")
        print(f"📊 Jumlah Cookies: {len(data.get('cookies', []))}")
        print("\n" + "-" * 65)
        print("SALIN NILAI DI BAWAH INI UNTUK ENVIRONMENT VARIABLE DI RENDER:")
        print("-" * 65)
        print("Nama Variabel : SMAILPRO_SESSION_B64")
        print("Nilai (Value) :\n")
        print(b64_str)
        print("\n" + "-" * 65)
        print("💡 TIPS DEPLOY DI RENDER:")
        print("1. Di dashboard Render, masuk ke tab 'Environment'.")
        print("2. Tambahkan variable baru:")
        print("   Key   : SMAILPRO_SESSION_B64")
        print("   Value : (Paste kode base64 panjang di atas)")
        print("3. Dengan cara ini, Anda tidak perlu mengunggah file session rahasia ke GitHub!")
        print("=" * 65)

    except Exception as e:
        print(f"❌ Terjadi kesalahan: {e}")

if __name__ == "__main__":
    export_session()
