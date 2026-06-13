# SecureTrust Bank

A full-featured **European** online banking application built with **Django**, plain
**HTML/CSS/JavaScript** (no frontend frameworks). Accounts use **IBANs** and **BIC/SWIFT**
codes, payments are **SEPA** credit transfers, and balances are held in **euro (€)**.
It ships with two portals:

- **Customer internet banking** (`/`) — accounts, transfers, bills, cards, loans,
  fixed deposits, support and notifications.
- **Staff portal** (`/staff/`) — a complete custom rewrite of the admin side.
  Every piece of bank data is created and updated here; there is no external
  integration or external clearing house — tellers, managers and administrators
  post everything manually with a full audit trail.

## Quick start

```bash
pip install -r requirements.txt
python manage.py migrate
python manage.py seed_bank        # account types, loan products, billers + default admin
python manage.py runserver
```

Default administrator (change the password immediately):

| Email | Password |
|---|---|
| `admin@securetrustbank.com` | `ChangeMe123!` |

Open `http://127.0.0.1:8000/` for the customer site, sign in with a staff account
to be routed to `http://127.0.0.1:8000/staff/`.

### Reaching the app from another device (CSRF)

If you open the app at anything other than `http://localhost:8000` — for example a
phone on your LAN at `http://192.168.1.5:8000`, or via an HTTPS tunnel — Django's
CSRF protection needs that origin to be trusted, otherwise form submissions fail.
Set it before starting the server:

```bash
export DJANGO_CSRF_TRUSTED_ORIGINS="http://192.168.1.5:8000"
python manage.py runserver 0.0.0.0:8000
```

(`localhost` and `127.0.0.1` are trusted by default.) If a token does expire while
a page is left open, the app now shows a friendly "session timed out" page with a
link to sign in again instead of a raw 403.

## Features

### Account lifecycle & status
- **Every new account starts as _pending approval_** — whether opened by a customer
  self-registering or by staff. It cannot transact until a manager/administrator
  reviews and approves it from the staff portal (a teller can open but not approve).
- **Frozen accounts** can still be signed into and viewed in full by the customer,
  but **no transactions are possible** (transfers, bills, deposits, withdrawals,
  fixed deposits and loan repayments are all blocked at the posting layer). Staff
  freeze/unfreeze from the account page.
- Other statuses: active, dormant, closed (closing requires a zero balance).

### Customer portal
- **Self-registration** with KYC details; a current account with its own IBAN is
  created instantly and held pending approval.
- **Transaction PIN** (hashed, 4-digit) required for every money movement;
  locks for 30 minutes after 3 wrong attempts.
- **Dashboard** with balances, 30-day money in/out, quick actions and recent activity.
- **SEPA transfers** by IBAN with live beneficiary-name lookup (AJAX) and IBAN
  checksum validation, daily transfer limits per account type, beneficiary saving
  and printable receipts.
- **Beneficiary management.**
- **Bill payments** to staff-managed billers (electricity, TV, airtime…).
- **Fixed deposits** — 30/90/180/365 days at tiered rates; early break forfeits interest.
- **Loans** — browse products, apply (KYC-verified customers only), track repayment
  progress, repay from any account.
- **Cards** — request Verve/Visa/Mastercard, freeze/unfreeze or block with PIN confirmation.
- **Statements** — filterable, CSV download, print-friendly receipts.
- **Support tickets** with threaded conversations.
- **In-app notifications + email alerts** for every credit, debit and account event.
- Profile management, KYC document upload, password & PIN change, password reset.

### Staff portal (custom admin)
Role-based access: **Teller → Manager → Administrator**.

- **Operations dashboard** — totals, today's volume, pending queues, 7-day volume chart
  (vanilla `<canvas>`, no chart library).
- **Customers** — search, create (with profile, account, opening deposit and optional
  instant KYC), edit, deactivate, open extra accounts.
- **KYC queue** — review uploaded documents, verify or reject with a reason
  (customer is notified).
- **Account approvals** — a dedicated queue (and dashboard tile) for accounts
  awaiting review; approve to activate.
- **Accounts** — search by IBAN/name, cash deposits/withdrawals (teller posting)
  **with a custom reason and value date** (back- or forward-dated), manual ledger
  adjustments and **set-an-exact-balance** (admin only, posted as an audited
  adjustment), one-click freeze/unfreeze, status changes, close, full ledger.
- **Transactions** — global search/filter, one-click reversal with mandatory reason.
- **Cards** — issue requested cards (generates number/CVV/expiry), block cards.
- **Loans** — approve & disburse (credits the customer instantly) or reject with a
  note, record branch cash repayments.
- **Fixed deposits** — pay out matured deposits or break early.
- **Tickets** — reply, assign, set status.
- **Configuration (admin)** — account types, loan products and billers are all
  data-driven and editable in the portal.
- **Staff management (admin)** — create tellers/managers/admins, deactivate accounts.
- **Audit log** — every sensitive action (logins, postings, reversals, KYC and loan
  decisions…) with actor and IP address.

## Engineering notes

- **Posting engine** (`banking/services.py`): every balance change happens inside
  `transaction.atomic()` with `select_for_update()` row locks; transfers write a
  debit and credit leg sharing one reference, and `balance_after` is recorded on
  every ledger entry so statements always reconcile.
- **Security**: hashed transaction PINs with lockout, login throttling with account
  lockout, 15-minute idle session timeout, role-gated views, per-object ownership
  checks, CSRF everywhere, password validators, full audit trail.
- **Money** is `Decimal` end-to-end and quantized to 2 places.
- The default Django admin remains available at `/django-admin/` as a maintenance
  fallback only.

## Tests

```bash
python manage.py test
```

32 tests covering the posting engine (SEPA transfers, daily limits, minimum
balance, reversals), IBAN generation/validation, the pending-approval and freeze
status gates, admin set-balance, fixed deposits, loan disbursement/repayment,
PIN lockout and view-level security (ownership, role gates, PIN-gated transfers).

## Project layout

```
config/          settings & root urls
accounts/        custom user (email login), customer profile/KYC, audit log
banking/         accounts, ledger, transfers, fixed deposits, bills, seed command
cards/           debit card lifecycle
loans/           products, applications, disbursement, repayments
support/         tickets
notifications/   in-app + email alerts
staffportal/     the custom staff/admin portal
templates/       server-rendered HTML (base, auth, customer/, staff/)
static/          css/main.css, js/main.js (vanilla)
```
