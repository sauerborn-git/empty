#!/usr/bin/env python3
"""Downloads the latest O2 invoice from the o2online.de customer portal
and forwards it by email via Gmail SMTP.

Usage:
    python o2_invoice_mailer.py [--headless/--debug] [--dry-run]

See README.md for setup instructions and known limitations.
"""

import argparse
import logging
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("o2_invoice_mailer")
STATE_FILE = Path(__file__).with_name(".o2_state.json")  # gespeicherte Browser-Sitzung (enthaelt Cookies!)

# Common OneTrust-style cookie banner selectors used on many German sites,
# including o2online.de at the time of writing. Adjust if the banner changes.
COOKIE_ACCEPT_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "button:has-text('Alle akzeptieren')",
    "button:has-text('Akzeptieren')",
]


def dismiss_cookie_banner(page):
    for selector in COOKIE_ACCEPT_SELECTORS:
        try:
            page.click(selector, timeout=3000)
            log.info("Cookie-Banner geschlossen (%s)", selector)
            return
        except PlaywrightTimeoutError:
            continue
    log.info("Kein Cookie-Banner gefunden oder bereits akzeptiert")


def login(page, username: str, password: str, login_url: str, interactive: bool = False):
    log.info("Oeffne Login-Seite: %s", login_url)
    page.goto(login_url)
    page.wait_for_load_state("networkidle")
    if "login.o2online.de" not in page.url:
        log.info("Bereits eingeloggt (gespeicherte Sitzung)")
        return
    dismiss_cookie_banner(page)

    # Der Login (login.o2online.de) laeuft zweistufig: erst Benutzername,
    # dann Passwort (callback_3_od = Benutzername, callback_4_od = Passwort).
    page.wait_for_selector("input[name='callback_3_od']", timeout=20000)
    page.fill("input[name='callback_3_od']", username)
    page.keyboard.press("Enter")

    page.wait_for_selector("input[name='callback_4_od']", timeout=20000)
    page.fill("input[name='callback_4_od']", password)
    page.keyboard.press("Enter")

    # Warten, bis entweder das Portal geladen ist oder die Sicherheitsabfrage erscheint
    page.wait_for_function(
        "() => !location.host.includes('login.o2online.de') "
        "|| document.body.innerText.includes('Sicherheitsabfrage')",
        timeout=30000,
    )

    if interactive and page.locator("text=Sicherheitsabfrage").count():
        log.info(
            "O2 verlangt einen Sicherheitscode per E-Mail. Bitte im Browser "
            "'Jetzt E-Mail senden' klicken und den Code eingeben (max. 5 Min.)"
        )
        page.wait_for_url(lambda url: "login.o2online.de" not in url, timeout=300000)
        page.wait_for_load_state("networkidle")

    if page.locator("text=Zwei-Faktor").count() or page.locator("text=SMS-Code").count() or page.locator("text=Sicherheitsabfrage").count():
        raise RuntimeError(
            "O2 verlangt eine Zwei-Faktor-Bestaetigung (SMS/App-Code). "
            "Dieses Script kann interaktive 2FA nicht automatisch loesen."
        )

    log.info("Login abgeschlossen")


def download_latest_invoice(page, invoice_url: str, download_dir: Path) -> Path:
    log.info("Oeffne Rechnungsuebersicht: %s", invoice_url)
    page.goto(invoice_url)
    dismiss_cookie_banner(page)
    page.wait_for_load_state("networkidle")

    # Die Uebersicht listet mehrere Rechnungen; die erste ist die aktuellste.
    download_trigger = page.locator("a[data-testid='vt-billing.invoice-download']").first
    download_trigger.wait_for(timeout=30000)

    with page.expect_download() as download_info:
        download_trigger.click()
    download = download_info.value

    download_dir.mkdir(parents=True, exist_ok=True)
    target_path = download_dir / (download.suggested_filename or "o2_rechnung.pdf")
    download.save_as(target_path)
    log.info("Rechnung gespeichert: %s", target_path)
    return target_path


def send_invoice_mail(
    invoice_path: Path,
    gmail_address: str,
    gmail_app_password: str,
    recipients: list[str],
):
    msg = EmailMessage()
    msg["Subject"] = f"O2 Rechnung: {invoice_path.name}"
    msg["From"] = gmail_address
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        "Im Anhang findest du die aktuelle O2-Rechnung, automatisch "
        "heruntergeladen und weitergeleitet."
    )

    with open(invoice_path, "rb") as f:
        msg.add_attachment(
            f.read(),
            maintype="application",
            subtype="pdf",
            filename=invoice_path.name,
        )

    log.info("Sende Mail an %s", recipients)
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(gmail_address, gmail_app_password)
        smtp.send_message(msg)
    log.info("Mail gesendet")


def load_config(need_mail: bool = True) -> dict:
    load_dotenv()
    required = ["O2_USERNAME", "O2_PASSWORD"]
    if need_mail:
        required += ["GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "MAIL_TO"]
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        log.error(
            "Fehlende Konfiguration in .env: %s (siehe .env.example)",
            ", ".join(missing),
        )
        sys.exit(1)

    return {
        "username": os.environ["O2_USERNAME"],
        "password": os.environ["O2_PASSWORD"],
        "login_url": os.getenv(
            "O2_LOGIN_URL", "https://login.o2online.de/signin/?goto=https%3A%2F%2Fwww.o2online.de%2Fmein-o2%2F"
        ),
        "invoice_url": os.getenv(
            "O2_INVOICE_URL", "https://www.o2online.de/mein-o2/rechnung/"
        ),
        "download_dir": Path(os.getenv("DOWNLOAD_DIR", "./downloads")),
        "gmail_address": os.getenv("GMAIL_ADDRESS", ""),
        "gmail_app_password": os.getenv("GMAIL_APP_PASSWORD", ""),
        "mail_to": [a.strip() for a in os.getenv("MAIL_TO", "").split(",") if a.strip()],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Browser sichtbar starten (headed) und langsamer, zum Anpassen der Selektoren",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rechnung herunterladen, aber keine Mail versenden",
    )
    parser.add_argument(
        "--setup",
        action="store_true",
        help="Einmalig: sichtbarer Login inkl. E-Mail-Sicherheitscode, speichert die Sitzung in "
        + STATE_FILE.name,
    )
    args = parser.parse_args()

    config = load_config(need_mail=not (args.dry_run or args.setup))

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not (args.debug or args.setup),
            slow_mo=250 if args.debug else 0,
        )
        context = browser.new_context(
            accept_downloads=True,
            storage_state=str(STATE_FILE) if STATE_FILE.exists() and not args.setup else None,
        )
        page = context.new_page()
        try:
            login(
                page, config["username"], config["password"], config["login_url"],
                interactive=args.setup,
            )
            if args.setup:
                context.storage_state(path=str(STATE_FILE))
                STATE_FILE.chmod(0o600)
                log.info("Sitzung gespeichert: %s", STATE_FILE)
                return
            invoice_path = download_latest_invoice(
                page, config["invoice_url"], config["download_dir"]
            )
        except Exception:
            if args.debug:
                screenshot_path = config["download_dir"] / "debug_error.png"
                config["download_dir"].mkdir(parents=True, exist_ok=True)
                page.screenshot(path=screenshot_path)
                log.error("Screenshot des Fehlerzustands: %s", screenshot_path)
            raise
        finally:
            browser.close()

    if args.dry_run:
        log.info("--dry-run gesetzt, es wird keine Mail versendet")
        return

    send_invoice_mail(
        invoice_path,
        config["gmail_address"],
        config["gmail_app_password"],
        config["mail_to"],
    )


if __name__ == "__main__":
    main()
