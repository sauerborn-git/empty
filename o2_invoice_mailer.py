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


def login(page, username: str, password: str, login_url: str):
    log.info("Oeffne Login-Seite: %s", login_url)
    page.goto(login_url)
    dismiss_cookie_banner(page)

    # NOTE: These selectors are best-effort placeholders. This script was
    # written without live access to o2online.de (network egress to the
    # site was blocked in the environment it was authored in), so verify
    # and adjust them against the real page before relying on this script.
    # Run with --debug to open a headed browser and inspect the DOM
    # (right-click a field -> Untersuchen) to find the correct selectors.
    page.fill("#username", username)  # TODO: verify field selector
    page.fill("#password", password)  # TODO: verify field selector
    page.click("button[type='submit']")  # TODO: verify submit button selector

    page.wait_for_load_state("networkidle")

    if page.locator("text=Zwei-Faktor").count() or page.locator("text=SMS-Code").count():
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

    # TODO: verify the selector for the "download latest invoice" control.
    # A common pattern is a link/button labelled "PDF" or "Rechnung
    # herunterladen" next to the most recent billing period entry.
    download_trigger = page.locator(
        "a:has-text('PDF'), button:has-text('Rechnung herunterladen')"
    ).first

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


def load_config() -> dict:
    load_dotenv()
    required = [
        "O2_USERNAME",
        "O2_PASSWORD",
        "GMAIL_ADDRESS",
        "GMAIL_APP_PASSWORD",
        "MAIL_TO",
    ]
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
            "O2_LOGIN_URL", "https://www.o2online.de/mein-o2/login/"
        ),
        "invoice_url": os.getenv(
            "O2_INVOICE_URL", "https://www.o2online.de/mein-o2/rechnung/"
        ),
        "download_dir": Path(os.getenv("DOWNLOAD_DIR", "./downloads")),
        "gmail_address": os.environ["GMAIL_ADDRESS"],
        "gmail_app_password": os.environ["GMAIL_APP_PASSWORD"],
        "mail_to": [addr.strip() for addr in os.environ["MAIL_TO"].split(",")],
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
    args = parser.parse_args()

    config = load_config()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=not args.debug,
            slow_mo=250 if args.debug else 0,
        )
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()
        try:
            login(page, config["username"], config["password"], config["login_url"])
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
