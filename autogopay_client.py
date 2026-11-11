import hashlib
import hmac
import logging
from typing import Any, Dict, Optional
import aiohttp
import config

logger = logging.getLogger(__name__)


class AutoGoPayClient:
    """Async client for AutoGoPay v1 Gateway API (GoPay Dynamic QRIS & Auto-Detection)."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None):
        self.base_url = (base_url or config.AUTOGOPAY_BASE_URL).rstrip("/")
        self.api_key = api_key if api_key is not None else config.AUTOGOPAY_API_KEY

    @property
    def is_configured(self) -> bool:
        return bool(self.base_url and self.api_key)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "OTPMailBot-AutoGoPay/1.0",
        }

    async def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if not self.is_configured:
            return {"success": False, "error": "AUTOGOPAY_API_KEY belum dikonfigurasi di .env"}

        url = f"{self.base_url}{path}"
        timeout = aiohttp.ClientTimeout(total=15)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.request(method, url, headers=self._headers(), json=payload) as resp:
                    try:
                        res_json = await resp.json(content_type=None)
                    except Exception:
                        text = await resp.text()
                        res_json = {"success": False, "message": f"HTTP {resp.status}: {text[:200]}"}

                    if resp.status >= 400 and not isinstance(res_json, dict):
                        return {"success": False, "error": f"HTTP {resp.status}", "http_status": resp.status}

                    if isinstance(res_json, dict):
                        res_json.setdefault("http_status", resp.status)
                        return res_json
                    return {"success": False, "error": "Invalid response format"}
        except Exception as e:
            logger.error("AutoGoPay request %s %s failed: %s", method, path, e)
            return {"success": False, "error": str(e)}

    async def generate_qris(self, amount: int) -> Dict[str, Any]:
        """
        Generates a dynamic GoPay QRIS via AutoGoPay (valid 15 minutes).
        Endpoint: POST /qris/generate
        """
        res = await self._request("POST", "/qris/generate", {"amount": int(amount)})
        if res.get("success") and isinstance(res.get("data"), dict):
            return {"success": True, "data": res["data"]}
        return {
            "success": False,
            "error": res.get("message") or res.get("error") or "Gagal membuat QRIS AutoGoPay",
        }

    async def check_status(self, transaction_id: str) -> Dict[str, Any]:
        """
        Checks payment status for a transaction_id.
        Endpoint: POST /qris/status
        Returns normalized dictionary with is_paid, is_pending, is_expired_or_cancelled, transaction_status.
        """
        res = await self._request("POST", "/qris/status", {"transaction_id": transaction_id})
        data = res.get("data") if isinstance(res.get("data"), dict) else {}
        tx_status = str(
            data.get("transaction_status") or data.get("status") or ""
        ).strip().lower()

        is_paid = tx_status in ("settlement", "capture", "paid", "success")
        is_pending = tx_status == "pending"
        is_terminal_fail = tx_status in ("expire", "expired", "cancel", "cancelled", "deny", "failure")

        return {
            "success": is_paid or is_pending or is_terminal_fail,
            "is_paid": is_paid,
            "is_pending": is_pending,
            "is_expired_or_cancelled": is_terminal_fail,
            "transaction_status": tx_status or "unknown",
            "message": res.get("message") or res.get("error") or "",
            "data": data,
            "raw": res,
        }

    async def cancel_qris(self, transaction_id: str) -> Dict[str, Any]:
        """
        Cancels a pending GoPay QRIS transaction.
        Endpoint: POST /qris/cancel
        """
        res = await self._request("POST", "/qris/cancel", {"transaction_id": transaction_id})
        if res.get("success"):
            return {"success": True, "data": res.get("data", {})}
        return {
            "success": False,
            "error": res.get("message") or res.get("error") or "Gagal membatalkan transaksi",
        }

    async def get_transactions(self) -> Dict[str, Any]:
        """
        Fetches latest GoPay Merchant settlement transactions.
        Endpoint: POST /transactions
        """
        res = await self._request("POST", "/transactions")
        if res.get("success") and isinstance(res.get("data"), dict):
            return {"success": True, "transactions": res["data"].get("transactions", [])}
        return {
            "success": False,
            "transactions": [],
            "error": res.get("message") or res.get("error") or "Gagal mengambil mutasi",
        }

    def verify_signature(self, raw_body: bytes, signature: str) -> bool:
        """Verifies AutoGoPay Webhook HMAC-SHA256 X-Signature header using API Key as secret."""
        if not self.api_key or not signature:
            return False
        expected = hmac.new(
            self.api_key.encode("utf-8"),
            raw_body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected.lower(), signature.strip().lower())


# Global instance
autogopay = AutoGoPayClient()
