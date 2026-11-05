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

    async def create_email(self, service: str = "gmail", max_retries: int = 10) -> Dict[str, Any]:
        """
        Creates a new temporary email for Gmail (Server 2/3), Outlook (Server 2), or Hotmail (Server 2).
        Uses Real Account (Premium) and guarantees unique email that was never generated before.
        """
        import random
        from database import db

        async with self.lock:
            await self.ensure_page_ready()
            
            service_lower = service.lower().strip()
            if service_lower == "gmail":
                domain = "gmail.com"
                server_choice = random.choice(["2", "3"])  # Server 2 (1000) & Server 3 (756) = ~1700 stok
                email_type = "google"
            elif service_lower == "hotmail":
                domain = "hotmail.com"
                server_choice = "2"  # Server 2 = ~8700 stok
                email_type = "microsoft"
            else:  # outlook
                domain = "outlook.com"
                server_choice = "2"  # Server 2 = ~8700 stok
                email_type = "microsoft"

            account_type = "real"  # Real Account Premium

            for attempt in range(max_retries):
                if service_lower == "gmail" and attempt > 0:
                    server_choice = random.choice(["2", "3"])

                logger.info("Requesting fresh %s (%s, server-%s, %s) email from SmailPro (attempt %d/%d)...",
                            service.upper(), domain, server_choice, account_type, attempt + 1, max_retries)

                try:
                    result = await self.page.evaluate('''async (cfg) => {
                        const query = `?username=random&type=${cfg.type}&domain=${cfg.domain}&server=${cfg.server}`;
                        const url = `/app/create${query}`;
                        
                        const headers = { 'Content-Type': 'application/json' };
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
                    }''', {"domain": domain, "type": account_type, "server": server_choice, "emailType": email_type})

                    if result.get("success") and result.get("email"):
                        cand = result["email"]
                        if db.is_email_used(cand):
                            logger.warning("Duplicate detected: %s was already used in an earlier order. Regenerating...", cand)
                            await asyncio.sleep(0.5)
                            continue

                        logger.info("Successfully generated fresh real email: %s", cand)
                        return result
                    else:
                        logger.warning("Attempt %d returned: %s", attempt + 1, result)

                except Exception as e:
                    logger.warning("Internal fetch failed on attempt %d: %s", attempt + 1, e)

                await asyncio.sleep(0.5)

            return {"success": False, "error": f"Gagal mendapatkan email unik setelah {max_retries} percobaan"}

    async def check_inbox(self, email_address: str, key: str = "", timestamp: int = 0) -> Dict[str, Any]:
        """
        Polls inbox for the given email using SmailPro + Sonjj API pipeline.
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

            # Determine service route
            domain = email_address.split("@")[-1].lower() if "@" in email_address else ""
            if "gmail" in domain:
                service_path = "temp_gmail"
            elif "outlook" in domain or "hotmail" in domain:
                service_path = "temp_outlook"
            else:
                service_path = "temp_email"

            try:
                # Execute full inbox check inside browser context (carries all auth cookies & avoids CORS)
                inbox_result = await self.page.evaluate('''async (param) => {
                    // Step 1: Request payload from /app/inbox
                    const body = [{
                        address: param.address,
                        timestamp: param.timestamp || Math.floor(Date.now() / 1000),
                        key: param.key || ''
                    }];

                    const inboxResp = await fetch('/app/inbox', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(body)
                    });

                    if (!inboxResp.ok) {
                        return { success: false, error: 'HTTP ' + inboxResp.status };
                    }

                    const inboxData = await inboxResp.json();
                    if (!Array.isArray(inboxData) || inboxData.length === 0) {
                        return { success: true, messages: [] };
                    }

                    const payload = inboxData[0]?.payload;
                    if (!payload) {
                        return { success: true, messages: [] };
                    }

                    // Step 2: Fetch message list from api.sonjj.com
                    const sonjjInboxUrl = `https://api.sonjj.com/v1/${param.service}/inbox?payload=${encodeURIComponent(payload)}`;
                    const sonjjResp = await fetch(sonjjInboxUrl);
                    if (!sonjjResp.ok) {
                        return { success: false, error: 'Sonjj HTTP ' + sonjjResp.status };
                    }

                    const sonjjData = await sonjjResp.json();
                    const rawMessages = sonjjData?.messages || sonjjData?.data?.messages || [];

                    if (!Array.isArray(rawMessages) || rawMessages.length === 0) {
                        return { success: true, messages: [] };
                    }

                    // Step 3: Fetch body for the messages
                    const messagesWithBody = [];
                    for (const m of rawMessages) {
                        const mid = m.mid || m.id;
                        let bodyText = m.body || '';

                        if (mid && !bodyText) {
                            try {
                                const msgUrl = `https://api.sonjj.com/v1/${param.service}/message?payload=${encodeURIComponent(payload)}&mid=${encodeURIComponent(mid)}`;
                                const msgResp = await fetch(msgUrl);
                                if (msgResp.ok) {
                                    const msgData = await msgResp.json();
                                    bodyText = msgData?.body || msgData?.data?.body || '';
                                }
                            } catch (e) {
                                console.error('Error fetching body for mid:', mid, e);
                            }
                        }

                        messagesWithBody.push({
                            mid: mid,
                            sender: m.textFrom || m.from || m.sender || '',
                            subject: m.textSubject || m.subject || '',
                            date: m.textDate || m.date || '',
                            body: bodyText
                        });
                    }

                    return { success: true, messages: messagesWithBody };
                }''', {
                    "address": email_address,
                    "key": key,
                    "timestamp": timestamp,
                    "service": service_path
                })

                if not inbox_result.get("success"):
                    logger.warning("Inbox request failed: %s", inbox_result.get("error"))
                    return {"success": False, "has_messages": False, "messages": [], "error": inbox_result.get("error")}

                raw_messages = inbox_result.get("messages", [])
                if not raw_messages:
                    logger.info("No messages found for %s", email_address)
                    return {"success": True, "has_messages": False, "messages": []}

                logger.info("Found %d incoming message(s) for %s!", len(raw_messages), email_address)

                parsed_messages = []
                for msg in raw_messages:
                    sender = msg.get("sender") or "Tidak Diketahui"
                    subject = msg.get("subject") or "(Tanpa Subjek)"
                    raw_body = msg.get("body") or ""
                    clean_body = self.clean_html(raw_body) if raw_body else ""

                    # Extract OTP from subject + body
                    combined_text = f"{subject}\n{clean_body}"
                    otp_code = self.extract_otp(combined_text)

                    parsed_messages.append({
                        "mid": msg.get("mid"),
                        "sender": sender.strip(),
                        "subject": subject.strip(),
                        "body": clean_body[:500],
                        "otp_code": otp_code
                    })

                return {
                    "success": True,
                    "has_messages": len(parsed_messages) > 0,
                    "messages": parsed_messages
                }

            except Exception as e:
                logger.error("Error checking inbox: %s", e, exc_info=True)
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
