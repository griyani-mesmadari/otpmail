import asyncio
import json
import sys
from playwright.async_api import async_playwright
import config

async def run_login_helper():
    print("=" * 65)
    print("      🔐 SMAILPRO PREMIUM LOGIN HELPER (ONE-TIME SETUP)")
    print("=" * 65)
    print("Sedang membuka browser Chromium...")
    print(f"Direktori Profile: {config.BROWSER_PROFILE_DIR}")
    
    playwright = await async_playwright().start()
    
    # Launch browser headed so user can manually sign in
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(config.BROWSER_PROFILE_DIR),
        headless=False,
        viewport={"width": 1280, "height": 800},
        args=["--disable-blink-features=AutomationControlled"]
    )
    
    page = context.pages[0] if context.pages else await context.new_page()
    
    login_url = "https://my.sonjj.com/login?back=https://smailpro.com/temporary-email"
    print(f"\nMengakses halaman login: {login_url}")
    await page.goto(login_url)
    
    print("\n" + "#" * 65)
    print("PETUNJUK:")
    print("1. Silakan login ke akun SmailPro / Sonjj Premium Anda di jendela browser.")
    print("2. Masukkan email dan password, lalu klik tombol Login / Masuk.")
    print("3. Tunggu hingga browser dialihkan ke https://smailpro.com/temporary-email")
    print("4. Pastikan status akun di smailpro sudah menunjukkan status Premium.")
    print("#" * 65)
    
    print("\nTekan tombol ENTER di terminal ini SETELAH Anda berhasil login...")
    # Use loop with timeout or standard input
    await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
    
    print("\nMemeriksa status sesi...")
    
    # Check page status
    status = await page.evaluate('''() => {
        const app = document.querySelector('[x-data]');
        if (app && app._x_dataStack) {
            for (const d of app._x_dataStack) {
                if (typeof d.isLoggedIn !== 'undefined') {
                    return {
                        isLoggedIn: d.isLoggedIn,
                        isPremium: d.isPremium,
                        email: d.selectedEmail ? d.selectedEmail.address : null
                    };
                }
            }
        }
        return { isLoggedIn: false, isPremium: false };
    }''')
    
    # Save cookies to session_state file
    cookies = await context.cookies()
    session_data = {
        "cookies": cookies,
        "status": status
    }
    
    with open(config.SESSION_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2)
        
    print(f"\n✅ Sesi berhasil disimpan ke {config.SESSION_STATE_FILE}!")
    print(f"📊 Status Terdeteksi: LoggedIn={status.get('isLoggedIn')}, Premium={status.get('isPremium')}")
    
    if not status.get("isPremium"):
        print("\n⚠️ PERINGATAN: Akun belum terdeteksi sebagai Premium.")
        print("Pastikan Anda login dengan akun yang memiliki paket Premium aktif di Smailpro.")
    else:
        print("\n🎉 SELAMAT! Akun Premium SmailPro siap digunakan oleh Telegram Bot.")
        
    print("\nMenutup browser dalam 3 detik...")
    await asyncio.sleep(3)
    await context.close()
    await playwright.stop()
    print("Selesai.")

if __name__ == "__main__":
    asyncio.run(run_login_helper())
