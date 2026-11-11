import io
import re
import qrcode
from PIL import Image

def crc16_ccitt(data: str) -> str:
    """Calculates CRC16-CCITT checksum for ASPI/EMVCo QRIS string."""
    crc = 0xFFFF
    for ch in data.encode('ascii'):
        crc ^= (ch << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"

def generate_dynamic_qris(static_qris: str, amount: int) -> str:
    """
    Converts a static QRIS string into a Dynamic QRIS string with the exact amount.
    The customer scanning this will have the nominal filled in automatically!
    """
    clean_qris = static_qris.strip()
    
    # Strip existing CRC tag (last 4 hex chars, and '6304' if at end)
    qris_body = clean_qris[:-4]
    if qris_body.endswith("6304"):
        qris_body = qris_body[:-4]

    # Convert static indicator (010211) to dynamic (010212)
    qris_body = qris_body.replace("010211", "010212", 1)

    # Format Tag 54 (Transaction Amount): 54 + length + amount
    amt_str = str(int(amount))
    amt_tag = f"54{len(amt_str):02d}{amt_str}"

    # Remove any existing Tag 54 to prevent duplicate
    qris_body = re.sub(r'54\d{2}\d+', '', qris_body)

    # Insert Tag 54 immediately before Tag 5802ID
    if "5802ID" in qris_body:
        parts = qris_body.split("5802ID", 1)
        new_qris_body = f"{parts[0]}{amt_tag}5802ID{parts[1]}"
    else:
        new_qris_body = f"{qris_body}{amt_tag}"

    # Append Tag 6304 and recalculate CRC
    payload_to_crc = f"{new_qris_body}6304"
    crc = crc16_ccitt(payload_to_crc)

    return f"{payload_to_crc}{crc}"

def generate_qr_image_from_payload(qr_payload: str) -> bytes:
    """Generates QR code PNG image bytes directly from a raw QRIS string."""
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(qr_payload)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()

def generate_qris_image_bytes(static_qris: str, amount: int) -> bytes:
    """Generates QR code PNG image bytes from a static QRIS template + amount."""
    dynamic_payload = generate_dynamic_qris(static_qris, amount)
    return generate_qr_image_from_payload(dynamic_payload)
