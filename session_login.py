import asyncio
import json
import logging
import sys
import time
from playwright.async_api import async_playwright
import config

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

async def run_login_helper():
    print("\n" + "=" * 65)
    print("      🔐 SMAILPRO PREMIUM LOGIN HELPER (AUTO-DETECT)")
    print("=" * 65)
    print("Memulai browser Chromium...")
    print(f"Direktori Profil Browser: {config.BROWSER_PROFILE_DIR}\n")
    
    playwright = await async_playwright().start()
    
    # Launch browser headed on user screen
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(config.BROWSER_PROFILE_DIR),
        headless=False,
        viewport={"width": 1280, "height": 850},
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check"
        ]
    )
    
    page = context.pages[0] if context.pages else await context.new_page()
    
    login_url = "https://my.sonjj.com/login?back=https://smailpro.com/temporary-email"
    print(f"Membuka halaman login: {login_url}")
    await page.goto(login_url)
    
    print("\n" + "#" * 65)
    print("👉 PETUNJUK:")
    print("1. Jendela browser Chromium sudah terbuka di layar Anda.")
    print("2. Silakan login ke akun SmailPro / Sonjj Premium Anda.")
    print("3. Sistem akan otomatis mendeteksi ketika Anda sudah berhasil login.")
    print("   (Atau Anda dapat menekan ENTER di terminal ini jika sudah selesai).")
    print("#" * 65 + "\n")
    print("⏳ Menunggu Anda login di browser (Timeout: 10 menit)...")
    
    # Auto-detection loop
    max_seconds = 600
    start_time = time.time()
    logged_in = False
    status = {}
    
    while time.time() - start_time < max_seconds:
        await asyncio.sleep(2)
        try:
            current_url = page.url
            
            # Check smailpro state if on smailpro page
            if "smailpro.com" in current_url and "/login" not in current_url:
                status = await page.evaluate('''() => {
                    const app = document.querySelector('[x-data]');
                    if (app && app._x_dataStack) {
                        for (const d of app._x_dataStack) {
                            if (typeof d.isLoggedIn !== 'undefined') {
                                return {
                                    isLoggedIn: !!d.isLoggedIn,
                                    isPremium: !!d.isPremium,
                                    email: d.selectedEmail ? d.selectedEmail.address : null
                                };
                            }
                        }
                    }
                    return { isLoggedIn: false, isPremium: false };
                }''')
                
                # If logged in flag is true on smailpro.com
                if status.get("isLoggedIn") or status.get("isPremium"):
                    print(f"\n🎉 LOGIN SMAILPRO BERHASIL TERDETEKSI!")
                    print(f"URL Saat ini : {current_url}")
                    print(f"Status Akun  : LoggedIn={status.get('isLoggedIn')}, Premium={status.get('isPremium')}")
                    logged_in = True
                    break

            # If user logged in on my.sonjj.com and redirected to dashboard or home
            elif "my.sonjj.com" in current_url and "/login" not in current_url:
                print(f"\n🎉 LOGIN DI SONJJ BERHASIL!")
                print(f"URL: {current_url}")
                # Navigate to temporary email to finalize session
                print("Mengarahkan browser ke https://smailpro.com/temporary-email ...")
                await page.goto("https://smailpro.com/temporary-email")
                await asyncio.sleep(4)
                logged_in = True
                break
                
        except Exception as e:
            pass

    # Save session cookies and storage
    cookies = await context.cookies()
    session_data = {
        "cookies": cookies,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status
    }
    
    with open(config.SESSION_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2)
        
    print("\n" + "=" * 65)
    print(f"✅ SESI BERHASIL DISIMPAN KE: {config.SESSION_STATE_FILE}")
    print(f"📊 Total Cookies Tersimpan: {len(cookies)}")
    print(f"💾 Browser Profile: {config.BROWSER_PROFILE_DIR}")
    print("=" * 65)
    
    print("\nMenutup browser dalam 5 detik...")
    await asyncio.sleep(5)
    await context.close()
    await playwright.stop()
    print("✅ Selesai! Bot sekarang siap digunakan dengan akun Anda.\n")

if __name__ == "__main__":
    asyncio.run(run_login_helper())
