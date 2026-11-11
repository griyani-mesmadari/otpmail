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
    print("      🔐 SMAILPRO PREMIUM LOGIN HELPER")
    print("=" * 65)
    print("Membuka browser Chromium di layar Anda...")
    print(f"Direktori Profil: {config.BROWSER_PROFILE_DIR}\n")
    
    playwright = await async_playwright().start()
    
    # Launch persistent context headed
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
    
    # Otomatis centang Remember Me agar cookies tahan bertahun-tahun
    try:
        await page.wait_for_selector("#remember_me", timeout=6000)
        if not await page.is_checked("#remember_me"):
            await page.check("#remember_me")
            print("✅ Opsi 'Remember me' otomatis dicentang agar sesi login awet jangka panjang!")
    except Exception:
        pass

    print("\n" + "#" * 65)
    print("👉 PETUNJUK:")
    print("1. Jendela browser Chromium sudah terbuka.")
    print("2. Silakan ketik Email & Password akun Smailpro Premium Anda lalu Login.")
    print("3. Tunggu hingga halaman dialihkan ke https://smailpro.com/temporary-email.")
    print("4. Setelah berhasil masuk ke dashboard Smailpro:")
    print("   👉 Tekan tombol ENTER di jendela CMD / terminal ini untuk menyimpan sesi!")
    print("#" * 65 + "\n")
    
    # Task 1: Auto-detection in background
    async def auto_detect():
        while True:
            await asyncio.sleep(2)
            try:
                curr = page.url
                if "smailpro.com" in curr and "/login" not in curr:
                    status = await page.evaluate('''() => {
                        const app = document.querySelector('[x-data]');
                        if (app && app._x_dataStack) {
                            for (const d of app._x_dataStack) {
                                if (typeof d.isLoggedIn !== 'undefined') {
                                    return {
                                        isLoggedIn: !!d.isLoggedIn,
                                        isPremium: !!d.isPremium
                                    };
                                }
                            }
                        }
                        return { isLoggedIn: false, isPremium: false };
                    }''')
                    if status.get("isLoggedIn") or status.get("isPremium"):
                        print("\n🎉 Auto-detect: Status login Premium terdeteksi aktif!")
                        return status
            except Exception:
                pass

    # Task 2: Wait for user press ENTER
    async def wait_enter():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, sys.stdin.readline)
        print("\nTombol ENTER ditekan oleh pengguna!")
        return True

    detect_task = asyncio.create_task(auto_detect())
    enter_task = asyncio.create_task(wait_enter())

    # Wait for either auto-detect or user pressing Enter
    done, pending = await asyncio.wait(
        [detect_task, enter_task],
        return_when=asyncio.FIRST_COMPLETED
    )
    for p in pending:
        p.cancel()

    # Re-check status on page
    status = {}
    try:
        if not page.is_closed():
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
    except Exception:
        pass

    # Save cookies & session
    cookies = await context.cookies()
    session_data = {
        "cookies": cookies,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "status": status
    }
    
    with open(config.SESSION_STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(session_data, f, indent=2)
        
    print("\n" + "=" * 65)
    print(f"✅ SESI SMAILPRO BERHASIL DISIMPAN KE: {config.SESSION_STATE_FILE}")
    print(f"📊 Total Cookies Tersimpan: {len(cookies)}")
    print(f"🔑 Status Akun: LoggedIn={status.get('isLoggedIn')}, Premium={status.get('isPremium')}")
    print("=" * 65)
    
    print("\nMenutup browser dalam 3 detik...")
    await asyncio.sleep(3)
    try:
        await context.close()
        await playwright.stop()
    except Exception:
        pass
    print("✅ Selesai! Bot sekarang siap digunakan.\n")

if __name__ == "__main__":
    asyncio.run(run_login_helper())
