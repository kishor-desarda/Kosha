# SPDX-License-Identifier: AGPL-3.0-or-later
"""Built-in e-mail notifier: SMTP with the Python standard library, plain text plus simple HTML.

It follows the SDK's `Notifier` contract, so the outbox treats it like any notifier add-in. Settings live
in Kosha's own settings (Admin › Notifications); the password is stored like other secrets and never
shown again. Every connection has a time-out, and messages are only sent from the outbox worker or by
the admin's test button, never while a user waits for their own action.
"""
from __future__ import annotations

import hashlib
import logging
import re
import smtplib
import socket
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate

from . import db
from .addins import SECRET_MASK

from kosha_sdk import AddinContext, Health  # noqa: E402
from kosha_sdk.notify import Notice, Notifier, NotifyError, Recipient  # noqa: E402

log = logging.getLogger("kosha.mailer")
SETTINGS_KEY = "smtp"
SECURITY = {"starttls": "STARTTLS (usually port 587)", "ssl": "SSL/TLS (usually port 465)",
            "none": "None (only inside a trusted network, usually port 25)"}
DEFAULTS = {"enabled": False, "host": "", "port": 587, "security": "starttls", "username": "", "password": "",
            "from_address": "", "from_name": "Kosha", "timeout_s": 20, "verify_tls": True}
FOOTER = "You get this e-mail because of your choices under My notifications in Kosha."
_EMAIL = re.compile(r"^[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+$")


def settings() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in (db.get_setting(SETTINGS_KEY) or {}).items() if k in DEFAULTS})
    from .vault import unseal
    cfg["password"] = unseal(cfg.get("password") or "")      # kept encrypted (vault.py)
    return cfg


def masked() -> dict:
    cfg = settings()
    if cfg.get("password"):
        cfg["password"] = SECRET_MASK
    return cfg


def save(changes: dict) -> dict:
    """Check and store SMTP settings. Raises ValueError with a plain message; the masked password never overwrites."""
    cfg = settings()
    for k, v in changes.items():
        if k not in DEFAULTS or (k == "password" and v == SECRET_MASK):
            continue
        if k in ("enabled", "verify_tls"):
            v = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on")
        elif k in ("port", "timeout_s"):
            try:
                v = int(v)
            except (TypeError, ValueError):
                raise ValueError(f"{'Port' if k == 'port' else 'Time-out'} must be a whole number.") from None
            if k == "port" and not 0 < v < 65536:
                raise ValueError("Port must be between 1 and 65535.")
            if k == "timeout_s":
                v = max(3, min(v, 120))
        elif k == "security":
            if v not in SECURITY:
                raise ValueError("Security must be starttls, ssl or none.")
        else:
            v = " ".join(str(v or "").split()) if k != "password" else str(v or "")
        cfg[k] = v
    if cfg["from_address"] and not _EMAIL.match(cfg["from_address"]):
        raise ValueError("The sender address does not look like an e-mail address.")
    from .vault import seal
    db.set_setting(SETTINGS_KEY, {**cfg, "password": seal(cfg["password"])})
    return masked()


def configured(cfg: dict | None = None) -> tuple[bool, str]:
    """(ready, why not) for the channel list and the user's settings screen."""
    cfg = cfg or settings()
    if not cfg.get("host") or not cfg.get("from_address"):
        return False, "The mail server is not set up yet (Admin › Notifications)."
    if not cfg.get("enabled"):
        return False, "E-mail is switched off (Admin › Notifications)."
    return True, ""


def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def build_message(notice: Notice, to: Recipient, cfg: dict) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"[Kosha] {_one_line(notice.title)}"[:250]
    msg["From"] = formataddr((_one_line(cfg.get("from_name") or "Kosha"), cfg["from_address"]))
    msg["To"] = formataddr((_one_line(to.name), to.email))
    msg["Date"] = formatdate(usegmt=True)
    domain = cfg["from_address"].rsplit("@", 1)[-1]
    # the same notice to the same person always has the same Message-ID, so mail programs can drop a repeat
    digest = hashlib.sha256(f"{notice.key}|{to.email.lower()}".encode()).hexdigest()[:32]
    msg["Message-ID"] = f"<kosha.{digest}@{domain}>"
    msg["Auto-Submitted"] = "auto-generated"
    if notice.kind:
        msg["X-Kosha-Kind"] = _one_line(notice.kind)[:40]
    msg.set_content(f"{notice.text()}\n--\n{FOOTER}\n")
    page = ('<!doctype html><html><body style="margin:0;padding:20px;background:#F3F4F1;color:#16191D;'
            'font-family:Segoe UI,Arial,sans-serif;font-size:14px">'
            '<div style="max-width:620px;margin:0 auto;background:#FFFFFF;border:1px solid #DCDFDC;border-radius:6px;'
            f'padding:20px">{notice.html()}'
            f'<hr style="border:0;border-top:1px solid #DCDFDC;margin:18px 0 10px">'
            f'<p style="margin:0;color:#5A6167;font-size:12px">{FOOTER}</p></div></body></html>')
    msg.add_alternative(page, subtype="html")
    return msg


class EmailNotifier(Notifier):
    """Sends one e-mail to each recipient of a notice that has an address."""

    def __init__(self, ctx: AddinContext | None = None, cfg: dict | None = None):
        super().__init__(ctx or AddinContext("kosha.email", settings=cfg or settings(), log=log))
        self.cfg = {**DEFAULTS, **(cfg if cfg is not None else self.ctx.settings)}

    def _where(self) -> str:
        return f"{self.cfg['host']}:{self.cfg['port']}"

    def _connect(self) -> smtplib.SMTP:
        cfg = self.cfg
        if not cfg.get("host") or not cfg.get("from_address"):
            raise NotifyError("The mail server or the sender address is not set (Admin › Notifications).")
        timeout = float(cfg.get("timeout_s") or 20)
        context = ssl.create_default_context() if cfg.get("verify_tls", True) else ssl._create_unverified_context()
        smtp = None
        try:
            if cfg.get("security") == "ssl":
                smtp = smtplib.SMTP_SSL(cfg["host"], int(cfg["port"]), timeout=timeout, context=context)
            else:
                smtp = smtplib.SMTP(cfg["host"], int(cfg["port"]), timeout=timeout)
            smtp.ehlo()
            if cfg.get("security", "starttls") == "starttls":
                smtp.starttls(context=context)
                smtp.ehlo()
            if cfg.get("username"):
                smtp.login(cfg["username"], cfg.get("password") or "")
            return smtp
        except Exception as e:
            if smtp is not None:
                try:
                    smtp.close()
                except Exception:
                    pass
            raise NotifyError(self._explain(e)) from None

    def _explain(self, e: Exception) -> str:
        where = self._where()
        if isinstance(e, smtplib.SMTPNotSupportedError):
            return (f"The mail server {where} does not offer STARTTLS. Choose SSL/TLS (usually port 465), or None only "
                    "inside a trusted network.")
        if isinstance(e, smtplib.SMTPAuthenticationError):
            return f"The mail server {where} refused the user name or password ({e.smtp_code})."
        if isinstance(e, smtplib.SMTPRecipientsRefused):
            addr, (code, text) = next(iter(e.recipients.items()))
            text = text.decode(errors="replace") if isinstance(text, bytes) else str(text)
            return f"The mail server refused the address {addr} ({code} {_one_line(text)[:120]})."
        if isinstance(e, smtplib.SMTPSenderRefused):
            return f"The mail server refused the sender address {e.sender} ({e.smtp_code})."
        if isinstance(e, smtplib.SMTPResponseException):
            return f"The mail server {where} answered {e.smtp_code}: {_one_line(e.smtp_error.decode(errors='replace'))[:160]}"
        if isinstance(e, (socket.timeout, TimeoutError)) or (
                isinstance(e, smtplib.SMTPServerDisconnected) and "timed out" in str(e)):    # smtplib wraps read time-outs
            return f"The mail server {where} did not answer within {self.cfg.get('timeout_s')} seconds."
        if isinstance(e, ssl.SSLError):
            return (f"The secure connection to the mail server {where} failed ({e.reason or e}). Check the security "
                    "setting and the port.")
        if isinstance(e, smtplib.SMTPServerDisconnected):
            return f"The mail server {where} closed the connection. Check the security setting and the port."
        if isinstance(e, OSError):
            return f"Cannot reach the mail server {where}: {e.strerror or e}."
        return f"Sending e-mail through {where} failed: {e.__class__.__name__}: {e}"

    def health(self) -> Health:
        try:
            smtp = self._connect()
        except NotifyError as e:
            return Health.down(str(e))
        try:
            smtp.quit()
        except Exception:
            pass
        who = f" as {self.cfg['username']}" if self.cfg.get("username") else ""
        return Health.up(f"Connected to {self._where()}{who}.")

    def send(self, notice: Notice) -> None:
        to = [r for r in notice.recipients if r.email and _EMAIL.match(r.email)]
        if not to:
            raise NotifyError("Nobody to send the e-mail to: the person has no valid e-mail address in Kosha.")
        smtp = self._connect()
        try:
            for r in to:
                smtp.send_message(build_message(notice, r, self.cfg), from_addr=self.cfg["from_address"], to_addrs=[r.email])
        except NotifyError:
            raise
        except Exception as e:
            raise NotifyError(self._explain(e)) from None
        finally:
            try:
                smtp.quit()
            except Exception:
                pass


def send(notice: Notice) -> None:
    """Send now with the saved settings (the outbox worker and the test button use this)."""
    EmailNotifier(cfg=settings()).send(notice)
