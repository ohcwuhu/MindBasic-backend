"""邮箱验证码邮件发送（SMTP；未启用时仅打印，便于开发调试）。"""

import hashlib
import logging
import random
import smtplib
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger("mindbasic")


def generate_code() -> str:
    return f"{random.SystemRandom().randint(0, 999999):06d}"


def hash_code(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def send_email(to: str, subject: str, text: str) -> None:
    if not settings.email_enabled:
        logger.info("email disabled, code mail to %s: %s", to, text)
        return
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = to
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
        server.starttls()
        server.login(settings.smtp_user, settings.smtp_password)
        server.sendmail(settings.smtp_from, [to], msg.as_string())
