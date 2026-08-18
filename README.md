# O2 Rechnung Downloader & Mailer

Loggt sich in das o2online.de Kundenportal ein, laedt die aktuelle Rechnung
als PDF herunter und schickt sie per Mail (Gmail SMTP) weiter.

## Wichtiger Hinweis zu den Selektoren

Dieses Script wurde in einer Sandbox-Umgebung geschrieben, die keinen
Netzwerkzugriff auf o2online.de hatte. Die CSS-Selektoren fuer das
Login-Formular und den Rechnungs-Download-Button in `o2_invoice_mailer.py`
(mit `# TODO` markiert) sind daher **Platzhalter** und muessen einmalig
gegen die echte Seite geprueft und angepasst werden:

1. Script mit `--debug` starten (siehe unten) – der Browser oeffnet sich sichtbar.
2. Im Browser mit Rechtsklick auf das Feld/den Button -> "Untersuchen" das
   tatsaechliche HTML-Element und dessen `id`/Text ermitteln.
3. Die entsprechende Zeile in `o2_invoice_mailer.py` anpassen.

Falls O2 beim Login eine Zwei-Faktor-Bestaetigung (SMS-/App-Code) verlangt,
bricht das Script mit einer klaren Fehlermeldung ab – das kann es nicht
automatisch loesen.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

Konfiguration anlegen:

```bash
cp .env.example .env
```

`.env` ausfuellen:

- `O2_USERNAME` / `O2_PASSWORD`: deine O2-Login-Daten
- `GMAIL_ADDRESS`: die Gmail-Adresse, von der aus versendet wird
- `GMAIL_APP_PASSWORD`: ein App-Passwort (siehe unten), **nicht** dein normales Gmail-Passwort
- `MAIL_TO`: Empfaenger-Adresse(n), kommagetrennt

### Gmail App-Passwort erstellen

1. In deinem Google-Konto die 2-Faktor-Authentifizierung aktivieren
   (falls noch nicht geschehen).
2. Unter https://myaccount.google.com/apppasswords ein neues App-Passwort
   erzeugen und als `GMAIL_APP_PASSWORD` eintragen.

## Ausfuehren

```bash
python o2_invoice_mailer.py --debug   # zum initialen Anpassen der Selektoren, Browser sichtbar
python o2_invoice_mailer.py           # normaler Lauf, headless
python o2_invoice_mailer.py --dry-run # laedt Rechnung herunter, verschickt aber keine Mail
```

## Sicherheitshinweise

- `.env` enthaelt Klartext-Zugangsdaten und ist in `.gitignore` – niemals committen.
- Nur fuer dein eigenes O2-Konto verwenden.
- Automatisiertes Einloggen kann gegen die Nutzungsbedingungen von O2
  verstossen – Nutzung auf eigenes Risiko.
