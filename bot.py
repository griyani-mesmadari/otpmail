import asyncio
import io
import logging
import re
import sys
from datetime import datetime
from typing import Dict, Optional

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from telegram import (
    Update,
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

import config
from database import db
from smailpro_engine import smailpro
from qris_helper import generate_qris_image_bytes

# Logging Configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# State tracking for topup dialogs: {user_id: {"step": "waiting_amount" | "waiting_proof", "amount": int}}
user_topup_states: Dict[int, dict] = {}
# Background auto-poll tracking: {order_id: asyncio.Task}
active_polling_tasks: Dict[int, asyncio.Task] = {}
# Global bot instance for Webhook callbacks
telegram_bot: Optional[Bot] = None


def schedule_delete(message, delay: int = 10):
    """
    Schedules a Telegram message to be automatically deleted after `delay` seconds.
    Ensures transient chat messages are cleared every 10s, keeping only essential email cards.
    """
    if not message:
        return

    async def _do_delete():
        try:
            await asyncio.sleep(delay)
            await message.delete()
        except Exception:
            pass  # Message was already deleted by user or lacks permission

    asyncio.create_task(_do_delete())


def get_main_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    """Returns persistent main menu reply keyboard with expanded user features."""
    buttons = [
        [KeyboardButton("📧 Otp by Gmail ( 1700 stok )")],
        [KeyboardButton("📬 Otp by Outlook ( 8700 stok )"), KeyboardButton("📨 Otp by Hotmail ( 8700 stok )")],
        [KeyboardButton("💰 Top Up Saldo"), KeyboardButton("👤 Profil & Saldo")],
        [KeyboardButton("📊 Cek Stok Server"), KeyboardButton("📖 Riwayat Order")],
        [KeyboardButton("🏷️ Tarif & Info"), KeyboardButton("💬 Hubungi CS / Admin")]
    ]
    if is_admin:
        buttons.append([KeyboardButton("🧹 Bersihkan Chat"), KeyboardButton("⚙️ Panel Admin")])
    else:
        buttons.append([KeyboardButton("🧹 Bersihkan Chat")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


# ================= COMMAND: /start =================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        schedule_delete(update.message, delay=10)

    user = update.effective_user
    db_user = db.get_or_create_user(user.id, user.username, user.first_name)
    is_admin = user.id in config.ADMIN_IDS

    balance_display = "<b>Unlimited 👑 (Admin Bebas Biaya)</b>" if is_admin else f"<code>Rp {db_user['balance']:,}</code>"

    welcome_text = (
        f"👋 Halo, <b>{user.first_name}</b>!\n\n"
        f"Selamat datang di <b>Bot OTP Email Generator</b> 🤖\n\n"
        f"📌 <b>Layanan Email Premium (100% Real Account):</b>\n"
        f"• 📧 <b>Otp by Gmail</b> (1.700 Stok — Server 2 & 3 Premium)\n"
        f"• 📬 <b>Otp by Outlook</b> (8.700 Stok — Server 2 Premium)\n"
        f"• 📨 <b>Otp by Hotmail</b> (8.700 Stok — Server 2 Premium)\n\n"
        f"✨ <i>Semua email adalah <b>Real Account</b> dan dijamin <b>100% akan update terus stoknya berkala, gaskan !!</b></i>\n\n"
        f"💰 <b>Ketentuan Tarif & Saldo:</b>\n"
        f"• Biaya per inbox/OTP masuk: <b>Rp {config.OTP_PRICE:,}</b>\n"
        f"• Saldo Anda hanya dipotong <b>JIKA OTP BERHASIL MASUK</b>!\n"
        f"• Minimal Top Up: <b>Rp {config.MIN_TOPUP:,}</b>\n\n"
        f"💳 <b>Saldo Anda Saat Ini:</b> {balance_display}\n\n"
        f"Silakan pilih menu di bawah ini untuk memulai:"
    )

    welcome_msg = await update.message.reply_text(
        welcome_text,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(is_admin)
    )
    schedule_delete(welcome_msg, delay=20)


# ================= ORDER FLOW (GMAIL, OUTLOOK, HOTMAIL) =================
async def handle_order_request(update: Update, context: ContextTypes.DEFAULT_TYPE, service: str):
    """Handles ordering Gmail, Outlook, or Hotmail email."""
    if update.message:
        schedule_delete(update.message, delay=10)

    user = update.effective_user
    db_user = db.get_or_create_user(user.id, user.username, user.first_name)
    is_admin = user.id in config.ADMIN_IDS
    if service == "gmail":
        service_title = "Gmail (1.700 Stok — Server 2 & 3)"
    elif service == "hotmail":
        service_title = "Hotmail (8.700 Stok — Server 2)"
    else:
        service_title = "Outlook (8.700 Stok — Server 2)"

    # Check minimum balance (Bypassed for Admin - Unlimited Balance)
    if not is_admin and db_user["balance"] < config.OTP_PRICE:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("💰 Top Up Saldo Sekarang", callback_data="btn_topup")]
        ])
        warn_msg = await update.message.reply_text(
            f"❌ <b>Saldo Tidak Mencukupi!</b>\n\n"
            f"Saldo Anda saat ini: <code>Rp {db_user['balance']:,}</code>\n"
            f"Tarif per OTP: <code>Rp {config.OTP_PRICE:,}</code>\n\n"
            f"Silakan lakukan Top Up terlebih dahulu (Minimal Rp {config.MIN_TOPUP:,}).",
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
        schedule_delete(warn_msg, delay=10)
        return

    # Sending progress indicator
    loading_msg = await update.message.reply_text(
        f"⏳ <i>Sedang membuka Server Gmail, Outlook, Hotmail dan men-generate 1 alamat {service_title}... Mohon tunggu sebentar.</i>",
        parse_mode=ParseMode.HTML
    )

    try:
        # Request email from Smailpro Engine
        result = await smailpro.create_email(service)

        if not result.get("success") or not result.get("email"):
            error_msg = result.get("error", "Gagal menghubungi Server Gmail, Outlook, Hotmail")
            await loading_msg.edit_text(
                f"❌ <b>Gagal Generate Email!</b>\n\n"
                f"Detail: <code>{error_msg}</code>\n"
                f"Silakan coba beberapa saat lagi atau hubungi admin.",
                parse_mode=ParseMode.HTML
            )
            schedule_delete(loading_msg, delay=10)
            return

        email_address = result["email"]
        email_key = result.get("key", "")
        email_timestamp = result.get("timestamp", int(datetime.now().timestamp()))

        # Register order in DB (Admin has 0 cost)
        order_cost = 0 if is_admin else config.OTP_PRICE
        order_id = db.create_order(
            user_id=user.id,
            service=service,
            email_address=email_address,
            email_key=email_key,
            email_timestamp=email_timestamp,
            cost=order_cost
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📩 Cek Inbox / OTP", callback_data=f"check_inbox_{order_id}")],
            [InlineKeyboardButton("🔄 Auto Refresh (3 Menit)", callback_data=f"auto_poll_{order_id}")],
            [InlineKeyboardButton("❌ Batalkan Order", callback_data=f"cancel_order_{order_id}")]
        ])

        cost_info = "Gratis 👑 (Admin Unlimited)" if is_admin else f"Rp {config.OTP_PRICE:,} <i>(Hanya dipotong saat OTP masuk)</i>"

        order_text = (
            f"✨ <b>EMAIL {service.upper()} REAL ACCOUNT BERHASIL DIBUAT!</b> ✨\n\n"
            f"📧 <b>Email:</b> <code>{email_address}</code>\n"
            f"<i>(Klik alamat di atas untuk otomatis menyalin)</i>\n\n"
            f"🆔 <b>ID Order:</b> <code>#{order_id}</code>\n"
            f"🏷️ <b>Layanan:</b> {service_title}\n"
            f"🔒 <b>Tipe Akun:</b> Real Account (Bukan Alias)\n"
            f"🛡️ <b>Status:</b> 100% Baru & Unik (Anti-Duplikat)\n"
            f"💰 <b>Biaya:</b> {cost_info}\n"
            f"⏱️ <b>Masa Aktif:</b> 15 - 20 Menit\n\n"
            f"📌 <b>Petunjuk:</b>\n"
            f"1. Gunakan email di atas pada aplikasi / situs yang ingin Anda daftarkan.\n"
            f"2. Kirim kode OTP / verifikasi ke email tersebut.\n"
            f"3. Klik tombol <b>📩 Cek Inbox / OTP</b> di bawah untuk mengambil kodenya!"
        )

        # Main order card is KEPT in chat! Not scheduled for deletion.
        await loading_msg.edit_text(
            order_text,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )

    except Exception as e:
        logger.error("Error creating email order: %s", e, exc_info=True)
        await loading_msg.edit_text(
            f"❌ Terjadi kesalahan saat memproses order: <code>{str(e)}</code>",
            parse_mode=ParseMode.HTML
        )
        schedule_delete(loading_msg, delay=10)


# ================= INBOX CHECK HANDLER =================
async def check_inbox_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Memeriksa inbox Server Gmail, Outlook, Hotmail...", show_alert=False)

    data = query.data
    order_id = int(data.split("_")[-1])
    order = db.get_order(order_id)

    if not order:
        res = await query.message.reply_text("❌ Data order tidak ditemukan.")
        schedule_delete(res, delay=10)
        return

    if order["status"] == "completed":
        res = await query.message.reply_text(
            f"✅ <b>Order #{order_id} sudah selesai!</b>\n\n"
            f"📧 <b>Email:</b> <code>{order['email_address']}</code>\n"
            f"🔑 <b>Kode OTP:</b> <code>{order['otp_code']}</code>\n"
            f"📬 <b>Pengirim:</b> {order['otp_sender']}\n"
            f"📌 <b>Subjek:</b> {order['otp_subject']}",
            parse_mode=ParseMode.HTML
        )
        return

    if order["status"] == "cancelled":
        res = await query.message.reply_text("❌ Order ini sudah dibatalkan sebelumnya.")
        schedule_delete(res, delay=10)
        return

    user_id = order["user_id"]
    email_address = order["email_address"]
    email_key = order.get("email_key", "")
    email_ts = order.get("email_timestamp", 0)

    # Poll inbox from Smailpro Engine
    inbox_res = await smailpro.check_inbox(email_address, key=email_key, timestamp=email_ts)

    if not inbox_res.get("success"):
        res = await query.message.reply_text(
            f"⚠️ Gagal mengecek inbox: <code>{inbox_res.get('error')}</code>\nSilakan coba lagi.",
            parse_mode=ParseMode.HTML
        )
        schedule_delete(res, delay=10)
        return

    if not inbox_res.get("has_messages"):
        # No message yet: show clean status note and auto-delete after 10 seconds!
        status_msg = await query.message.reply_text(
            f"📭 <b>Belum ada pesan / OTP yang masuk.</b>\n\n"
            f"📧 Email: <code>{email_address}</code>\n"
            f"💡 <i>Saldo Anda BELUM dipotong sepeserpun. Pastikan Anda sudah mengirimkan OTP dari aplikasi target, lalu klik cek kembali.</i>\n\n"
            f"⏱️ <i>Pesan status ini otomatis terhapus dalam 10 detik agar chat tetap rapi.</i>",
            parse_mode=ParseMode.HTML
        )
        schedule_delete(status_msg, delay=10)
        return

    # Message(s) found! Process first message
    messages = inbox_res.get("messages", [])
    msg = messages[0]
    otp_code = msg.get("otp_code")
    sender = msg.get("sender") or "Tidak Diketahui"
    subject = msg.get("subject") or "Tanpa Subjek"
    body_snippet = msg.get("body") or ""

    # Deduct balance (Admin has free unlimited access)
    is_admin = user_id in config.ADMIN_IDS
    if is_admin:
        new_balance_text = "Unlimited 👑 (Admin)"
        cost_text = "Rp 0 (Gratis Admin)"
    else:
        try:
            new_balance = db.update_balance(
                user_id=user_id,
                amount=-order["cost"],
                tx_type="otp_deduct",
                description=f"OTP masuk #{order_id} ({email_address})"
            )
            new_balance_text = f"Rp {new_balance:,}"
        except ValueError:
            new_balance_text = "Rp 0"
        cost_text = f"Rp {order['cost']:,} (Terpotong)"

    # Mark completed in DB
    db.complete_order(order_id, otp_code=otp_code or "TERKIRIM", sender=sender, subject=subject)

    # Cancel any active auto polling task for this order
    if order_id in active_polling_tasks:
        active_polling_tasks[order_id].cancel()
        del active_polling_tasks[order_id]

    # Format result message
    otp_highlight = f"<code>{otp_code}</code>" if otp_code else "<i>(Lihat isi pesan di bawah)</i>"

    success_text = (
        f"🎉 <b>INBOX / KODE OTP BERHASIL DITERIMA!</b> 🎉\n\n"
        f"📧 <b>Email:</b> <code>{email_address}</code>\n"
        f"📬 <b>Dari:</b> <code>{sender}</code>\n"
        f"📌 <b>Subjek:</b> <b>{subject}</b>\n\n"
        f"🔑 <b>KODE OTP:</b> 👉 {otp_highlight} 👈\n"
        f"<i>(Klik kode di atas untuk otomatis menyalin)</i>\n\n"
        f"📄 <b>Cuplikan Pesan:</b>\n"
        f"<blockquote>{body_snippet[:350]}</blockquote>\n\n"
        f"💰 <b>Biaya:</b> <code>{cost_text}</code>\n"
        f"💳 <b>Sisa Saldo Anda:</b> <code>{new_balance_text}</code>"
    )

    await query.message.reply_text(
        success_text,
        parse_mode=ParseMode.HTML
    )

    # Update original order card markup
    completed_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🔑 OTP: {otp_code or 'Diterima'}", callback_data=f"noop_{order_id}")],
        [InlineKeyboardButton("📩 Cek Inbox Lagi", callback_data=f"check_inbox_{order_id}")],
        [InlineKeyboardButton("🗑️ Hapus Kartu Ini", callback_data=f"delete_card_{order_id}")]
    ])
    try:
        await query.message.edit_reply_markup(reply_markup=completed_keyboard)
    except Exception:
        pass


# ================= AUTO REFRESH INBOX TASK =================
async def auto_poll_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = int(query.data.split("_")[-1])

    order = db.get_order(order_id)
    if not order or order["status"] != "waiting":
        await query.answer("Order tidak aktif atau sudah selesai.", show_alert=True)
        return

    if order_id in active_polling_tasks and not active_polling_tasks[order_id].done():
        await query.answer("Auto refresh sudah berjalan untuk order ini!", show_alert=True)
        return

    await query.answer("Auto refresh dimulai! Bot akan memantau inbox selama 3 menit.", show_alert=False)
    status_msg = await query.message.reply_text(
        f"🔄 <b>Auto-Refresh Aktif!</b>\n"
        f"Bot akan mengecek inbox email <code>{order['email_address']}</code> setiap 5 detik secara otomatis...\n"
        f"<i>Notifikasi akan dikirim begitu OTP masuk. Pesan status ini terhapus dalam 10 detik.</i>",
        parse_mode=ParseMode.HTML
    )
    schedule_delete(status_msg, delay=10)

    # Launch background polling task
    task = asyncio.create_task(run_auto_poll(order_id, context))
    active_polling_tasks[order_id] = task


async def run_auto_poll(order_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Polls inbox every 5s for up to 3 minutes (36 checks)."""
    max_checks = 36  # 36 * 5s = 180s (3 minutes)
    for i in range(max_checks):
        await asyncio.sleep(5)
        order = db.get_order(order_id)
        if not order or order["status"] != "waiting":
            return

        try:
            inbox_res = await smailpro.check_inbox(
                order["email_address"],
                key=order.get("email_key", ""),
                timestamp=order.get("email_timestamp", 0)
            )

            if inbox_res.get("success") and inbox_res.get("has_messages"):
                messages = inbox_res.get("messages", [])
                msg = messages[0]
                otp_code = msg.get("otp_code")
                sender = msg.get("sender") or "Tidak Diketahui"
                subject = msg.get("subject") or "Tanpa Subjek"
                body_snippet = msg.get("body") or ""

                # Deduct balance (Admin has free unlimited access)
                user_id = order["user_id"]
                is_admin = user_id in config.ADMIN_IDS
                if is_admin:
                    new_balance_text = "Unlimited 👑 (Admin)"
                    cost_text = "Rp 0 (Gratis Admin)"
                else:
                    try:
                        new_balance = db.update_balance(
                            user_id=user_id,
                            amount=-order["cost"],
                            tx_type="otp_deduct",
                            description=f"Auto-poll OTP #{order_id} ({order['email_address']})"
                        )
                        new_balance_text = f"Rp {new_balance:,}"
                    except ValueError:
                        new_balance_text = "Rp 0"
                    cost_text = f"Rp {order['cost']:,} (Terpotong)"

                db.complete_order(order_id, otp_code=otp_code or "TERKIRIM", sender=sender, subject=subject)

                otp_highlight = f"<code>{otp_code}</code>" if otp_code else "<i>(Lihat pesan di bawah)</i>"
                success_text = (
                    f"🎉 <b>INBOX / KODE OTP BERHASIL DITERIMA!</b> 🎉\n\n"
                    f"📧 <b>Email:</b> <code>{order['email_address']}</code>\n"
                    f"📬 <b>Dari:</b> <code>{sender}</code>\n"
                    f"📌 <b>Subjek:</b> <b>{subject}</b>\n\n"
                    f"🔑 <b>KODE OTP:</b> 👉 {otp_highlight} 👈\n\n"
                    f"📄 <b>Cuplikan Pesan:</b>\n"
                    f"<blockquote>{body_snippet[:350]}</blockquote>\n\n"
                    f"💰 <b>Biaya:</b> <code>{cost_text}</code>\n"
                    f"💳 <b>Sisa Saldo Anda:</b> <code>{new_balance_text}</code>"
                )

                await context.bot.send_message(
                    chat_id=user_id,
                    text=success_text,
                    parse_mode=ParseMode.HTML
                )
                return

        except Exception as e:
            logger.warning("Auto poll error for order %s: %s", order_id, e)

    # If timed out after 3 minutes
    order = db.get_order(order_id)
    if order and order["status"] == "waiting":
        timeout_msg = await context.bot.send_message(
            chat_id=order["user_id"],
            text=(
                f"⏱️ <b>Waktu auto-refresh 3 menit telah selesai.</b>\n"
                f"Belum ada email yang masuk untuk <code>{order['email_address']}</code>.\n"
                f"Saldo Anda tetap utuh Rp 0 terpotong. Anda dapat menekan tombol <b>Cek Inbox / OTP</b> pada kartu email kapan saja saat OTP dikirim."
            ),
            parse_mode=ParseMode.HTML
        )
        schedule_delete(timeout_msg, delay=10)


# ================= CANCEL ORDER HANDLER =================
async def cancel_order_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    order_id = int(query.data.split("_")[-1])

    order = db.get_order(order_id)
    if not order:
        await query.answer("Order tidak ditemukan.", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
        return

    if order["status"] != "waiting":
        await query.answer("Order ini tidak dapat dibatalkan (sudah selesai/batal).", show_alert=True)
        return

    # Cancel order in DB
    db.cancel_order(order_id)
    if order_id in active_polling_tasks:
        active_polling_tasks[order_id].cancel()
        del active_polling_tasks[order_id]

    await query.answer(f"❌ Order #{order_id} ({order['email_address']}) dibatalkan. Saldo Anda utuh.", show_alert=True)

    # Delete the message card immediately so it disappears from the list
    try:
        await query.message.delete()
    except Exception:
        pass

    # Send temporary confirmation note that self-deletes in 10s
    try:
        user_id = query.from_user.id
        notice = await context.bot.send_message(
            chat_id=user_id,
            text=f"🗑️ <i>Order #{order_id} (<code>{order['email_address']}</code>) telah dibatalkan & dihapus dari list. Saldo Anda utuh.</i>",
            parse_mode=ParseMode.HTML
        )
        schedule_delete(notice, delay=10)
    except Exception:
        pass


async def delete_card_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Kartu email dihapus dari chat.", show_alert=False)
    try:
        await query.message.delete()
    except Exception:
        pass


async def noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer("Kode OTP sudah masuk. Silakan salin.", show_alert=False)


# ================= TOP UP SALDO FLOW =================
async def topup_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Displays Top Up options and payment methods."""
    if update.message:
        schedule_delete(update.message, delay=10)

    user = update.effective_user
    db_user = db.get_or_create_user(user.id, user.username, user.first_name)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Rp 5.000", callback_data="topup_amt_5000"),
            InlineKeyboardButton("Rp 10.000", callback_data="topup_amt_10000")
        ],
        [
            InlineKeyboardButton("Rp 20.000", callback_data="topup_amt_20000"),
            InlineKeyboardButton("Rp 50.000", callback_data="topup_amt_50000")
        ],
        [
            InlineKeyboardButton("✏️ Masukkan Nominal Kustom", callback_data="topup_amt_custom")
        ]
    ])

    topup_text = (
        f"💳 <b>TOP UP SALDO AKUN</b> 💳\n\n"
        f"Saldo Anda saat ini: <code>Rp {db_user['balance']:,}</code>\n"
        f"Minimal Top Up: <b>Rp {config.MIN_TOPUP:,}</b>\n\n"
        f"Pilih salah satu nominal top up cepat di bawah ini atau tentukan nominal kustom:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        msg = await update.callback_query.message.reply_text(topup_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    else:
        msg = await update.message.reply_text(topup_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    schedule_delete(msg, delay=60)


async def topup_amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user_id = query.from_user.id

    if data == "topup_amt_custom":
        user_topup_states[user_id] = {"step": "waiting_custom_amount"}
        custom_msg = await query.message.reply_text(
            f"✏️ <b>Ketik nominal saldo yang ingin Anda top up:</b>\n"
            f"<i>(Contoh: <code>15000</code> atau <code>25000</code>. Minimal Rp {config.MIN_TOPUP:,})</i>",
            parse_mode=ParseMode.HTML
        )
        schedule_delete(custom_msg, delay=30)
        return

    amount = int(data.replace("topup_amt_", ""))
    await show_payment_instructions(query.message, user_id, amount)


async def show_payment_instructions(message_target, user_id: int, base_amount: int):
    """Generates dynamic QRIS image with 3-digit unique code, registers pending topup, and displays payment details."""
    import random

    if base_amount < config.MIN_TOPUP:
        base_amount = config.MIN_TOPUP

    # Round base to thousands and generate 3-digit unique code (101 - 999)
    base_thousands = (base_amount // 1000) * 1000

    # Ensure unique_code does not collide with any currently pending topup amount
    pending_amounts = db.get_pending_topup_amounts()
    attempts = 0
    unique_code = random.randint(101, 999)
    final_amount = base_thousands + unique_code
    while final_amount in pending_amounts and attempts < 100:
        unique_code = random.randint(101, 999)
        final_amount = base_thousands + unique_code
        attempts += 1

    # Immediately register topup as pending in database for QRIS Hook automatic detection
    topup_id = db.create_topup(user_id=user_id, amount=final_amount, proof_file_id="QRIS_AUTO_WAITING")

    user_topup_states[user_id] = {
        "step": "waiting_proof",
        "topup_id": topup_id,
        "base_amount": base_thousands,
        "unique_code": unique_code,
        "amount": final_amount
    }

    caption = (
        f"💳 <b>PEMBAYARAN TOP UP VIA QRIS OTOMATIS</b>\n\n"
        f"💰 <b>Total Transfer:</b> <code>Rp {final_amount:,}</code>\n"
        f"🔢 <b>Nominal Pokok:</b> Rp {base_thousands:,}\n"
        f"🎯 <b>Kode Unik:</b> <code>+{unique_code}</code> <i>(Pembeda mutasi otomatis Anda)</i>\n"
        f"🏷️ <b>Merchant:</b> PT SERVER HANDAL, SUKOLIL\n\n"
        f"📱 <b>Cara Pembayaran:</b>\n"
        f"1. Buka aplikasi m-Banking (BCA, Mandiri, BRI, BNI, Jago, dll) atau E-Wallet (DANA, GoPay, OVO, ShopeePay).\n"
        f"2. Scan / upload gambar QR Code di atas.\n"
        f"3. Pastikan nominal tertera tepat <b>Rp {final_amount:,}</b> lalu selesaikan pembayaran.\n"
        f"4. <i>Selesai! Sistem akan otomatis mendeteksi mutasi dan saldo langsung masuk.</i>"
    )

    try:
        # Generate dynamic QRIS PNG in memory with final_amount
        qr_bytes = generate_qris_image_bytes(config.STATIC_QRIS, final_amount)
        photo_file = io.BytesIO(qr_bytes)
        photo_file.name = "qris.png"
        qr_msg = await message_target.reply_photo(
            photo=photo_file,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
        schedule_delete(qr_msg, delay=300)
    except Exception as e:
        logger.error("Failed to generate/send QRIS photo: %s", e)
        # Fallback to text if image generation encounters issue
        fallback_msg = await message_target.reply_text(caption, parse_mode=ParseMode.HTML)
        schedule_delete(fallback_msg, delay=300)


# ================= RECEIVE PROOF OF TRANSFER =================
async def handle_photo_proof(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id

    state = user_topup_states.get(user_id)
    if not state or state.get("step") != "waiting_proof":
        # Ignore random photos
        return

    amount = state.get("amount", config.MIN_TOPUP)
    unique_code = state.get("unique_code", 0)
    base_amount = state.get("base_amount", amount)
    topup_id = state.get("topup_id")
    photo = update.message.photo[-1]  # Highest resolution photo
    file_id = photo.file_id

    # Check if this topup was already auto-approved by QRIS Hook
    if topup_id:
        existing = db.get_topup(topup_id)
        if existing and existing.get("status") == "approved":
            del user_topup_states[user_id]
            await update.message.reply_text(
                f"🎉 <b>Pembayaran Anda sebesar Rp {amount:,} sudah otomatis masuk beberapa saat yang lalu!</b>\n\n"
                f"Saldo sudah aktif di akun Anda. Anda bisa langsung memesan email OTP.",
                parse_mode=ParseMode.HTML
            )
            return
        # Update existing record with photo proof
        db.update_topup_proof(topup_id, file_id)
    else:
        # Create topup record fallback
        topup_id = db.create_topup(user_id=user_id, amount=amount, proof_file_id=file_id)

    # Clear user state
    del user_topup_states[user_id]

    await update.message.reply_text(
        f"✅ <b>Bukti transfer berhasil diterima!</b>\n\n"
        f"🆔 ID Top Up: <code>#{topup_id}</code>\n"
        f"💰 Nominal Transfer: <code>Rp {amount:,}</code> (Kode Unik: {unique_code})\n\n"
        f"Permohonan top up Anda sedang ditinjau oleh Admin. Anda akan menerima notifikasi otomatis begitu saldo disetujui.",
        parse_mode=ParseMode.HTML
    )

    # Forward to Admins
    admin_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(f"✅ Terima (Rp {amount:,})", callback_data=f"adm_appr_{topup_id}"),
            InlineKeyboardButton("❌ Tolak", callback_data=f"adm_rejc_{topup_id}")
        ]
    ])

    admin_caption = (
        f"🔔 <b>PERMOHONAN TOP UP BARU!</b>\n\n"
        f"🆔 <b>ID Top Up:</b> <code>#{topup_id}</code>\n"
        f"👤 <b>Pengirim:</b> {user.first_name} (@{user.username or '-'}) [<code>{user_id}</code>]\n"
        f"💰 <b>Total Transfer:</b> <b>Rp {amount:,}</b>\n"
        f"🔢 <b>Kode Unik:</b> <code>{unique_code}</code> (Pokok: Rp {base_amount:,})\n"
        f"⏱️ <b>Waktu:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"👉 Cocokkan mutasi rekening Anda dengan nominal <b>Rp {amount:,}</b> lalu klik tombol di bawah:"
    )

    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=file_id,
                caption=admin_caption,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_keyboard
            )
        except Exception as e:
            logger.error("Failed to notify admin %s: %s", admin_id, e)


# ================= ADMIN APPROVE / REJECT TOPUP =================
async def admin_topup_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    admin_id = query.from_user.id

    if admin_id not in config.ADMIN_IDS:
        await query.answer("Hanya admin yang memiliki izin!", show_alert=True)
        return

    data = query.data

    if data == "adm_ignore_topup":
        await query.answer("Mutasi diabaikan.", show_alert=False)
        orig_text = query.message.text or query.message.caption or ""
        await query.edit_message_text(
            text=f"{orig_text}\n\n⚪ <b>DIABAIKAN (Bukan Top Up Bot)</b>",
            parse_mode=ParseMode.HTML
        )
        return

    if data.startswith("adm_assign_"):
        parts = data.split("_")
        amount = int(parts[2])
        target_user_id = int(parts[3])
        new_balance = db.update_balance(
            user_id=target_user_id,
            amount=amount,
            tx_type="topup",
            description=f"Top Up QRIS Hook (Manual Assign oleh Admin @{query.from_user.username or admin_id})"
        )
        await query.answer(f"Saldo Rp {amount:,} berhasil dimasukkan!", show_alert=True)
        orig_text = query.message.text or query.message.caption or ""
        await query.edit_message_text(
            text=f"{orig_text}\n\n✅ <b>BERHASIL DIMASUKKAN ke User {target_user_id} oleh Admin @{query.from_user.username or admin_id}!</b>\nSaldo baru: <code>Rp {new_balance:,}</code>",
            parse_mode=ParseMode.HTML
        )
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=target_user_id,
                text=(
                    f"🎉 <b>PEMBAYARAN QRIS BERHASIL DITERIMA!</b> 🎉\n\n"
                    f"✅ Saldo sebesar <b>Rp {amount:,}</b> telah ditambahkan ke akun Anda!\n"
                    f"💳 <b>Total Saldo Sekarang:</b> <b>Rp {new_balance:,}</b>\n\n"
                    f"Terima kasih atas pembayaran Anda! Sekarang Anda sudah dapat langsung memesan email OTP."
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error("Could not notify assigned user %s: %s", target_user_id, e)
        return

    action, topup_id_str = data.split("_")[1], data.split("_")[2]
    topup_id = int(topup_id_str)

    if action == "appr":
        # Approve
        res = db.approve_topup(topup_id, admin_id)
        if not res:
            await query.answer("Topup sudah pernah diproses sebelumnya.", show_alert=True)
            return

        await query.answer("Top up berhasil disetujui!", show_alert=False)
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n✅ <b>DISETUJUI oleh Admin @{query.from_user.username or admin_id}</b>",
            parse_mode=ParseMode.HTML
        )

        # Notify User
        try:
            await context.bot.send_message(
                chat_id=res["user_id"],
                text=(
                    f"🎉 <b>TOP UP BERHASIL DISETUJUI!</b> 🎉\n\n"
                    f"🆔 <b>ID Top Up:</b> <code>#{topup_id}</code>\n"
                    f"💰 <b>Nominal Ditambahkan:</b> <code>Rp {res['amount']:,}</code>\n"
                    f"💳 <b>Total Saldo Anda Sekarang:</b> <b>Rp {res['new_balance']:,}</b>\n\n"
                    f"Terima kasih! Anda sekarang dapat langsung memesan OTP Gmail atau Outlook."
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error("Could not notify user of topup approval: %s", e)

    elif action == "rejc":
        # Reject
        topup = db.reject_topup(topup_id, admin_id)
        if not topup:
            await query.answer("Topup sudah pernah diproses sebelumnya.", show_alert=True)
            return

        await query.answer("Top up ditolak.", show_alert=False)
        await query.edit_message_caption(
            caption=f"{query.message.caption}\n\n❌ <b>DITOLAK oleh Admin @{query.from_user.username or admin_id}</b>",
            parse_mode=ParseMode.HTML
        )

        # Notify User
        try:
            await context.bot.send_message(
                chat_id=topup["user_id"],
                text=(
                    f"❌ <b>Mohon Maaf, Top Up #{topup_id} Ditolak.</b>\n\n"
                    f"Bukti pembayaran Anda tidak valid atau transfer belum diterima.\n"
                    f"Jika ada pertanyaan, silakan hubungi customer service / admin."
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.error("Could not notify user of topup rejection: %s", e)


# ================= PROFILE & HISTORY =================
async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        schedule_delete(update.message, delay=10)

    user = update.effective_user
    db_user = db.get_or_create_user(user.id, user.username, user.first_name)
    is_admin = user.id in config.ADMIN_IDS
    active_order = db.get_active_order_by_user(user.id)

    active_info = "Tidak ada"
    if active_order:
        active_info = f"<code>{active_order['email_address']}</code> ({active_order['service'].upper()})"

    balance_display = "<b>Unlimited 👑 (Admin Bebas Biaya)</b>" if is_admin else f"<b>Rp {db_user['balance']:,}</b>"

    text = (
        f"👤 <b>PROFIL PENGGUNA</b>\n\n"
        f"🆔 <b>User ID:</b> <code>{user.id}</code>\n"
        f"👤 <b>Nama:</b> {user.first_name}\n"
        f"🏷️ <b>Username:</b> @{user.username or '-'}\n\n"
        f"💰 <b>Saldo Tersedia:</b> {balance_display}\n"
        f"💸 <b>Total Pemakaian:</b> Rp {db_user['total_spent']:,}\n"
        f"📬 <b>Order Aktif Terakhir:</b> {active_info}\n\n"
        f"📅 <b>Terdaftar Sejak:</b> {db_user['created_at']}\n\n"
        f"⏱️ <i>Pesan ini otomatis dihapus dalam 10 detik agar chat tetap rapi.</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Top Up Saldo", callback_data="btn_topup")],
        [InlineKeyboardButton("📖 Riwayat Order", callback_data="btn_history")]
    ])

    prof_msg = await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    schedule_delete(prof_msg, delay=10)


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        schedule_delete(update.message, delay=10)

    user = update.effective_user
    orders = db.get_user_orders(user.id, limit=8)

    if not orders:
        text = "📖 <b>Riwayat Order:</b>\n\nAnda belum memiliki riwayat order email/OTP.\n\n⏱️ <i>Pesan ini otomatis dihapus dalam 10 detik.</i>"
        if update.callback_query:
            await update.callback_query.answer()
            hist_msg = await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML)
        else:
            hist_msg = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        schedule_delete(hist_msg, delay=10)
        return

    lines = ["📖 <b>RIWAYAT 8 ORDER TERAKHIR:</b>\n"]
    for o in orders:
        status_icon = "⏳" if o["status"] == "waiting" else ("✅" if o["status"] == "completed" else "❌")
        otp_info = f"🔑 <code>{o['otp_code']}</code>" if o.get("otp_code") else f"<i>({o['status']})</i>"
        lines.append(
            f"{status_icon} <b>#{o['id']}</b> | <b>{o['service'].upper()}</b>\n"
            f"   📧 <code>{o['email_address']}</code>\n"
            f"   {otp_info} | {o['created_at'][:16]}\n"
        )
    lines.append("\n⏱️ <i>Pesan ini otomatis dihapus dalam 10 detik agar chat tetap rapi.</i>")

    full_text = "\n".join(lines)
    if update.callback_query:
        await update.callback_query.answer()
        hist_msg = await update.callback_query.message.reply_text(full_text, parse_mode=ParseMode.HTML)
    else:
        hist_msg = await update.message.reply_text(full_text, parse_mode=ParseMode.HTML)
    schedule_delete(hist_msg, delay=10)


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Clears recent chat history in the private chat by deleting messages.
    """
    chat_id = update.effective_chat.id
    curr_msg = update.effective_message
    curr_id = curr_msg.message_id if curr_msg else None

    # Status notice
    status_msg = await context.bot.send_message(
        chat_id=chat_id,
        text="🧹 <i>Sedang membersihkan riwayat chat...</i>",
        parse_mode=ParseMode.HTML
    )

    if curr_id:
        # Collect message IDs backwards (up to 100 messages)
        ids_to_delete = list(range(max(1, curr_id - 100), curr_id + 1))
        # Batch delete in chunks of 100
        for i in range(0, len(ids_to_delete), 100):
            chunk = ids_to_delete[i:i + 100]
            try:
                await context.bot.delete_messages(chat_id=chat_id, message_ids=chunk)
            except Exception:
                # Fallback: delete individually
                for mid in chunk:
                    try:
                        await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                    except Exception:
                        pass

    # Delete status notice
    try:
        await status_msg.delete()
    except Exception:
        pass

    user = update.effective_user
    is_admin = user.id in config.ADMIN_IDS if user else False

    # Send clean fresh menu
    clean_notice = (
        f"🧹 <b>Riwayat chat berhasil dibersihkan!</b>\n\n"
        f"Chat Anda sekarang bersih dan siap digunakan.\n"
        f"Silakan pilih menu di bawah atau ketik /start untuk mulai memesan:"
    )
    await context.bot.send_message(
        chat_id=chat_id,
        text=clean_notice,
        parse_mode=ParseMode.HTML,
        reply_markup=get_main_keyboard(is_admin)
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        schedule_delete(update.message, delay=10)

    text = (
        f"ℹ️ <b>PANDUAN & BANTUAN BOT OTP</b>\n\n"
        f"1. <b>Cara Pesan Email:</b>\n"
        f"   • Pastikan saldo Anda minimal Rp {config.OTP_PRICE:,}.\n"
        f"   • Klik menu <b>📧 Otp by Gmail</b>, <b>📬 Otp by Outlook</b>, atau <b>📨 Otp by Hotmail</b>.\n"
        f"   • Bot akan membuka Server Gmail, Outlook, Hotmail dan memberikan 1 alamat email Real Account baru.\n\n"
        f"2. <b>Cara Menerima OTP:</b>\n"
        f"   • Salin email yang diberikan dan gunakan untuk registrasi pada website/aplikasi tujuan.\n"
        f"   • Setelah OTP terkirim, kembali ke bot dan klik tombol <b>📩 Cek Inbox / OTP</b> pada kartu email tersebut.\n"
        f"   • Jika kode masuk, bot akan menampilkan kode OTP dan baru memotong saldo Anda sebesar Rp {config.OTP_PRICE:,}.\n\n"
        f"3. <b>Top Up Saldo:</b>\n"
        f"   • Klik menu <b>💰 Top Up Saldo</b> (Minimal Rp {config.MIN_TOPUP:,}).\n"
        f"   • Lakukan transfer dan kirim bukti pembayaran ke bot.\n\n"
        f"⏱️ <i>Pesan ini otomatis dihapus dalam 15 detik agar chat tetap rapi.</i>"
    )
    help_msg = await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    schedule_delete(help_msg, delay=15)


async def stok_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        schedule_delete(update.message, delay=10)

    text = (
        f"📊 <b>STATUS LIVE STOK EMAIL REAL ACCOUNT</b>\n\n"
        f"🟢 <b>Server Gmail:</b>\n"
        f"• Server-2 & Server-3 (Premium): <b>~1.756 Akun</b>\n"
        f"• Tipe: Random Real Account (@gmail.com)\n"
        f"• Status: 100% Siap Digunakan\n\n"
        f"🟢 <b>Server Outlook:</b>\n"
        f"• Server-2 (Premium): <b>~8.708 Akun</b>\n"
        f"• Tipe: Microsoft Real Account (@outlook.com)\n"
        f"• Status: 100% Siap Digunakan\n\n"
        f"🟢 <b>Server Hotmail:</b>\n"
        f"• Server-2 (Premium): <b>~8.708 Akun</b>\n"
        f"• Tipe: Microsoft Real Account (@hotmail.com)\n"
        f"• Status: 100% Siap Digunakan\n\n"
        f"🛡️ <b>Jaminan Anti-Duplikat:</b> <b>AKTIF</b>\n"
        f"<i>Setiap email yang Anda generate dijamin 100% baru dan belum pernah dipakai sebelumnya.</i>\n\n"
        f"⏱️ <i>Pesan ini otomatis dihapus dalam 15 detik.</i>"
    )

    msg = await (update.message or update.callback_query.message).reply_text(
        text, parse_mode=ParseMode.HTML
    )
    schedule_delete(msg, delay=15)


async def tarif_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        schedule_delete(update.message, delay=10)

    text = (
        f"🏷️ <b>DAFTAR TARIF & KETENTUAN LAYANAN</b>\n\n"
        f"💰 <b>Biaya per OTP:</b> <b>Rp {config.OTP_PRICE:,}</b>\n"
        f"🛡️ <b>Garansi Adil (Fair Billing):</b>\n"
        f"Saldo Anda <b>HANYA DIPOTONG</b> saat email / kode OTP berhasil masuk ke inbox. Jika OTP tidak masuk atau order dibatalkan, saldo Anda <b>Rp 0 (TIDAK DIPOTONG)</b>!\n\n"
        f"💳 <b>Ketentuan Top Up Saldo:</b>\n"
        f"• Minimal Top Up: <b>Rp {config.MIN_TOPUP:,}</b>\n"
        f"• Metode Pembayaran: <b>QRIS Otomatis Dinamis</b>\n"
        f"• Mendukung Semua Bank & E-Wallet: BCA, Mandiri, BRI, BNI, Jago, DANA, GoPay, OVO, ShopeePay\n"
        f"• <b>Kode Unik 3 Digit:</b> Otomatis ditambahkan di belakang nominal agar mutasi Anda langsung diverifikasi cepat oleh Admin.\n\n"
        f"⏱️ <i>Pesan ini otomatis dihapus dalam 15 detik.</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💰 Top Up Saldo Sekarang", callback_data="btn_topup")]
    ])

    msg = await (update.message or update.callback_query.message).reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )
    schedule_delete(msg, delay=15)


async def cs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        schedule_delete(update.message, delay=10)

    text = (
        f"💬 <b>PUSAT BANTUAN & CUSTOMER SERVICE</b>\n\n"
        f"Jika Anda membutuhkan bantuan terkait:\n"
        f"• Konfirmasi Top Up Saldo / Masalah Pembayaran\n"
        f"• Pertanyaan seputar Layanan Email / OTP\n"
        f"• Request layanan atau stok khusus\n\n"
        f"Silakan langsung hubungi Admin / CS kami:\n"
        f"👤 <b>Admin:</b> Ramba Store (@rambasstore)\n\n"
        f"⏱️ <i>Pesan ini otomatis dihapus dalam 20 detik.</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💬 Chat Admin di Telegram", url="https://t.me/rambasstore")]
    ])

    msg = await (update.message or update.callback_query.message).reply_text(
        text, parse_mode=ParseMode.HTML, reply_markup=keyboard
    )
    schedule_delete(msg, delay=20)


async def order_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        schedule_delete(update.message, delay=10)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📧 Pesan Gmail (Server 2 & 3)", callback_data="btn_ord_gmail")],
        [InlineKeyboardButton("📬 Pesan Outlook (Server 2)", callback_data="btn_ord_outlook")],
        [InlineKeyboardButton("📨 Pesan Hotmail (Server 2)", callback_data="btn_ord_hotmail")]
    ])
    msg = await update.message.reply_text(
        "⚡ <b>PILIH LAYANAN EMAIL OTP REAL ACCOUNT:</b>",
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )
    schedule_delete(msg, delay=20)


# ================= ADMIN DASHBOARD & COMMANDS =================
async def admin_panel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in config.ADMIN_IDS:
        return

    stats = db.get_stats()
    session_status = await smailpro.check_session_status()

    premium_icon = "✅ AKTIF" if session_status.get("isPremium") else "❌ BELUM LOGIN"

    text = (
        f"⚙️ <b>PANEL KONTROL ADMIN</b>\n\n"
        f"📊 <b>Statistik Bot:</b>\n"
        f"• Total Pengguna: <b>{stats['total_users']}</b> orang\n"
        f"• Total Order: <b>{stats['total_orders']}</b> pesanan\n"
        f"• Order Sukses (OTP Masuk): <b>{stats['completed_orders']}</b>\n"
        f"• Total Omset OTP: <b>Rp {stats['total_revenue']:,}</b>\n"
        f"• Total Saldo Mengendap: <b>Rp {stats['total_user_balance']:,}</b>\n\n"
        f"🌐 <b>Status Server Gmail, Outlook, Hotmail:</b>\n"
        f"• Status Premium: <b>{premium_icon}</b>\n"
        f"• Logged In: <b>{session_status.get('isLoggedIn')}</b>\n\n"
        f"🛠️ <b>Fitur Analisis & Kontrol Admin:</b>\n"
        f"Klik tombol menu di bawah untuk melihat rincian analytics kebutuhan user & leaderboard:"
    )

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Analisis Kebutuhan OTP", callback_data="adm_analytics"),
            InlineKeyboardButton("🏆 Top User & Top Up", callback_data="adm_topusers")
        ],
        [
            InlineKeyboardButton("🔄 Refresh Status", callback_data="adm_refresh_panel")
        ]
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)


async def admin_analytics_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in config.ADMIN_IDS:
        return

    analytics = db.get_service_analytics()

    service_lines = []
    for s in analytics["services"]:
        srv_name = s["service"].upper()
        service_lines.append(f"• <b>{srv_name}</b>: {s['total_orders']} order ({s['completed_orders']} sukses OTP)")
    service_text = "\n".join(service_lines) if service_lines else "<i>Belum ada order</i>"

    platform_lines = []
    for idx, p in enumerate(analytics["top_platforms"], 1):
        platform_lines.append(f"{idx}. <b>{p['platform']}</b>: <code>{p['count']} OTP</code>")
    platform_text = "\n".join(platform_lines) if platform_lines else "<i>Belum ada data platform</i>"

    sender_lines = []
    for idx, snd in enumerate(analytics["top_senders"][:8], 1):
        sender_lines.append(f"{idx}. <code>{snd['sender']}</code> ({snd['count']}x)")
    sender_text = "\n".join(sender_lines) if sender_lines else "<i>Belum ada data pengirim</i>"

    text = (
        f"📊 <b>ANALISIS KEBUTUHAN LAYANAN OTP USER</b>\n\n"
        f"📦 <b>Pemakaian Email per Server:</b>\n"
        f"{service_text}\n\n"
        f"🎯 <b>Platform / Layanan yang Paling Banyak Didaftarkan User:</b>\n"
        f"{platform_text}\n\n"
        f"📬 <b>Top Pengirim (Sender OTP Terbanyak):</b>\n"
        f"{sender_text}\n\n"
        f"💡 <i>Gunakan data ini untuk mengetahui tren aplikasi apa yang paling banyak diminati oleh pembeli!</i>"
    )

    back_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Kembali ke Panel Admin", callback_data="adm_refresh_panel")]
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard)


async def admin_topusers_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in config.ADMIN_IDS:
        return

    data = db.get_top_users_analytics(limit=8)

    otp_lines = []
    for idx, u in enumerate(data["top_otp_users"], 1):
        uname = f"@{u['username']}" if u.get("username") else u.get("first_name", "User")
        otp_lines.append(
            f"{idx}. 👤 <b>{uname}</b> [<code>{u['user_id']}</code>]\n"
            f"   ↳ <b>{u['otp_count']}</b> OTP Sukses | Total Belanja: <code>Rp {u['total_spent']:,}</code>"
        )
    otp_text = "\n".join(otp_lines) if otp_lines else "<i>Belum ada transaksi OTP</i>"

    topup_lines = []
    for idx, u in enumerate(data["top_topup_users"], 1):
        uname = f"@{u['username']}" if u.get("username") else u.get("first_name", "User")
        topup_lines.append(
            f"{idx}. 💎 <b>{uname}</b> [<code>{u['user_id']}</code>]\n"
            f"   ↳ Total Top Up: <b>Rp {u['total_topup_amount']:,}</b> ({u['topup_count']}x transfer)"
        )
    topup_text = "\n".join(topup_lines) if topup_lines else "<i>Belum ada riwayat top up</i>"

    text = (
        f"🏆 <b>LEADERBOARD TOP USER & TOP UP</b>\n\n"
        f"🔥 <b>Top Pengguna OTP Terbanyak:</b>\n"
        f"{otp_text}\n\n"
        f"💰 <b>Top Pengguna Top Up Terbesar:</b>\n"
        f"{topup_text}"
    )

    back_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Kembali ke Panel Admin", callback_data="adm_refresh_panel")]
    ])

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=back_keyboard)


async def cmd_tambahsaldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_IDS:
        return

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        new_balance = db.update_balance(user_id, amount, "admin_adjust", "Ditambahkan manual oleh Admin")
        await update.message.reply_text(f"✅ Berhasil menambahkan Rp {amount:,} ke User {user_id}. Saldo sekarang: Rp {new_balance:,}.")
        # Notify user
        await context.bot.send_message(
            chat_id=user_id,
            text=f"🎁 Admin telah menambahkan saldo sebesar <b>Rp {amount:,}</b> ke akun Anda.\nTotal saldo: <b>Rp {new_balance:,}</b>.",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        await update.message.reply_text("Format salah. Gunakan: /tambahsaldo <user_id> <jumlah>")


async def cmd_kurangsaldo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_IDS:
        return

    try:
        user_id = int(context.args[0])
        amount = int(context.args[1])
        new_balance = db.update_balance(user_id, -amount, "admin_adjust", "Dikurangi manual oleh Admin")
        await update.message.reply_text(f"✅ Berhasil memotong Rp {amount:,} dari User {user_id}. Saldo sekarang: Rp {new_balance:,}.")
    except Exception as e:
        await update.message.reply_text("Format salah. Gunakan: /kurangsaldo <user_id> <jumlah>")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in config.ADMIN_IDS:
        return

    message_text = " ".join(context.args).strip()
    if not message_text:
        await update.message.reply_text("Silakan tulis pesan broadcast. Contoh: /broadcast Halo semuanya!")
        return

    user_ids = db.get_all_user_ids()
    sent = 0
    await update.message.reply_text(f"📢 Memulai broadcast ke {len(user_ids)} pengguna...")

    for uid in user_ids:
        try:
            await context.bot.send_message(chat_id=uid, text=f"📢 <b>PENGUMUMAN</b>\n\n{message_text}", parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass

    await update.message.reply_text(f"✅ Broadcast selesai. Terkirim ke {sent}/{len(user_ids)} pengguna.")


# ================= TEXT MESSAGE ROUTER =================
async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    user_id = update.effective_user.id

    # Check topup custom amount state
    if user_id in user_topup_states and user_topup_states[user_id].get("step") == "waiting_custom_amount":
        schedule_delete(update.message, delay=10)
        if text.isdigit():
            amt = int(text)
            if amt < config.MIN_TOPUP:
                warn = await update.message.reply_text(f"❌ Minimal top up adalah Rp {config.MIN_TOPUP:,}. Silakan masukkan nominal yang valid:")
                schedule_delete(warn, delay=10)
                return
            await show_payment_instructions(update.message, user_id, amt)
            return
        else:
            warn = await update.message.reply_text("Ketikkan angka saja, contoh: 10000")
            schedule_delete(warn, delay=10)
            return

    # Main Menu routing
    if text.startswith("📧 Otp by Gmail") or text == "📧 Pesan OTP Gmail":
        await handle_order_request(update, context, service="gmail")
    elif text.startswith("📬 Otp by Outlook") or text == "📬 Pesan OTP Outlook":
        await handle_order_request(update, context, service="outlook")
    elif text.startswith("📨 Otp by Hotmail") or text == "📨 Pesan OTP Hotmail":
        await handle_order_request(update, context, service="hotmail")
    elif text == "💰 Top Up Saldo":
        await topup_menu_handler(update, context)
    elif text == "👤 Profil & Saldo":
        await profile_handler(update, context)
    elif text == "📊 Cek Stok Server":
        await stok_handler(update, context)
    elif text == "📖 Riwayat Order":
        await history_handler(update, context)
    elif text == "🏷️ Tarif & Info":
        await tarif_handler(update, context)
    elif text == "💬 Hubungi CS / Admin":
        await cs_handler(update, context)
    elif text == "ℹ️ Bantuan":
        await help_handler(update, context)
    elif text in ("🧹 Bersihkan Chat", "/clear"):
        await clear_command(update, context)
    elif text == "⚙️ Panel Admin":
        await admin_panel_handler(update, context)
    else:
        # Default fallback
        schedule_delete(update.message, delay=10)
        fb = await update.message.reply_text(
            "Silakan gunakan tombol menu di bawah untuk bernavigasi:",
            reply_markup=get_main_keyboard(user_id in config.ADMIN_IDS)
        )
        schedule_delete(fb, delay=10)


from aiohttp import web

# ================= RENDER HTTP HEALTH CHECK & QRIS HOOK WEBHOOK =================
async def health_check(request):
    recent_topups = []
    recent_users = []
    recent_orders = []
    try:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, user_id, amount, status, created_at FROM topups ORDER BY id DESC LIMIT 10")
            recent_topups = [dict(r) for r in cursor.fetchall()]
            cursor.execute("SELECT user_id, username, first_name, balance, total_spent FROM users ORDER BY user_id DESC LIMIT 10")
            recent_users = [dict(r) for r in cursor.fetchall()]
            cursor.execute("SELECT id, user_id, service, email_address, status, created_at FROM orders ORDER BY id DESC LIMIT 10")
            recent_orders = [dict(r) for r in cursor.fetchall()]
    except Exception as e:
        recent_topups = [{"error": str(e)}]

    return web.json_response({
        "status": "online",
        "service": "telegram_otp_bot",
        "qrishook_webhook": "/webhook/qris",
        "users_count": len(recent_users),
        "recent_users": recent_users,
        "recent_topups": recent_topups,
        "recent_orders": recent_orders,
        "timestamp": datetime.now().isoformat()
    })


async def qris_webhook_handler(request: web.Request):
    """
    Receives incoming QRIS payment notifications forwarded by QRIS Hook Android App (suriyadi15/qrishook).
    Automatically matches payment amount with pending topups, approves topup, and credits user balance instantly.
    """
    # 1. Verify Secret if configured
    if config.QRISHOOK_SECRET:
        req_secret = (
            request.headers.get("X-Webhook-Secret") or
            request.query.get("secret")
        )
        if not req_secret or req_secret != config.QRISHOOK_SECRET:
            logger.warning("QRISHook unauthorized request received (secret mismatch)")
            return web.json_response({"error": "Unauthorized: secret mismatch"}, status=401)

    # 2. Parse JSON payload
    try:
        data = await request.json()
    except Exception as e:
        logger.error("Failed to parse JSON from QRISHook: %s", e)
        return web.json_response({"error": "Invalid JSON"}, status=400)

    logger.info("Received QRISHook event: %s", data)

    # 3. Extract amount, sender_name, and source
    raw_amount = None
    sender_name = "Pengirim QRIS"
    source_app = "QRIS"

    if isinstance(data, dict):
        source_app = data.get("source_app") or data.get("source_package") or "QRIS"

        # Format standard suriyadi15/qrishook: {"payment": {"amount": 5482, ...}}
        payment_info = data.get("payment")
        if isinstance(payment_info, dict):
            raw_amount = payment_info.get("amount")
            if payment_info.get("sender_name"):
                sender_name = payment_info.get("sender_name")
            if payment_info.get("payment_source"):
                source_app = f"{source_app} ({payment_info.get('payment_source')})"

        # Fallback to root level amount
        if raw_amount is None:
            raw_amount = data.get("amount")

        # Fallback to parsing from notification title/text
        if raw_amount is None:
            notif = data.get("notification") or {}
            notif_text = f"{notif.get('title', '')} {notif.get('text', '')} {notif.get('big_text', '')}"
            m = re.search(r'(?:Rp\.?|IDR)\s*([0-9.,]+)', notif_text, re.IGNORECASE)
            if m:
                raw_amount = m.group(1)

    if not raw_amount:
        logger.warning("QRISHook payload missing amount: %s", data)
        return web.json_response({"error": "Missing payment amount in payload"}, status=400)

    # Clean amount to integer
    try:
        clean_amt_str = str(raw_amount).replace(".", "").replace(",", "").strip()
        amount = int(float(clean_amt_str))
    except Exception as e:
        logger.error("Invalid amount format in QRISHook: %s (%s)", raw_amount, e)
        return web.json_response({"error": f"Invalid amount format: {raw_amount}"}, status=400)

    if amount <= 0:
        return web.json_response({"error": "Amount must be greater than 0"}, status=400)

    logger.info("Processing QRISHook incoming payment: Rp %s from %s (%s)", amount, sender_name, source_app)

    # Detect Test / Demo ping from QRIS Hook app
    if (data.get("merchant_id") == "demo_merchant" or
        "test" in str(data.get("type", "")).lower() or
        sender_name == "John Doe"):
        logger.info("QRISHook test connection event received successfully!")
        return web.json_response({
            "success": True,
            "status": "ok",
            "message": "QRIS Hook test notification received successfully! Webhook is active and working."
        }, status=200)

    # 4. Search matching pending topup in database
    topup = db.find_pending_topup_by_amount(amount)
    if not topup:
        # Fallback: check if there is a pending topup in the same thousand range (e.g. 5100 - 5999)
        base_k = (amount // 1000) * 1000
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM topups 
                WHERE status = 'pending' AND amount BETWEEN ? AND ?
                ORDER BY id DESC LIMIT 1
            """, (base_k + 100, base_k + 999))
            fallback_row = cursor.fetchone()
            if fallback_row:
                topup = dict(fallback_row)
                logger.info("Found fuzzy fallback pending topup: #%s for user %s (expected Rp %s, paid Rp %s)",
                            topup["id"], topup["user_id"], topup["amount"], amount)

    if not topup:
        # Check if amount is a round multiple of 1000 (e.g. 50000, 150000, 300000)
        # All bot topups have unique code (101-999) so amount % 1000 != 0.
        # Round amounts are store transactions outside the bot, ignore silently!
        if amount % 1000 == 0:
            logger.info("Silently ignoring round store transaction of Rp %s", amount)
            return web.json_response({
                "success": True,
                "message": f"Store payment Rp {amount:,} acknowledged (non-bot transaction ignored silently)."
            }, status=200)

        logger.warning("No pending topup found for exact or fuzzy amount: Rp %s", amount)
        # Find candidate users who interacted with the bot recently
        recent_candidates = []
        try:
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT user_id, username, first_name FROM users ORDER BY created_at DESC LIMIT 3")
                recent_candidates = [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error("Could not fetch recent users for assign buttons: %s", e)

        # Notify admins about unmatched incoming funds WITH 1-CLICK ASSIGN BUTTONS
        if telegram_bot:
            buttons = []
            for c in recent_candidates:
                label = f"✅ Masukkan ke {c['first_name']} (@{c.get('username') or c['user_id']})"
                buttons.append([InlineKeyboardButton(label, callback_data=f"adm_assign_{amount}_{c['user_id']}")])
            buttons.append([InlineKeyboardButton("❌ Abaikan (Bukan Top Up Bot)", callback_data="adm_ignore_topup")])
            admin_kb = InlineKeyboardMarkup(buttons)

            for admin_id in config.ADMIN_IDS:
                try:
                    await telegram_bot.send_message(
                        chat_id=admin_id,
                        text=(
                            f"⚠️ <b>[QRISHOOK] MUTASI MASUK (KODE UNIK TERDETEKSI)</b>\n\n"
                            f"💰 <b>Nominal:</b> <code>Rp {amount:,}</code>\n"
                            f"👤 <b>Pengirim:</b> {sender_name}\n"
                            f"📱 <b>Aplikasi:</b> {source_app}\n"
                            f"⏱️ <b>Waktu:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                            f"<i>Order otomatis tidak ditemukan di database (kemungkinan server baru restart atau waktu order habis). Klik tombol di bawah untuk langsung memasukkan saldo ke akun user:</i>"
                        ),
                        parse_mode=ParseMode.HTML,
                        reply_markup=admin_kb
                    )
                except Exception as err:
                    logger.error("Failed to notify admin of unmatched QRISHook: %s", err)

        return web.json_response({
            "success": False,
            "message": f"Payment Rp {amount:,} received, but no matching pending topup found in database."
        }, status=200)

    # 5. Automatically approve topup
    topup_id = topup["id"]
    user_id = topup["user_id"]
    approval_res = db.approve_topup(
        topup_id=topup_id,
        admin_id=0,
        description=f"Top Up Otomatis QRIS Hook (Topup #{topup_id} - {sender_name})"
    )

    if not approval_res:
        logger.warning("Topup #%s was already approved or cancelled.", topup_id)
        return web.json_response({
            "success": True,
            "message": f"Topup #{topup_id} already processed"
        }, status=200)

    new_balance = approval_res["new_balance"]
    logger.info("SUCCESS: Topup #%s approved automatically! User %s new balance: Rp %s", topup_id, user_id, new_balance)

    # 6. Send instant Telegram notifications
    if telegram_bot:
        # Notify User
        try:
            user_text = (
                f"🎉 <b>PEMBAYARAN QRIS BERHASIL DITERIMA!</b>\n\n"
                f"✅ Saldo sebesar <b>Rp {amount:,}</b> telah berhasil ditambahkan ke akun Anda secara otomatis!\n\n"
                f"💳 <b>Saldo Baru:</b> <code>Rp {new_balance:,}</code>\n"
                f"🆔 <b>ID Top Up:</b> <code>#{topup_id}</code>\n"
                f"⏱️ <b>Waktu:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"Terima kasih atas pembayaran Anda! Sekarang Anda sudah bisa langsung memesan email OTP."
            )
            await telegram_bot.send_message(
                chat_id=user_id,
                text=user_text,
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_keyboard(user_id in config.ADMIN_IDS)
            )
        except Exception as err:
            logger.error("Failed to notify user %s of auto topup approval: %s", user_id, err)

        # Notify Admin
        for admin_id in config.ADMIN_IDS:
            try:
                admin_text = (
                    f"⚡ <b>[QRISHOOK OTOMATIS] TOP UP MASUK & SELESAI!</b>\n\n"
                    f"🆔 <b>ID Top Up:</b> <code>#{topup_id}</code>\n"
                    f"👤 <b>User ID:</b> <code>{user_id}</code>\n"
                    f"💰 <b>Nominal:</b> <b>Rp {amount:,}</b> (Lunas)\n"
                    f"💳 <b>Saldo Baru User:</b> Rp {new_balance:,}\n"
                    f"🏦 <b>Sumber / Pengirim:</b> {sender_name} ({source_app})\n"
                    f"⏱️ <b>Status:</b> Auto-Approved (Sistem QRIS Hook)"
                )
                await telegram_bot.send_message(
                    chat_id=admin_id,
                    text=admin_text,
                    parse_mode=ParseMode.HTML
                )
            except Exception as err:
                logger.error("Failed to notify admin %s of auto topup: %s", admin_id, err)

    return web.json_response({
        "success": True,
        "status": "approved",
        "topup_id": topup_id,
        "user_id": user_id,
        "amount": amount,
        "new_balance": new_balance
    }, status=200)


async def start_health_server():
    try:
        app = web.Application()
        app.router.add_get("/", health_check)
        app.router.add_get("/health", health_check)
        app.router.add_get("/webhook", health_check)
        app.router.add_get("/webhook/qris", health_check)
        app.router.add_get("/qrishook", health_check)
        app.router.add_post("/", qris_webhook_handler)
        app.router.add_post("/webhook", qris_webhook_handler)
        app.router.add_post("/webhook/qris", qris_webhook_handler)
        app.router.add_post("/qrishook", qris_webhook_handler)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", config.PORT)
        await site.start()
        logger.info("Render health check & QRISHook webhook server running on port %s", config.PORT)
    except Exception as e:
        logger.warning("Could not start health check web server on port %s: %s", config.PORT, e)


# ================= BOT INITIALIZATION & START =================
async def post_init(application: Application):
    """Initializes SmailPro engine, health check server, and bottom-left menu commands."""
    global telegram_bot
    telegram_bot = application.bot

    # Start web server for Render health check and QRISHook webhook
    await start_health_server()

    # Set bottom-left Menu Button commands
    from telegram import BotCommand
    commands = [
        BotCommand("start", "Mulai bot & menu utama"),
        BotCommand("order", "Pesan email OTP"),
        BotCommand("topup", "Top up saldo (QRIS otomatis)"),
        BotCommand("saldo", "Cek saldo & profil akun"),
        BotCommand("stok", "Cek live stok server email"),
        BotCommand("riwayat", "Riwayat 8 order terakhir"),
        BotCommand("tarif", "Daftar tarif & ketentuan"),
        BotCommand("clear", "Bersihkan semua riwayat chat"),
        BotCommand("cs", "Hubungi Customer Service"),
        BotCommand("bantuan", "Panduan lengkap bot")
    ]
    try:
        await application.bot.set_my_commands(commands)
        logger.info("Standard Telegram bot commands menu registered successfully")
    except Exception as e:
        logger.warning("Could not set bot commands: %s", e)
    
    logger.info("Initializing SmailPro browser engine...")
    try:
        await smailpro.initialize()
        logger.info("SmailPro engine initialized successfully!")
    except Exception as e:
        logger.error("Failed to start SmailPro engine on boot: %s", e)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Logs errors caused by Updates."""
    logger.error("Exception while handling an update: %s", context.error)


def main():
    if not config.BOT_TOKEN or "xx" in config.BOT_TOKEN:
        print("\n" + "=" * 65)
        print("❌ ERROR: BOT_TOKEN belum diisi di file .env!")
        print("1. Salin .env.example menjadi .env: copy .env.example .env")
        print("2. Buka file .env dan isi BOT_TOKEN dari @BotFather di Telegram.")
        print("=" * 65 + "\n")
        return

    logger.info("Starting Telegram Bot application...")

    application = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Commands
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("order", order_command))
    application.add_handler(CommandHandler("stok", stok_handler))
    application.add_handler(CommandHandler("tarif", tarif_handler))
    application.add_handler(CommandHandler("clear", clear_command))
    application.add_handler(CommandHandler("cs", cs_handler))
    application.add_handler(CommandHandler("bantuan", help_handler))
    application.add_handler(CommandHandler("saldo", profile_handler))
    application.add_handler(CommandHandler("riwayat", history_handler))
    application.add_handler(CommandHandler("topup", topup_menu_handler))
    application.add_handler(CommandHandler("analytics", admin_analytics_handler))
    application.add_handler(CommandHandler("topuser", admin_topusers_handler))
    application.add_handler(CommandHandler("tambahsaldo", cmd_tambahsaldo))
    application.add_handler(CommandHandler("kurangsaldo", cmd_kurangsaldo))
    application.add_handler(CommandHandler("broadcast", cmd_broadcast))
    application.add_handler(CommandHandler("admin", admin_panel_handler))

    # Callback queries
    application.add_handler(CallbackQueryHandler(check_inbox_callback, pattern=r"^check_inbox_"))
    application.add_handler(CallbackQueryHandler(auto_poll_callback, pattern=r"^auto_poll_"))
    application.add_handler(CallbackQueryHandler(cancel_order_callback, pattern=r"^cancel_order_"))
    application.add_handler(CallbackQueryHandler(delete_card_callback, pattern=r"^delete_card_"))
    application.add_handler(CallbackQueryHandler(noop_callback, pattern=r"^noop_"))
    application.add_handler(CallbackQueryHandler(topup_menu_handler, pattern=r"^btn_topup$"))
    application.add_handler(CallbackQueryHandler(history_handler, pattern=r"^btn_history$"))
    application.add_handler(CallbackQueryHandler(topup_amount_callback, pattern=r"^topup_amt_"))
    application.add_handler(CallbackQueryHandler(admin_topup_callback, pattern=r"^adm_"))
    application.add_handler(CallbackQueryHandler(admin_analytics_handler, pattern=r"^adm_analytics$"))
    application.add_handler(CallbackQueryHandler(admin_topusers_handler, pattern=r"^adm_topusers$"))
    application.add_handler(CallbackQueryHandler(admin_panel_handler, pattern=r"^adm_refresh_panel$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: handle_order_request(u, c, "gmail"), pattern=r"^btn_ord_gmail$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: handle_order_request(u, c, "outlook"), pattern=r"^btn_ord_outlook$"))
    application.add_handler(CallbackQueryHandler(lambda u, c: handle_order_request(u, c, "hotmail"), pattern=r"^btn_ord_hotmail$"))

    # Photos (payment receipts)
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_proof))

    # Text router
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    # Error handler
    application.add_error_handler(error_handler)

    print("==================================================")
    print("🚀 BOT TELEGRAM OTP EMAIL AKTIF DAN SIAP DIGUNAKAN!")
    print("==================================================")

    # Run bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
