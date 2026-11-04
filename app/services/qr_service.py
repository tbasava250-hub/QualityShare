"""
QualityShare QR Code Service

Generates a QR code image from a share URL.
The QR code is returned as PNG bytes — it's never stored on disk.
"""

import io
import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.colormasks import SolidFillColorMask


def generate_qr_code(url, size=10, border=2):
    """
    Generate a QR code PNG image for the given URL.

    Args:
        url: the full sharing URL (e.g. "https://example.com/s/abc123")
        size: box size in pixels (controls resolution)
        border: quiet zone width in boxes

    Returns:
        PNG image as bytes, ready to be sent as an HTTP response
        or embedded as a base64 data URI.
    """
    qr = qrcode.QRCode(
        version=None,  # auto-detect smallest version that fits
        error_correction=qrcode.constants.ERROR_CORRECT_H,  # high error correction
        box_size=size,
        border=border,
    )
    qr.add_data(url)
    qr.make(fit=True)

    # Create the image with a clean black-on-white style
    img = qr.make_image(
        image_factory=StyledPilImage,
        color_mask=SolidFillColorMask(
            back_color=(255, 255, 255),  # white background
            front_color=(33, 33, 33),     # near-black modules
        ),
    )

    # Write to an in-memory buffer and return the bytes
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()
