import asyncio
import logging
import re
import sys
from datetime import datetime
from typing import Dict

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from telegram import (
    Update,
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
    """Returns persistent main menu reply keyboard."""
    buttons = [
        [KeyboardButton("📧 Otp by Gmail ( 1700 stok )")],
        [KeyboardButton("📬 Otp by Outlook ( 8700 stok )"), KeyboardButton("📨 Otp by Hotmail ( 8700 stok )")],
        [KeyboardButton("💰 Top Up Saldo"), KeyboardButton("👤 Profil & Saldo")],
        [KeyboardButton("📖 Riwayat Order"), KeyboardButton("ℹ️ Bantuan")]
    ]
    if is_admin:
        buttons.append([KeyboardButton("⚙️ Panel Admin")])
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
        f"✨ <i>Semua email adalah <b>Real Account</b> dan dijamin <b>100% selalu baru</b> (tidak pernah didaur ulang/dipakai sebelumnya).</i>\n\n"
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
    """Generates dynamic QRIS image with 3-digit unique code and displays payment details."""
    import random

    if base_amount < config.MIN_TOPUP:
        base_amount = config.MIN_TOPUP

    # Round base to thousands and generate 3-digit unique code (101 - 999)
    base_thousands = (base_amount // 1000) * 1000
    unique_code = random.randint(101, 999)
    final_amount = base_thousands + unique_code

    user_topup_states[user_id] = {
        "step": "waiting_proof",
        "base_amount": base_thousands,
        "unique_code": unique_code,
        "amount": final_amount
    }

    caption = (
        f"💳 <b>PEMBAYARAN TOP UP VIA QRIS OTOMATIS</b>\n\n"
        f"💰 <b>Total Transfer:</b> <code>Rp {final_amount:,}</code>\n"
        f"🔢 <b>Nominal Pokok:</b> Rp {base_thousands:,}\n"
        f"🎯 <b>Kode Unik:</b> <code>+{unique_code}</code> <i>(Pembeda mutasi transfer Anda)</i>\n"
        f"🏷️ <b>Merchant:</b> PT SERVER HANDAL, SUKOLIL\n\n"
        f"⚡ <b>Keunggulan QRIS Ini:</b>\n"
        f"Nominal tepat <b>Rp {final_amount:,}</b> sudah otomatis terisi saat di-scan! Anda tidak perlu mengetikkan nominal secara manual.\n"
        f"<i>💡 Saldo yang masuk ke akun Anda adalah <b>Rp {final_amount:,}</b> (penuh tanpa potongan)!</i>\n\n"
        f"📱 <b>Cara Bayar:</b>\n"
        f"1. Buka aplikasi m-Banking (BCA, Mandiri, BRI, BNI, Jago, dll) atau E-Wallet (DANA, GoPay, OVO, ShopeePay).\n"
        f"2. Scan / upload QR Code di atas.\n"
        f"3. Pastikan nominal tertera tepat <b>Rp {final_amount:,}</b> lalu selesaikan pembayaran.\n"
        f"4. 📸 <b>KIRIM FOTO / SCREENSHOT BUKTI TRANSFER</b> langsung ke chat bot ini.\n\n"
        f"💡 <i>Metode Alternatif (Transfer Manual):</i>\n"
        f"• DANA: <code>{config.PAYMENT_DANA}</code>\n"
        f"• GOPAY: <code>{config.PAYMENT_GOPAY}</code>\n"
        f"• OVO: <code>{config.PAYMENT_OVO}</code>\n"
        f"• BANK: <code>{config.PAYMENT_BANK}</code>\n\n"
        f"⚠️ <i>Pastikan mentransfer tepat <b>Rp {final_amount:,}</b> agar admin dapat langsung mengenali mutasi transfer Anda.</i>"
    )

    try:
        # Generate dynamic QRIS PNG in memory with final_amount
        qr_bytes = generate_qris_image_bytes(config.STATIC_QRIS, final_amount)
        qr_msg = await message_target.reply_photo(
            photo=qr_bytes,
            caption=caption,
            parse_mode=ParseMode.HTML
        )
        schedule_delete(qr_msg, delay=300)
    except Exception as e:
        logger.error("Failed to generate QRIS photo: %s", e)
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
    photo = update.message.photo[-1]  # Highest resolution photo
    file_id = photo.file_id

    # Create topup record
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
        f"🛠️ <b>Perintah Admin:</b>\n"
        f"• <code>/tambahsaldo &lt;user_id&gt; &lt;jumlah&gt;</code>\n"
        f"• <code>/kurangsaldo &lt;user_id&gt; &lt;jumlah&gt;</code>\n"
        f"• <code>/broadcast &lt;pesan&gt;</code>"
    )

    await update.message.reply_text(text, parse_mode=ParseMode.HTML)


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
    elif text == "📖 Riwayat Order":
        await history_handler(update, context)
    elif text == "ℹ️ Bantuan":
        await help_handler(update, context)
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

# ================= RENDER HTTP HEALTH CHECK =================
async def health_check(request):
    return web.json_response({
        "status": "online",
        "service": "telegram_otp_bot",
        "timestamp": datetime.now().isoformat()
    })

async def start_health_server():
    try:
        app = web.Application()
        app.router.add_get("/", health_check)
        app.router.add_get("/health", health_check)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", config.PORT)
        await site.start()
        logger.info("Render health check web server running on port %s", config.PORT)
    except Exception as e:
        logger.warning("Could not start health check web server on port %s: %s", config.PORT, e)


# ================= BOT INITIALIZATION & START =================
async def post_init(application: Application):
    """Initializes SmailPro engine and health check server on bot startup."""
    # Start web server for Render health check
    await start_health_server()
    
    logger.info("Initializing SmailPro browser engine...")
    try:
        await smailpro.initialize()
        logger.info("SmailPro engine initialized successfully!")
    except Exception as e:
        logger.error("Failed to start SmailPro engine on boot: %s", e)


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

    # Photos (payment receipts)
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo_proof))

    # Text router
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    print("==================================================")
    print("🚀 BOT TELEGRAM OTP EMAIL AKTIF DAN SIAP DIGUNAKAN!")
    print("==================================================")

    # Run bot
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
