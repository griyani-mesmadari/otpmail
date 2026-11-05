import asyncio
import json
import logging
import re
import time
from typing import Optional, Dict, Any, List
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page
import config

logger = logging.getLogger(__name__)

class SmailProEngine:
    def __init__(self):
        self.playwright: Optional[Playwright] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self.lock = asyncio.Lock()
        self.is_initialized = False

    async def initialize(self):
        """Initializes the persistent browser context and opens SmailPro."""
        if self.is_initialized and self.page and not self.page.is_closed():
            return

        try:
            self.playwright = await async_playwright().start()
            
            # Persistent context keeps cookies and login state
            args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
            
            logger.info("Starting Playwright persistent context at %s (headless=%s)", 
                        config.BROWSER_PROFILE_DIR, config.SMAILPRO_HEADLESS)
            
            self.context = await self.playwright.chromium.launch_persistent_context(
                user_data_dir=str(config.BROWSER_PROFILE_DIR),
                headless=config.SMAILPRO_HEADLESS,
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                args=args
            )

            # Try to load session from environment variables or file
            loaded_session = None
            if config.SMAILPRO_SESSION_JSON:
                try:
                    loaded_session = json.loads(config.SMAILPRO_SESSION_JSON)
                    logger.info("Loaded SmailPro session from SMAILPRO_SESSION_JSON environment variable")
                except Exception as e:
                    logger.warning("Failed parsing SMAILPRO_SESSION_JSON: %s", e)
            elif config.SMAILPRO_SESSION_B64:
                try:
                    import base64
                    raw_decoded = base64.b64decode(config.SMAILPRO_SESSION_B64).decode("utf-8")
                    loaded_session = json.loads(raw_decoded)
                    logger.info("Loaded SmailPro session from SMAILPRO_SESSION_B64 environment variable")
                except Exception as e:
                    logger.warning("Failed parsing SMAILPRO_SESSION_B64: %s", e)
            elif config.SESSION_STATE_FILE.exists():
                try:
                    with open(config.SESSION_STATE_FILE, "r", encoding="utf-8") as f:
                        loaded_session = json.load(f)
                        logger.info("Loaded SmailPro session from %s", config.SESSION_STATE_FILE)
                except Exception as e:
                    logger.warning("Could not load session state file: %s", e)

            if loaded_session and "cookies" in loaded_session:
                await self.context.add_cookies(loaded_session["cookies"])
                logger.info("Successfully added %d session cookies to browser context", len(loaded_session["cookies"]))

            # Open SmailPro page
            pages = self.context.pages
            self.page = pages[0] if pages else await self.context.new_page()
            
            logger.info("Navigating to https://smailpro.com/temporary-email ...")
            await self.page.goto("https://smailpro.com/temporary-email", timeout=45000)
            await self.page.wait_for_timeout(3000)

            # Check login status
            status = await self.check_session_status()
            logger.info("SmailPro session status: %s", status)

            self.is_initialized = True
        except Exception as e:
            logger.error("Failed to initialize SmailProEngine: %s", e, exc_info=True)
            self.is_initialized = False
            raise

    async def ensure_page_ready(self):
        """Ensures the page is open and alive."""
        if not self.is_initialized or not self.page or self.page.is_closed():
            await self.initialize()
        else:
            try:
                # Ping page
                await self.page.evaluate("() => document.title")
            except Exception:
                logger.warning("Page unresponsive, reinitializing...")
                await self.initialize()

    async def check_session_status(self) -> Dict[str, Any]:
        """Checks if current session on Smailpro is logged in and has premium."""
        if not self.page:
            return {"isLoggedIn": False, "isPremium": False}

        try:
            status = await self.page.evaluate('''() => {
                const app = document.querySelector('[x-data]');
                if (app && app._x_dataStack) {
                    for (const d of app._x_dataStack) {
                        if (typeof d.isLoggedIn !== 'undefined') {
                            return {
                                isLoggedIn: !!d.isLoggedIn,
                                isPremium: !!d.isPremium,
                                historyCount: d.historyCount || 0
                            };
                        }
                    }
                }
                return { isLoggedIn: false, isPremium: false, historyCount: 0 };
            }''')
            return status
        except Exception as e:
            logger.error("Error checking session status: %s", e)
            return {"isLoggedIn": False, "isPremium": False, "error": str(e)}

    async def create_email(self, service: str = "gmail") -> Dict[str, Any]:
        """
        Creates a new temporary email for Gmail or Outlook.
        service: 'gmail' or 'outlook'
        Returns dict: {"success": bool, "email": str, "key": str, "timestamp": int, "error": str}
        """
        async with self.lock:
            await self.ensure_page_ready()
            
            domain = "gmail.com" if service == "gmail" else "outlook.com"
            email_type = "google" if service == "gmail" else "microsoft"
            
            logger.info("Requesting new %s email from SmailPro...", service.upper())

            # Method 1: Execute internal API in browser context
            try:
                result = await self.page.evaluate('''async (cfg) => {
                    const query = `?username=random&type=alias&domain=${cfg.domain}&server=1`;
                    const url = `/app/create${query}`;
                    
                    const headers = { 'Content-Type': 'application/json' };
                    // If not logged in, captcha would be needed, but logged in accounts don't need captcha
                    const response = await fetch(url, {
                        method: 'GET',
                        headers: headers
                    });

                    if (!response.ok) {
                        const err = await response.json().catch(() => ({ msg: 'HTTP ' + response.status }));
                        return { success: false, error: err.msg || err.message || ('HTTP ' + response.status) };
                    }

                    const data = await response.json();
                    if (data && data.address) {
                        return {
                            success: true,
                            email: data.address,
                            key: data.key || '',
                            timestamp: data.timestamp || Math.floor(Date.now() / 1000)
                        };
                    }
                    return { success: false, error: data.msg || 'No email returned' };
                }''', {"domain": domain, "emailType": email_type})

                if result.get("success") and result.get("email"):
                    logger.info("Successfully generated email via API fetch: %s", result["email"])
                    return result
                else:
                    logger.warning("Fetch method returned: %s. Falling back to DOM clicks...", result)
            except Exception as e:
                logger.warning("Internal fetch failed: %s. Trying DOM automation fallback...", e)

            # Method 2: Fallback via DOM interaction
            try:
                # Select service button
                button_keyword = "Google" if service == "gmail" else "Microsoft"
                btn_clicked = await self.page.evaluate('''(keyword) => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const btn = buttons.find(b => b.innerText.includes(keyword));
                    if (btn) {
                        btn.click();
                        return true;
                    }
                    return false;
                }''', button_keyword)
                
                await self.page.wait_for_timeout(1000)

                # Click Generate
                await self.page.evaluate('''() => {
                    const buttons = Array.from(document.querySelectorAll('button'));
                    const btn = buttons.find(b => b.innerText.includes('Generate') || b.innerText.includes('Create'));
                    if (btn) btn.click();
                }''')
                
                # Wait for email update
                for _ in range(10):
                    await self.page.wait_for_timeout(1000)
                    active_email = await self.page.evaluate('''() => {
                        const app = document.querySelector('[x-data]');
                        if (app && app._x_dataStack) {
                            for (const d of app._x_dataStack) {
                                if (d.selectedEmail && d.selectedEmail.address) {
                                    return {
                                        email: d.selectedEmail.address,
                                        key: d.selectedEmail.key || '',
                                        timestamp: d.selectedEmail.timestamp || Math.floor(Date.now() / 1000)
                                    };
                                }
                            }
                        }
                        return null;
                    }''')
                    
                    if active_email and active_email.get("email"):
                        logger.info("Successfully generated email via DOM: %s", active_email["email"])
                        return {
                            "success": True,
                            "email": active_email["email"],
                            "key": active_email.get("key", ""),
                            "timestamp": active_email.get("timestamp", int(time.time()))
                        }

                return {"success": False, "error": "Timeout waiting for email generation"}
            except Exception as e:
                logger.error("DOM fallback failed: %s", e)
                return {"success": False, "error": str(e)}

    async def check_inbox(self, email_address: str, key: str = "", timestamp: int = 0) -> Dict[str, Any]:
        """
        Polls inbox for the given email.
        Returns:
        {
            "success": bool,
            "has_messages": bool,
            "messages": [
                {
                    "mid": str,
                    "sender": str,
                    "subject": str,
                    "body": str,
                    "otp_code": str (if extracted)
                }
            ],
            "error": str
        }
        """
        async with self.lock:
            await self.ensure_page_ready()
            
            logger.info("Checking inbox for %s...", email_address)

            try:
                # 1. Fetch inbox list via page evaluate
                inbox_result = await self.page.evaluate('''async (param) => {
                    const body = [{
                        address: param.address,
                        timestamp: param.timestamp || Math.floor(Date.now() / 1000),
                        key: param.key || ''
                    }];

                    const response = await fetch('/app/inbox', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body)
                    });

                    if (!response.ok) {
                        return { success: false, error: 'HTTP ' + response.status };
                    }

                    const data = await response.json();
                    return { success: true, data: data };
                }''', {"address": email_address, "key": key, "timestamp": timestamp})

                if not inbox_result.get("success"):
                    logger.warning("Inbox request failed: %s", inbox_result.get("error"))
                    return {"success": False, "has_messages": False, "messages": [], "error": inbox_result.get("error")}

                data = inbox_result.get("data", [])
                messages = []
                
                # Smailpro returns array of email objects or messages
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("messages"):
                            messages.extend(item.get("messages"))
                        elif isinstance(item, dict) and item.get("mid"):
                            messages.append(item)

                if not messages:
                    return {"success": True, "has_messages": False, "messages": []}

                logger.info("Found %d incoming message(s) for %s!", len(messages), email_address)

                parsed_messages = []
                for msg in messages:
                    mid = msg.get("mid")
                    sender = msg.get("from") or msg.get("sender") or "Tidak diketahui"
                    subject = msg.get("subject") or "(Tanpa Subjek)"
                    
                    # Fetch message content
                    body_content = ""
                    if mid:
                        try:
                            body_content = await self.page.evaluate('''async (p) => {
                                const response = await fetch(`/app/message?email=${encodeURIComponent(p.email)}&mid=${encodeURIComponent(p.mid)}`, {
                                    method: 'GET',
                                    headers: { 'Content-Type': 'application/json' }
                                });
                                if (response.ok) {
                                    return await response.text();
                                }
                                return '';
                            }''', {"email": email_address, "mid": mid})
                        except Exception as e:
                            logger.warning("Could not fetch body for message %s: %s", mid, e)
                            body_content = msg.get("snippet") or msg.get("text") or ""

                    # Extract OTP
                    combined_text = f"{subject}\n{body_content}"
                    otp_code = self.extract_otp(combined_text)

                    # Clean html snippet if any
                    clean_body = self.clean_html(body_content) if body_content else ""

                    parsed_messages.append({
                        "mid": mid,
                        "sender": sender,
                        "subject": subject,
                        "body": clean_body[:500],  # snippet
                        "otp_code": otp_code
                    })

                return {
                    "success": True,
                    "has_messages": len(parsed_messages) > 0,
                    "messages": parsed_messages
                }

            except Exception as e:
                logger.error("Error checking inbox: %s", e)
                return {"success": False, "has_messages": False, "messages": [], "error": str(e)}

    @staticmethod
    def extract_otp(text: str) -> Optional[str]:
        """
        Extracts 4-8 digit OTP verification codes from email text using regex heuristics.
        """
        if not text:
            return None

        # Pattern 1: Keywords like 'code is 123456', 'kode verifikasi: 123456', 'OTP: 1234'
        kw_patterns = [
            r'(?i)(?:otp|code|kode|pin|verification|verifikasi|security code|kata sandi)\s*(?:is|adalah|:|;|-|=|\s)\s*([0-9]{4,8})\b',
            r'(?i)(?:verification|verifikasi|confirm|konfirmasi)\s*code\s*[:\s]*([0-9]{4,8})\b',
            r'\b([0-9]{3}[-\s][0-9]{3})\b',  # e.g., 123-456 or 123 456
        ]

        for p in kw_patterns:
            m = re.search(p, text)
            if m:
                code = m.group(1).replace("-", "").replace(" ", "").strip()
                if 4 <= len(code) <= 8 and code.isdigit():
                    return code

        # Pattern 2: Standalone 6-digit or 4-digit number surrounded by whitespace or markdown
        six_digit = re.findall(r'(?<!\d)([0-9]{6})(?!\d)', text)
        if six_digit:
            # Filter out common years like 2024, 2025, 2026
            for c in six_digit:
                if not (c.startswith("202") and len(c) == 4):
                    return c

        four_digit = re.findall(r'(?<!\d)([0-9]{4})(?!\d)', text)
        if four_digit:
            for c in four_digit:
                if c not in ("2024", "2025", "2026", "2027"):
                    return c

        return None

    @staticmethod
    def clean_html(raw_html: str) -> str:
        """Removes html tags and decodes simple text."""
        cleanr = re.compile(r'<.*?>|&([a-z0-9]+|#[0-9]{1,6}|#x[0-9a-f]{1,6});')
        cleantext = re.sub(cleanr, ' ', raw_html)
        return " ".join(cleantext.split())

    async def close(self):
        """Clean shutdown of browser context."""
        try:
            if self.context:
                await self.context.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logger.error("Error closing browser: %s", e)

# Singleton SmailProEngine Instance
smailpro = SmailProEngine()
