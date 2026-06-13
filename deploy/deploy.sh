#!/usr/bin/env bash
#
# SecureTrust Bank — one-shot production deployment for Ubuntu (22.04 / 24.04).
#
# Run this ONCE on a fresh Ubuntu server as root (or with sudo). It will prompt
# for everything it needs and then provision a hardened production stack:
#
#   PostgreSQL  ·  Gunicorn (systemd)  ·  Nginx (TLS)  ·  UFW  ·  fail2ban
#   ·  unattended security upgrades  ·  daily standing-order processing
#   ·  Cloudflare DNS + origin TLS (optional, automated)
#
# Usage:
#   git clone <your-repo> /root/onlineBank
#   cd /root/onlineBank
#   sudo bash deploy/deploy.sh
#
set -euo pipefail

# --------------------------------------------------------------------------- #
#  Constants & helpers
# --------------------------------------------------------------------------- #
APP_NAME="onlinebank"
RUN_USER="onlinebank"
BASE="/opt/${APP_NAME}"
APP_DIR="${BASE}/app"
VENV="${BASE}/venv"
ENV_DIR="/etc/${APP_NAME}"
ENV_FILE="${ENV_DIR}/${APP_NAME}.env"
LOG_DIR="/var/log/${APP_NAME}"
SOCK="/run/${APP_NAME}.sock"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_SRC="$(cd "${SCRIPT_DIR}/.." && pwd)"

c_bold=$'\033[1m'; c_green=$'\033[32m'; c_yellow=$'\033[33m'; c_red=$'\033[31m'; c_reset=$'\033[0m'
say()  { printf '%s\n' "${c_bold}==>${c_reset} $*"; }
ok()   { printf '%s\n' "${c_green}  ✓${c_reset} $*"; }
warn() { printf '%s\n' "${c_yellow}  ! ${c_reset} $*"; }
die()  { printf '%s\n' "${c_red}  ✗ $*${c_reset}" >&2; exit 1; }

ask() {  # ask "Prompt" "default" -> echoes answer
  local prompt="$1" default="${2:-}" reply
  if [[ -n "$default" ]]; then
    read -rp "$(printf '%s [%s]: ' "$prompt" "$default")" reply
    printf '%s' "${reply:-$default}"
  else
    read -rp "$(printf '%s: ' "$prompt")" reply
    printf '%s' "$reply"
  fi
}
ask_secret() {  # ask_secret "Prompt" -> echoes answer (hidden input)
  local prompt="$1" reply
  read -rsp "$(printf '%s: ' "$prompt")" reply; echo >&2
  printf '%s' "$reply"
}
ask_yn() {  # ask_yn "Prompt" "y|n" -> returns 0 for yes
  local prompt="$1" default="${2:-y}" reply
  reply="$(ask "$prompt (y/n)" "$default")"
  [[ "$reply" =~ ^[Yy] ]]
}
gen_secret() { python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(64))
PY
}

[[ $EUID -eq 0 ]] || die "Please run this script as root (use: sudo bash deploy/deploy.sh)."
[[ -f "${PROJECT_SRC}/manage.py" ]] || die "Could not find manage.py — run this from inside the cloned repository."

# --------------------------------------------------------------------------- #
#  1. Collect configuration
# --------------------------------------------------------------------------- #
say "SecureTrust Bank deployment — gathering details"
echo "  Press Enter to accept the [default] shown in brackets."
echo

DOMAIN="$(ask 'Primary domain (e.g. bank.example.com)')"
[[ -n "$DOMAIN" ]] || die "A domain is required."
INCLUDE_WWW="n"
if [[ "$DOMAIN" != www.* ]] && ask_yn "Also serve www.${DOMAIN}?" "y"; then
  INCLUDE_WWW="y"
fi

DETECTED_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || hostname -I | awk '{print $1}')"
SERVER_IP="$(ask 'This server public IPv4 address' "$DETECTED_IP")"

echo
say "Django administrator (the staff super-user you'll log in as)"
ADMIN_EMAIL="$(ask 'Admin email')"
[[ -n "$ADMIN_EMAIL" ]] || die "Admin email is required."
ADMIN_PASSWORD="$(ask_secret 'Admin password (min 8 chars)')"
[[ ${#ADMIN_PASSWORD} -ge 8 ]] || die "Admin password must be at least 8 characters."

echo
say "Database (PostgreSQL, created locally on this server)"
DB_NAME="$(ask 'Database name' "${APP_NAME}")"
DB_USER="$(ask 'Database user' "${APP_NAME}")"
DB_PASSWORD="$(ask_secret 'Database password (blank = auto-generate strong one)')"
[[ -n "$DB_PASSWORD" ]] || { DB_PASSWORD="$(gen_secret | tr -d '/+=' | cut -c1-32)"; ok "Generated a strong database password."; }

echo
say "Outbound email (SMTP) — used for password resets and customer notifications"
CONFIGURE_SMTP="n"
EMAIL_HOST=""; EMAIL_PORT="587"; EMAIL_USER=""; EMAIL_PASSWORD=""; EMAIL_TLS="1"; EMAIL_SSL="0"
DEFAULT_FROM="no-reply@${DOMAIN}"
if ask_yn "Configure a real SMTP server now?" "y"; then
  CONFIGURE_SMTP="y"
  EMAIL_HOST="$(ask 'SMTP host (e.g. smtp.sendgrid.net)')"
  EMAIL_PORT="$(ask 'SMTP port' '587')"
  EMAIL_USER="$(ask 'SMTP username')"
  EMAIL_PASSWORD="$(ask_secret 'SMTP password / API key')"
  DEFAULT_FROM="$(ask 'From address' "no-reply@${DOMAIN}")"
  if [[ "$EMAIL_PORT" == "465" ]]; then EMAIL_TLS="0"; EMAIL_SSL="1"; fi
else
  warn "Skipping SMTP — emails will be written to the server log until you set it later."
fi

echo
say "Cloudflare"
USE_CF="n"; TLS_MODE="selfsigned"; CF_API_TOKEN=""; CF_ZONE_ID=""
if ask_yn "Is this domain on Cloudflare?" "y"; then
  USE_CF="y"
  echo "  TLS options behind Cloudflare:"
  echo "    1) Cloudflare Origin Certificate  (recommended — paste it, set CF SSL mode to 'Full (strict)')"
  echo "    2) Let's Encrypt via certbot      (needs a Cloudflare API token for the DNS challenge)"
  TLS_CHOICE="$(ask 'Choose 1 or 2' '1')"
  [[ "$TLS_CHOICE" == "2" ]] && TLS_MODE="letsencrypt" || TLS_MODE="cforigin"

  if ask_yn "Automatically create/update the DNS A record via the Cloudflare API?" "y"; then
    CF_API_TOKEN="$(ask_secret 'Cloudflare API token (Zone:DNS:Edit)')"
    CF_ZONE_ID="$(ask 'Cloudflare Zone ID (Overview tab, right sidebar)')"
  fi
  if [[ "$TLS_MODE" == "letsencrypt" && -z "$CF_API_TOKEN" ]]; then
    CF_API_TOKEN="$(ask_secret 'Cloudflare API token for the DNS challenge (Zone:DNS:Edit)')"
  fi
else
  if ask_yn "Obtain a free Let's Encrypt certificate directly (domain must already point here)?" "y"; then
    TLS_MODE="letsencrypt-http"
  else
    warn "No TLS provider chosen — a self-signed certificate will be installed (browsers will warn)."
  fi
fi

SECRET_KEY="$(gen_secret)"

ALLOWED_HOSTS="$DOMAIN"
CSRF_ORIGINS="https://${DOMAIN}"
if [[ "$INCLUDE_WWW" == "y" ]]; then
  ALLOWED_HOSTS="${ALLOWED_HOSTS},www.${DOMAIN}"
  CSRF_ORIGINS="${CSRF_ORIGINS},https://www.${DOMAIN}"
  SERVER_NAMES="${DOMAIN} www.${DOMAIN}"
else
  SERVER_NAMES="${DOMAIN}"
fi

echo
say "Review"
cat <<REVIEW
  Domain ............ ${SERVER_NAMES}
  Server IP ......... ${SERVER_IP}
  Admin login ....... ${ADMIN_EMAIL}
  Database .......... ${DB_NAME} (user ${DB_USER}, local PostgreSQL)
  SMTP .............. $([[ "$CONFIGURE_SMTP" == y ]] && echo "${EMAIL_HOST}:${EMAIL_PORT}" || echo "not configured")
  Cloudflare ........ $([[ "$USE_CF" == y ]] && echo "yes" || echo "no")
  TLS ............... ${TLS_MODE}
  Install path ...... ${APP_DIR}
REVIEW
ask_yn "Proceed with installation?" "y" || die "Aborted by user."

# --------------------------------------------------------------------------- #
#  2. System packages
# --------------------------------------------------------------------------- #
say "Installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3 python3-venv python3-dev build-essential \
  postgresql postgresql-contrib libpq-dev \
  nginx ufw fail2ban unattended-upgrades curl rsync ca-certificates >/dev/null
ok "Base packages installed."

# --------------------------------------------------------------------------- #
#  3. Application user, code, virtualenv
# --------------------------------------------------------------------------- #
say "Creating service user and copying application"
id -u "$RUN_USER" &>/dev/null || adduser --system --group --home "$BASE" --disabled-login "$RUN_USER" >/dev/null
mkdir -p "$APP_DIR" "$ENV_DIR" "$LOG_DIR"
rsync -a --delete \
  --exclude '.git' --exclude '*.pyc' --exclude '__pycache__' \
  --exclude 'venv' --exclude '.venv' --exclude 'db.sqlite3' \
  --exclude 'staticfiles' \
  "${PROJECT_SRC}/" "${APP_DIR}/"
ok "Code copied to ${APP_DIR}."

say "Building Python virtual environment"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --upgrade pip wheel --quiet
"$VENV/bin/pip" install -r "${APP_DIR}/requirements.txt" --quiet
ok "Dependencies installed."

# --------------------------------------------------------------------------- #
#  4. PostgreSQL
# --------------------------------------------------------------------------- #
say "Configuring PostgreSQL"
systemctl enable --now postgresql >/dev/null 2>&1 || true
sudo -u postgres psql -v ON_ERROR_STOP=1 >/dev/null <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
    CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';
  ELSE
    ALTER ROLE ${DB_USER} WITH PASSWORD '${DB_PASSWORD}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${DB_NAME} OWNER ${DB_USER}'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '${DB_NAME}')\gexec
ALTER ROLE ${DB_USER} SET client_encoding TO 'utf8';
ALTER ROLE ${DB_USER} SET default_transaction_isolation TO 'read committed';
ALTER ROLE ${DB_USER} SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
SQL
ok "Database ${DB_NAME} ready (listening on localhost only)."

# --------------------------------------------------------------------------- #
#  5. Production environment file
# --------------------------------------------------------------------------- #
say "Writing environment file ${ENV_FILE}"
{
  echo "# Generated by deploy.sh on $(date -u +%FT%TZ). Keep this file secret."
  echo "DJANGO_DEBUG=0"
  echo "DJANGO_SECRET_KEY=${SECRET_KEY}"
  echo "DJANGO_ALLOWED_HOSTS=${ALLOWED_HOSTS}"
  echo "DJANGO_CSRF_TRUSTED_ORIGINS=${CSRF_ORIGINS}"
  echo "DJANGO_BEHIND_PROXY=1"
  echo "DJANGO_DB_NAME=${DB_NAME}"
  echo "DJANGO_DB_USER=${DB_USER}"
  echo "DJANGO_DB_PASSWORD=${DB_PASSWORD}"
  echo "DJANGO_DB_HOST=127.0.0.1"
  echo "DJANGO_DB_PORT=5432"
  echo "DJANGO_DEFAULT_FROM_EMAIL=${DEFAULT_FROM}"
  if [[ "$CONFIGURE_SMTP" == "y" ]]; then
    echo "DJANGO_EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend"
    echo "DJANGO_EMAIL_HOST=${EMAIL_HOST}"
    echo "DJANGO_EMAIL_PORT=${EMAIL_PORT}"
    echo "DJANGO_EMAIL_HOST_USER=${EMAIL_USER}"
    echo "DJANGO_EMAIL_HOST_PASSWORD=${EMAIL_PASSWORD}"
    echo "DJANGO_EMAIL_USE_TLS=${EMAIL_TLS}"
    echo "DJANGO_EMAIL_USE_SSL=${EMAIL_SSL}"
  fi
} > "$ENV_FILE"
chown root:"$RUN_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"
ok "Environment written (mode 640, readable only by root and ${RUN_USER})."

# --------------------------------------------------------------------------- #
#  6. Django: migrate, collectstatic, superuser
# --------------------------------------------------------------------------- #
say "Running migrations and collecting static files"
# Only the core (shell-safe, generated) settings are needed for management
# commands; SMTP credentials are not, so we pass an explicit, quoted env array.
DJ_ENV=(
  DJANGO_DEBUG=0
  DJANGO_SECRET_KEY="$SECRET_KEY"
  DJANGO_ALLOWED_HOSTS="$ALLOWED_HOSTS"
  DJANGO_DB_NAME="$DB_NAME"
  DJANGO_DB_USER="$DB_USER"
  DJANGO_DB_PASSWORD="$DB_PASSWORD"
  DJANGO_DB_HOST=127.0.0.1
  DJANGO_DB_PORT=5432
)
run_dj() { sudo -u "$RUN_USER" env "${DJ_ENV[@]}" "$VENV/bin/python" "$APP_DIR/manage.py" "$@"; }
run_dj migrate --noinput
run_dj collectstatic --noinput >/dev/null
ok "Database migrated and static files collected."

say "Creating / updating the administrator account"
sudo -u "$RUN_USER" env "${DJ_ENV[@]}" DJANGO_SUPERUSER_PASSWORD="$ADMIN_PASSWORD" \
  "$VENV/bin/python" "$APP_DIR/manage.py" shell <<PY
from django.contrib.auth import get_user_model
import os
U = get_user_model()
email = "${ADMIN_EMAIL}"
pw = os.environ["DJANGO_SUPERUSER_PASSWORD"]
u, created = U.objects.get_or_create(email=email, defaults={"is_staff": True, "is_superuser": True, "role": "ADMIN"})
u.is_staff = True; u.is_superuser = True; u.role = "ADMIN"; u.is_active = True
u.set_password(pw); u.save()
print("created" if created else "updated", email)
PY
ok "Administrator ready: ${ADMIN_EMAIL}"

chown -R "$RUN_USER":"$RUN_USER" "$BASE" "$LOG_DIR"

# --------------------------------------------------------------------------- #
#  7. Gunicorn systemd service
# --------------------------------------------------------------------------- #
say "Installing the Gunicorn systemd service"
WORKERS="$(( $(nproc) * 2 + 1 ))"
# systemd's RuntimeDirectory creates and manages /run/onlinebank for the socket.
SOCK="/run/${APP_NAME}/gunicorn.sock"
cat > "/etc/systemd/system/${APP_NAME}.service" <<UNIT
[Unit]
Description=SecureTrust Bank (Gunicorn)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
RuntimeDirectory=${APP_NAME}
ExecStart=${VENV}/bin/gunicorn config.wsgi:application \\
    --workers ${WORKERS} \\
    --bind unix:${SOCK} \\
    --timeout 60 \\
    --access-logfile ${LOG_DIR}/access.log \\
    --error-logfile ${LOG_DIR}/error.log
ExecReload=/bin/kill -s HUP \$MAINPID
Restart=always
RestartSec=3

# --- hardening ---
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${BASE} ${LOG_DIR}
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictNamespaces=true
RestrictSUIDSGID=true
LockPersonality=true
RestrictRealtime=true
CapabilityBoundingSet=
AmbientCapabilities=

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now "${APP_NAME}.service" >/dev/null
sleep 2
systemctl is-active --quiet "${APP_NAME}.service" && ok "Gunicorn is running." || { journalctl -u "${APP_NAME}.service" -n 30 --no-pager; die "Gunicorn failed to start."; }

# --------------------------------------------------------------------------- #
#  8. Standing-order daily timer
# --------------------------------------------------------------------------- #
say "Scheduling daily standing-order processing"
cat > "/etc/systemd/system/${APP_NAME}-standing-orders.service" <<UNIT
[Unit]
Description=Process due standing orders
After=postgresql.service

[Service]
Type=oneshot
User=${RUN_USER}
Group=${RUN_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${VENV}/bin/python ${APP_DIR}/manage.py process_standing_orders
UNIT
cat > "/etc/systemd/system/${APP_NAME}-standing-orders.timer" <<UNIT
[Unit]
Description=Run standing orders every morning

[Timer]
OnCalendar=*-*-* 06:30:00
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now "${APP_NAME}-standing-orders.timer" >/dev/null
ok "Standing orders will run daily at 06:30 (catches up if the server was off)."

# --------------------------------------------------------------------------- #
#  9. TLS certificates
# --------------------------------------------------------------------------- #
say "Setting up TLS"
CERT_DIR="/etc/ssl/${APP_NAME}"
mkdir -p "$CERT_DIR"; chmod 700 "$CERT_DIR"
CERT_PATH="${CERT_DIR}/fullchain.pem"
KEY_PATH="${CERT_DIR}/privkey.pem"
USE_CERTBOT="n"

case "$TLS_MODE" in
  cforigin)
    echo "  Paste your Cloudflare Origin Certificate (PEM), then press Ctrl-D:"
    cat > "$CERT_PATH"
    echo "  Paste the matching Private Key (PEM), then press Ctrl-D:"
    cat > "$KEY_PATH"
    [[ -s "$CERT_PATH" && -s "$KEY_PATH" ]] || die "Certificate or key was empty."
    ok "Cloudflare origin certificate installed. (Set SSL mode to 'Full (strict)' in Cloudflare.)"
    ;;
  letsencrypt)
    apt-get install -y -qq certbot python3-certbot-dns-cloudflare >/dev/null
    install -m 600 /dev/null /root/.cloudflare.ini
    printf 'dns_cloudflare_api_token = %s\n' "$CF_API_TOKEN" > /root/.cloudflare.ini
    certbot certonly --non-interactive --agree-tos -m "$ADMIN_EMAIL" \
      --dns-cloudflare --dns-cloudflare-credentials /root/.cloudflare.ini \
      -d "$DOMAIN" $([[ "$INCLUDE_WWW" == y ]] && echo "-d www.${DOMAIN}") || die "certbot (DNS) failed."
    CERT_PATH="/etc/letsencrypt/live/${DOMAIN}/fullchain.pem"
    KEY_PATH="/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
    USE_CERTBOT="y"
    ok "Let's Encrypt certificate issued via Cloudflare DNS."
    ;;
  letsencrypt-http)
    apt-get install -y -qq certbot >/dev/null
    USE_CERTBOT="y"
    ok "certbot installed — certificate will be requested after Nginx is up."
    ;;
  *)
    openssl req -x509 -nodes -newkey rsa:2048 -days 825 \
      -keyout "$KEY_PATH" -out "$CERT_PATH" \
      -subj "/CN=${DOMAIN}" >/dev/null 2>&1
    warn "Installed a self-signed certificate (browsers will warn). Replace it for real use."
    ;;
esac
chmod 600 "$KEY_PATH" 2>/dev/null || true

# --------------------------------------------------------------------------- #
# 10. Nginx
# --------------------------------------------------------------------------- #
say "Configuring Nginx"
# Restore real visitor IPs from Cloudflare and (optionally) restrict origin to CF.
CF_REALIP_CONF="/etc/nginx/conf.d/cloudflare-realip.conf"
ACCESS_RULES=""
if [[ "$USE_CF" == "y" ]]; then
  {
    echo "# Trust Cloudflare's edge so logs and Django see the real client IP."
    for ip in $(curl -fsS https://www.cloudflare.com/ips-v4 2>/dev/null); do echo "set_real_ip_from ${ip};"; done
    for ip in $(curl -fsS https://www.cloudflare.com/ips-v6 2>/dev/null); do echo "set_real_ip_from ${ip};"; done
    echo "real_ip_header CF-Connecting-IP;"
  } > "$CF_REALIP_CONF"
  # Only allow Cloudflare to reach the origin directly.
  ACCESS_RULES="$(for ip in $(curl -fsS https://www.cloudflare.com/ips-v4) $(curl -fsS https://www.cloudflare.com/ips-v6); do echo "    allow ${ip};"; done; echo "    deny all;")"
  ok "Cloudflare real-IP and origin lockdown configured."
fi

SITE="/etc/nginx/sites-available/${APP_NAME}"
cat > "$SITE" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAMES};
    # ACME http-01 challenge (used only for direct Let's Encrypt)
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 301 https://\$host\$request_uri; }
}

server {
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name ${SERVER_NAMES};

    ssl_certificate     ${CERT_PATH};
    ssl_certificate_key ${KEY_PATH};
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers off;
    ssl_session_timeout 1d;
    ssl_session_cache shared:SSL:10m;

    server_tokens off;
    client_max_body_size 12m;
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options DENY always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

${ACCESS_RULES}

    location /static/ {
        alias ${APP_DIR}/staticfiles/;
        expires 30d;
        access_log off;
    }
    location /media/ {
        alias ${APP_DIR}/media/;
        expires 7d;
    }
    location / {
        proxy_pass http://unix:${SOCK};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_redirect off;
    }

    location ~ /\. { deny all; }
}
NGINX
mkdir -p "${APP_DIR}/media"
chown -R "$RUN_USER":"$RUN_USER" "${APP_DIR}/media"

ln -sf "$SITE" "/etc/nginx/sites-enabled/${APP_NAME}"
rm -f /etc/nginx/sites-enabled/default
nginx -t >/dev/null 2>&1 || { nginx -t; die "Nginx config test failed."; }
systemctl enable nginx >/dev/null 2>&1 || true
systemctl restart nginx
ok "Nginx is serving ${SERVER_NAMES}."

# --------------------------------------------------------------------------- #
# 11. Cloudflare DNS (optional, via API)
# --------------------------------------------------------------------------- #
if [[ "$USE_CF" == "y" && -n "$CF_API_TOKEN" && -n "$CF_ZONE_ID" ]]; then
  say "Creating/updating Cloudflare DNS A record"
  cf_upsert() {
    local name="$1"
    local rec
    rec="$(curl -fsS -X GET \
      "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records?type=A&name=${name}" \
      -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json")"
    local rid
    rid="$(printf '%s' "$rec" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["result"][0]["id"] if d.get("result") else "")')"
    local body
    body="$(printf '{"type":"A","name":"%s","content":"%s","ttl":1,"proxied":true}' "$name" "$SERVER_IP")"
    if [[ -n "$rid" ]]; then
      curl -fsS -X PUT "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records/${rid}" \
        -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json" --data "$body" >/dev/null
    else
      curl -fsS -X POST "https://api.cloudflare.com/client/v4/zones/${CF_ZONE_ID}/dns_records" \
        -H "Authorization: Bearer ${CF_API_TOKEN}" -H "Content-Type: application/json" --data "$body" >/dev/null
    fi
    ok "DNS A record for ${name} -> ${SERVER_IP} (proxied)."
  }
  cf_upsert "$DOMAIN" || warn "Could not update DNS for ${DOMAIN} — check the token/zone."
  [[ "$INCLUDE_WWW" == "y" ]] && { cf_upsert "www.${DOMAIN}" || warn "Could not update www record."; }
fi

# Direct Let's Encrypt (no Cloudflare proxy) needs Nginx running first.
if [[ "$TLS_MODE" == "letsencrypt-http" ]]; then
  say "Requesting Let's Encrypt certificate over HTTP"
  certbot --nginx --non-interactive --agree-tos -m "$ADMIN_EMAIL" --redirect \
    -d "$DOMAIN" $([[ "$INCLUDE_WWW" == y ]] && echo "-d www.${DOMAIN}") \
    && ok "Certificate issued and Nginx updated." \
    || warn "certbot failed — make sure ${DOMAIN} resolves to ${SERVER_IP} on port 80, then run: certbot --nginx -d ${DOMAIN}"
fi

# --------------------------------------------------------------------------- #
# 12. Firewall, fail2ban, auto-updates
# --------------------------------------------------------------------------- #
say "Hardening: firewall, fail2ban, automatic security updates"
ufw --force reset >/dev/null
ufw default deny incoming >/dev/null
ufw default allow outgoing >/dev/null
ufw allow OpenSSH >/dev/null
if [[ "$USE_CF" == "y" ]]; then
  # Only let Cloudflare reach 80/443 at the network layer too.
  for ip in $(curl -fsS https://www.cloudflare.com/ips-v4) $(curl -fsS https://www.cloudflare.com/ips-v6); do
    ufw allow from "$ip" to any port 80 proto tcp >/dev/null
    ufw allow from "$ip" to any port 443 proto tcp >/dev/null
  done
else
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
fi
ufw --force enable >/dev/null
ok "UFW enabled (SSH + $( [[ "$USE_CF" == y ]] && echo 'Cloudflare-only ' )HTTP/HTTPS)."

systemctl enable --now fail2ban >/dev/null 2>&1 || true
cat > /etc/fail2ban/jail.d/sshd.local <<'F2B'
[sshd]
enabled = true
maxretry = 5
bantime = 1h
findtime = 10m
F2B
systemctl restart fail2ban >/dev/null 2>&1 || true
ok "fail2ban protecting SSH."

dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null 2>&1 || true
ok "Unattended security upgrades enabled."

# Renewal hook so Nginx reloads after certbot renews.
if [[ "$USE_CERTBOT" == "y" ]]; then
  mkdir -p /etc/letsencrypt/renewal-hooks/deploy
  printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
  chmod +x /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh
fi

# --------------------------------------------------------------------------- #
#  Done
# --------------------------------------------------------------------------- #
echo
say "${c_green}Deployment complete.${c_reset}"
cat <<DONE

  Your bank is live at:   https://${DOMAIN}
  Staff/admin login:      https://${DOMAIN}/staff/   (${ADMIN_EMAIL})
  Django admin:           https://${DOMAIN}/admin/

  Service management:
    systemctl status ${APP_NAME}
    systemctl restart ${APP_NAME}          # after code changes
    journalctl -u ${APP_NAME} -f           # live logs

  To deploy updated code later:
    cd <repo> && sudo bash deploy/deploy.sh   # safe to re-run (idempotent)

  Secrets live in ${ENV_FILE} (mode 640). Keep it private.
DONE
if [[ "$USE_CF" == "y" ]]; then
  echo "  Cloudflare: set SSL/TLS mode to 'Full (strict)' and enable 'Always Use HTTPS'."
fi
if [[ "$CONFIGURE_SMTP" != "y" ]]; then
  echo "  ${c_yellow}Reminder:${c_reset} SMTP not set — add the DJANGO_EMAIL_* vars to ${ENV_FILE} and restart."
fi
echo
