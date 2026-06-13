# Production deployment

`deploy/deploy.sh` provisions a complete, hardened production stack for
SecureTrust Bank on a fresh **Ubuntu 22.04 or 24.04** server. It is interactive:
run it once, answer the prompts, and it configures everything.

## What it sets up

| Component | Detail |
|-----------|--------|
| **App server** | Gunicorn behind a Unix socket, managed by systemd with sandboxing (`ProtectSystem=strict`, `NoNewPrivileges`, dropped capabilities, …) |
| **Database** | PostgreSQL, created locally, listening on `127.0.0.1` only, with a strong auto-generated password |
| **Web server** | Nginx terminating TLS, HTTP→HTTPS redirect, security headers, static/media serving, `server_tokens off` |
| **TLS** | Cloudflare Origin Certificate, Let's Encrypt (DNS or HTTP-01), or a self-signed fallback |
| **Cloudflare** | Optional automatic DNS A-record creation via API; real-visitor-IP restoration; origin locked to Cloudflare IP ranges |
| **Firewall** | UFW — default deny inbound, SSH allowed, 80/443 restricted to Cloudflare ranges when Cloudflare is used |
| **Intrusion** | fail2ban on SSH |
| **Patching** | unattended-upgrades for security updates |
| **Secrets** | Django `SECRET_KEY` auto-generated; all secrets written to `/etc/onlinebank/onlinebank.env` (mode 640) |
| **Recurring jobs** | A daily systemd timer runs `manage.py process_standing_orders` |

The app itself runs with `DEBUG=0`; the settings module refuses to start in that
mode if the secret key, hosts, or email backend were left at insecure defaults.

## Prerequisites

1. A fresh Ubuntu server with a public IPv4 address and root/sudo access.
2. A domain you control (on Cloudflare or elsewhere).
3. **(Recommended) Cloudflare:**
   - **Origin Certificate** — in the Cloudflare dashboard go to **SSL/TLS → Origin
     Server → Create Certificate**, and copy the certificate and private key. The
     script will prompt you to paste both. Afterwards set **SSL/TLS → Overview →
     Full (strict)**.
   - **(Optional) API token** for automatic DNS — **My Profile → API Tokens →
     Create Token → Edit zone DNS**, scoped to your zone. You'll also need the
     **Zone ID** from the domain's Overview page.

## Running it

```bash
git clone <your-repo-url> ~/onlineBank
cd ~/onlineBank
sudo bash deploy/deploy.sh
```

Answer the prompts (domain, admin login, SMTP, Cloudflare). When it finishes,
your bank is live at `https://<your-domain>/`.

## Re-running / updating code

The script is **idempotent** — pull new code and run it again to redeploy:

```bash
cd ~/onlineBank && git pull && sudo bash deploy/deploy.sh
```

Or, for a quick restart after a code change you've already `rsync`ed:

```bash
sudo systemctl restart onlinebank
```

## Useful commands

```bash
systemctl status onlinebank                 # service health
journalctl -u onlinebank -f                  # live application logs
systemctl list-timers | grep onlinebank      # next standing-order run
sudo -u onlinebank /opt/onlinebank/venv/bin/python \
     /opt/onlinebank/app/manage.py process_standing_orders   # run now
```

## Notes & follow-ups

- **SMTP:** if you skipped it, add the `DJANGO_EMAIL_*` variables to
  `/etc/onlinebank/onlinebank.env` and `systemctl restart onlinebank`.
- **Uploaded KYC documents** are served from `/media/`. They sit behind
  Cloudflare and the firewall, but are not individually access-controlled; if you
  store real identity documents, put them behind an authenticated media view or
  object storage with signed URLs before going live with real customers.
- **Backups:** set up regular `pg_dump` backups of the PostgreSQL database and
  the `media/` directory.
