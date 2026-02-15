import mimetypes
import smtplib
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr


def _resolve_from_email(smtp_settings):
    configured = (getattr(smtp_settings, "from_email", "") or "").strip()
    username = (getattr(smtp_settings, "username", "") or "").strip()
    host = (getattr(smtp_settings, "host", "") or "").strip().lower()

    _, configured_addr = parseaddr(configured)
    configured_addr = configured_addr.strip().lower()
    username_addr = username.strip().lower()

    if not configured_addr:
        return username_addr or configured or username

    # Deliverability guard: Gmail SMTP often rejects/filters mismatched From.
    if "gmail" in host and username_addr and configured_addr != username_addr:
        return username_addr

    return configured_addr or configured


def send_email_smtp(smtp_settings, to_email, subject, body_html, body_text=None, inline_images=None):
    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    effective_from = _resolve_from_email(smtp_settings)
    msg["From"] = effective_from
    msg["To"] = to_email

    alternative = MIMEMultipart("alternative")
    msg.attach(alternative)

    if body_text:
        alternative.attach(MIMEText(body_text, "plain", "utf-8"))
    alternative.attach(MIMEText(body_html, "html", "utf-8"))

    for img in inline_images or []:
        path = img.get("path")
        cid = img.get("cid")
        if not path or not cid:
            continue
        try:
            with open(path, "rb") as f:
                data = f.read()
            guessed, _ = mimetypes.guess_type(path)
            subtype = "png"
            if guessed and "/" in guessed:
                subtype = guessed.split("/", 1)[1]
            mime_img = MIMEImage(data, _subtype=subtype)
            mime_img.add_header("Content-ID", f"<{cid}>")
            mime_img.add_header("Content-Disposition", "inline")
            msg.attach(mime_img)
        except Exception:
            continue

    server = smtplib.SMTP(smtp_settings.host, smtp_settings.port, timeout=15)
    try:
        if smtp_settings.use_tls:
            server.starttls()
        server.login(smtp_settings.username, smtp_settings.password)
        server.sendmail(effective_from, [to_email], msg.as_string())
    finally:
        server.quit()
