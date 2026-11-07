"""
QualityShare Email Service

Handles email delivery for password reset links.
Uses Gmail SMTP with App Password authentication.

SECURITY NOTES:
- Never hardcode credentials - use environment variables
- Never log sensitive information (passwords, tokens)
- Use generic success messages to prevent account enumeration
- Log only safe, non-sensitive information
"""

import logging
from typing import Optional

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.config import Config

logger = logging.getLogger(__name__)


class EmailServiceError(Exception):
    """Custom exception for email service errors."""


def _create_password_reset_email(
    recipient_email: str,
    reset_url: str,
    expiry_minutes: int
) -> MIMEMultipart:
    """
    Create a password reset email message.

    Args:
        recipient_email: The email address to send to
        reset_url: The full password reset URL
        expiry_minutes: How long the link is valid

    Returns:
        A MIMEMultipart email message ready to send
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = "QualityShare Password Reset"
    msg["From"] = Config.MAIL_DEFAULT_SENDER
    msg["To"] = recipient_email

    text_content = f"""Hello,

To reset your QualityShare password, please visit the following link:
{reset_url}

This link will expire in {expiry_minutes} minutes.

If you did not request a password reset, please ignore this email. Your password will remain unchanged.

Best regards,
The QualityShare Team
"""

    msg.attach(MIMEText(text_content, "plain"))
    return msg


def send_password_reset_email(
    recipient_email: str,
    reset_url: str,
    expiry_minutes: int = 30
) -> bool:
    """
    Send a password reset email to the specified recipient.

    SECURITY:
    - Never logs the reset URL or any sensitive data
    - Never reveals whether email address exists
    - Returns False if sending fails, but still shows generic success

    Args:
        recipient_email: The user's email address
        reset_url: The full password reset URL (must include token)
        expiry_minutes: Token expiration time in minutes

    Returns:
        True if email was sent successfully, False otherwise
    """
    try:
        # Validate configuration before attempting to send
        if not Config.MAIL_USERNAME:
            logger.error(
                "SMTP_USERNAME not configured - password reset email not sent"
            )
            return False

        if not Config.MAIL_PASSWORD:
            logger.error(
                "SMTP_PASSWORD not configured - password reset email not sent"
            )
            return False

        if not recipient_email or "@" not in recipient_email:
            logger.warning("Invalid recipient email - email not sent")
            return False

        # Create the email message
        msg = _create_password_reset_email(
            recipient_email,
            reset_url,
            expiry_minutes
        )

        # Connect to Gmail SMTP server
        server = smtplib.SMTP(Config.MAIL_SERVER, Config.MAIL_PORT)

        try:
            # Upgrade to secure connection if TLS is enabled
            if Config.MAIL_USE_TLS:
                server.starttls()

            # Authenticate with SMTP credentials
            server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)

            # Send the email
            # Note: We only log the count of recipients, not their addresses
            server.sendmail(Config.MAIL_DEFAULT_SENDER, recipient_email, msg.as_string())

        finally:
            server.quit()

        logger.info("Password reset email sent successfully")
        return True

    except smtplib.SMTPAuthenticationError as e:
        logger.error(
            "SMTP authentication failed (SMTP_HOST=%s, SMTP_PORT=%s, SMTP_USERNAME=%s): check username/app password. Error: %s",
            Config.MAIL_SERVER, Config.MAIL_PORT, Config.MAIL_USERNAME, str(e)
        )
        return False

    except smtplib.SMTPConnectError as e:
        logger.error(
            "Failed to connect to SMTP server (SMTP_HOST=%s, SMTP_PORT=%s): %s",
            Config.MAIL_SERVER, Config.MAIL_PORT, str(e)
        )
        return False

    except smtplib.SMTPException as e:
        logger.error(
            "SMTP error while sending password reset email (SMTP_HOST=%s, SMTP_PORT=%s): [%s] %s",
            Config.MAIL_SERVER, Config.MAIL_PORT, type(e).__name__, str(e)
        )
        return False

    except Exception as e:
        logger.error(
            "Unexpected error sending password reset email (SMTP_HOST=%s, SMTP_PORT=%s): [%s] %s",
            Config.MAIL_SERVER, Config.MAIL_PORT, type(e).__name__, str(e)
        )
        return False
