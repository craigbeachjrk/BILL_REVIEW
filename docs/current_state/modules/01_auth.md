# Module 1 — Auth (Authentication & Role-Based Access Control)

**Scope of review:**
- `auth.py` — 282 lines — the auth library module
- `main.py:1562-1615` — auth helpers, require_user, require_admin
- `main.py:3169-3428` — login, logout, change-password, user CRUD endpoints
- `templates/login.html`, `templates/change_password.html`, `templates/users.html`, `templates/admin.html`
- `tests/unit/test_auth.py`, `tests/integration/test_api_auth.py`
- DDB table `jrk-bill-review-users` + `role-index` GSI

**Reviewer notes:** This is the smallest and most self-contained of the 14 modules. Good baseline for the JTBD-lens review pattern.

---

## 1. Module Purpose (Business)

The auth module's one job: **ensure only authorized JRK employees access the bill review tool, and that they only see/do what their role allows**. Secondary jobs: let admins manage user accounts; let users change their own passwords.

Financial application — an AP clerk posting a fraudulent invoice would move real money. The access-control system is a compliance boundary, not just a convenience feature.

## 2. User Personas & Roles

From `auth.py:16-48`, four roles are defined:

| Role ID | Display Name | What They Can Do (per ROLES config) |
|---|---|---|
| `System_Admins` | System Administrator | Everything (`*` permission) |
| `UBI_Admins` | UBI Administrator | Read/write UBI + billback + config; access `/ubi`, `/ubi_mapping`, `/uom_mapping`, `/review`, `/config`, `/track`, `/debug` |
| `Utility_APs` | Utility AP Specialist | Read bills, submit bills, process invoices, read reports; access `/`, `/review`, `/invoices`, `/track` |
| `HR_Admins` | HR Administrator | HR read/write/export; **no pages allowed** (pages=[]) |

**⚠️ Drift with AUTHENTICATION_SETUP.md** (now in `docs/archive/2025-11/`): the archived doc describes this role system as the canonical ACL mechanism. Current code **does not actually use** roles for ACL — see Section 4 / Issue ISSUE-001.

## 3. End-to-End Workflow Walkthrough

### 3a. Login (first-time user)
1. User visits any URL → redirected to `/login` (via `require_user` raising 307)
2. `/login` renders `templates/login.html` with email + password fields
3. User submits form → POST `/login`
4. `auth.authenticate(username, password)` called:
   - `get_user(user_id)` → DDB `jrk-bill-review-users` by `user_id`
   - Check `enabled == True` else reject
   - `verify_password()` using bcrypt
   - If pass, update `last_login_utc` in DDB
5. If `must_change_password` flag set → redirect to `/change-password` (with session cookie set)
6. Else → redirect to `/` (home, landing.html)
7. Login event also written to `jrk-bill-drafts` table with key `login#{username}#{timestamp}`

### 3b. Login (returning user)
Same as 3a but `must_change_password` is False so redirect goes to `/`.

### 3c. Change password (forced)
1. After first login, user hits `/change-password`
2. Form has: current_password, new_password, confirm_password
3. On submit:
   - Validate new == confirm
   - Validate new length ≥ 8
   - **Re-authenticate via `auth.authenticate(user_id, current_password)`** to verify current password (side effect: this updates last_login_utc)
   - `auth.update_password()` — writes new hash, clears `must_change_password`
4. Redirect to `/`

### 3d. Change password (voluntary)
Same as 3c but user initiated from somewhere in UI.

### 3e. Logout
1. User POSTs to `/logout`
2. Cookie deleted
3. Redirect to `/login`

### 3f. Admin creates user
1. Admin visits `/config/users`
2. `auth.get_user(admin_user)` called; checked `role == "System_Admins"` or 403
3. Page renders `users.html` with list of all users
4. Admin fills form, submits → POST `/api/users` with {user_id, password, role, full_name}
5. Same admin check
6. Validations: all fields required, role in ROLES, password ≥ 8 chars
7. `auth.create_user()` → DDB put_item with ConditionExpression to prevent duplicates

### 3g. Admin disables/enables/resets password/changes role
Four separate endpoints, each with duplicated admin-check code. Payload varies.

---

## 4. 🚨 Clunkiness / Workflow Gaps

This is where the JTBD lens really matters. Let me enumerate.

### 4a. Two admin systems that don't align → **[ISSUE-001]**
Two separate "admin" concepts exist and aren't synchronized:

**System 1:** `auth.py` `ROLES["System_Admins"]` — role stored in DDB `jrk-bill-review-users.role`
**System 2:** `main.py:177` — hardcoded Python set `ADMIN_USERS = {"tma@jrk.com", "cbeach@jrk.com", "claude-qa@jrk.com"}`

- `require_admin()` (main.py:1605) uses hardcoded set
- `/api/users/*` endpoints (main.py:3286-3415) use **role-based check** via `auth.get_user().role == "System_Admins"`
- ~28 other endpoints scattered through main.py use the hardcoded set

**Consequence:** a user promoted to `System_Admins` via the UI **cannot perform admin-gated operations on non-user endpoints** (billback archive, exclusion hash cleanup, debug endpoints, etc.). They can only manage other users.

Conversely, a user in `ADMIN_USERS` set but with role `Utility_APs` CAN perform most admin ops but CANNOT manage users. 

**User Job Affected:** "Grant a new employee admin access" — must be done in BOTH systems (DDB role + deploy a main.py change to add to hardcoded set) OR the admin they create is only half-admin.

### 4b. `auth.py` permission functions are dead code → **[ISSUE-002]**
- `has_permission(user_role, permission)` — `auth.py:186-204` — **never called from main.py**
- `can_access_page(user_role, page_path)` — `auth.py:207-218` — **never called from main.py**

Searched entire codebase (`grep -r`) — only references are in auth.py itself. The permission framework exists in memory but nothing uses it. Access control reduces to "authenticated or not" (for most endpoints) and "in ADMIN_USERS set or not" (for admin endpoints).

**User Job Affected:** "Restrict UBI team from reviewing non-UBI bills" — technically impossible; everyone authenticated sees everything.

### 4c. Duplicated admin check in every /api/users endpoint → **[ISSUE-003]**
All 6 user-management API endpoints repeat:
```python
user_data = auth.get_user(admin_user)
if not user_data or user_data.get("role") != "System_Admins":
    return JSONResponse({"error": "Access denied"}, status_code=403)
```

This should be a FastAPI dependency like `require_system_admin`. Every duplication is an opportunity for a future endpoint to forget the check.

**User Job Affected:** None in happy path, but security regressions likely.

### 4d. No password complexity beyond length-8 → **[ISSUE-004]**
Only rule: `len(new_password) >= 8`. No upper/lower/symbol/number requirement, no dictionary check, no check that new ≠ old.

### 4e. No password history → **[ISSUE-005]**
User can reset to the same password they just had. `update_password` doesn't store history.

### 4f. No failed-login lockout → **[ISSUE-006]**
Unlimited login attempts. Brute-force risk.

### 4g. No session timeout on inactivity → **[ISSUE-007]**
Cookie max age is 7 days, no idle-timeout. A user who logs in and walks away is logged in for the week.

### 4h. No 2FA / MFA → **[ISSUE-008]**
For a financial application with posting authority, this is a meaningful gap.

### 4i. No self-service password reset → **[ISSUE-009]**
"Forgot password" flow doesn't exist. Only path is: ask admin to reset via `/api/users/{user_id}/reset-password`, then admin tells the user their temporary password out-of-band (phone/in-person/Slack). Password resets + role changes funnel through a manual IT process.

**User Job Affected:** "I forgot my password at 10pm Sunday" — unsolvable until Monday when admin is reachable.

### 4j. Emergency auth bypass is a production backdoor → **[ISSUE-010]**
`main.py:1570-1581`: if env vars `DISABLE_AUTH=1` AND `DISABLE_AUTH_SECRET="I-UNDERSTAND-THIS-IS-INSECURE"` are both set, auth is bypassed. The bypass logs "[SECURITY WARNING] Auth bypass active" but doesn't disable itself. Anyone with AppRunner env var access can toggle this.

**Additionally:** The confirmation phrase is literally in the source code — leakage resistance = zero. A real emergency bypass pattern uses a short-lived signed token validated against a HMAC secret, or ideally just redeploys with temporary hardcoded credentials.

### 4k. `change-password` side-effects `last_login_utc` → **[ISSUE-011]**
Current password verification happens via `auth.authenticate()`, which updates `last_login_utc` as a side effect. So changing your password makes it look like you logged in. Minor but creates confusing audit trail.

### 4l. HR_Admins role has pages=[] → **[ISSUE-012]**
`auth.py:46`: HR_Admins role has empty pages list — and permissions mention hr:read/hr:write/hr:export but nothing in the codebase checks these permissions. This role is a placeholder with no behavior. Either remove or implement.

### 4m. `/api/users/{user_id}/role` only accepts 3 roles → **[ISSUE-013]**
Endpoint hardcodes valid roles: `{"System_Admins", "UBI_Admins", "Utility_APs"}` (main.py:3400). Missing `HR_Admins`. Should read from `auth.ROLES` as source of truth.

### 4n. List users uses DDB Scan → **[ISSUE-014]** (PERF)
`auth.list_users()` defaults to full table scan. Currently the user set is tiny so no issue, but the pattern is wrong — should query `role-index` GSI.

### 4o. Login event written to wrong table → **[ISSUE-015]** (TECH-DEBT)
Login success writes to `jrk-bill-drafts` table with key `login#{username}#{timestamp}`. Drafts table is for invoice edits; co-locating login events pollutes the PK space and complicates TTL/retention policy. Should be its own table or at least a different key prefix in a dedicated config table.

### 4p. No CSRF protection on logout → **[ISSUE-016]** (SECURITY)
POST `/logout` has no CSRF token. A malicious site that knows a user's cookie domain could cause log-out. Low impact (just forced logout) but a real CSRF issue for state-changing POST.

Also: many state-changing endpoints (`/api/users`, `/api/*/archive`, etc.) lack CSRF tokens. Broader concern; to be reviewed across modules.

### 4q. No audit log of admin actions → **[ISSUE-017]** (COMPLIANCE)
Admin creates/disables/role-changes a user — no persistent audit trail beyond `print()` to logs. A financial app should have an immutable admin-action log.

---

## 5. Integration Gaps

### 5a. Auth doesn't gate module access → **[ISSUE-018]** (INTEGRATION)
`can_access_page()` exists but isn't called. The implication: any authenticated user can navigate to `/master-bills`, `/ubi`, `/review-checks`, `/admin`, etc. This was presumably designed to be role-gated but the wiring was never done.

Individual admin endpoints self-gate via `if user not in ADMIN_USERS: ...`, but pages don't. A Utility_APs user landing on `/admin` sees the dashboard.

### 5b. Multiple auth mechanisms in play
- Session cookie (for human browser traffic)
- No API key / bearer token (for programmatic access)
- No service account role (Lambda → app)
- Smoke test uses `claude-qa@jrk.com` service account (per memory `reference_service_account.md`)

If the scraper import Lambda or the autonomy simulator wanted to call an app endpoint, there's no clean path — it'd have to emulate a browser session.

### 5c. VE module auth override
`main.py:1602`: `_ve_web.require_user = require_user` — VE module's auth is monkey-patched at import. Works but fragile; if VE module evolves to use a different auth interface, will silently break.

---

## 6. Feature Inventory (UI vs. what works)

| UI element | Exists? | Works end-to-end? | Note |
|---|---|---|---|
| `/login` page | ✅ | ✅ | |
| `/logout` | ✅ | ✅ | No CSRF |
| `/change-password` (self) | ✅ | ✅ | Returns to `/` on success |
| `/config/users` (admin) | ✅ | ✅ | Gated only by role check |
| Create user | ✅ | ✅ | 4-field form |
| Disable user | ✅ | ✅ | |
| Enable user | ✅ | ✅ | |
| Admin-reset password | ✅ | ✅ | Must communicate new password out-of-band |
| Change user's role | ✅ | ✅ | (But only to one of 3 roles, not 4) |
| Forgot password (self-service) | ❌ | — | Not implemented |
| Email verification | ❌ | — | |
| 2FA | ❌ | — | |
| SSO / SAML / OIDC | ❌ | — | |
| Audit log UI | ❌ | — | Just logs |
| Permission-based page visibility | ❌ | — | Code exists, never called |
| Per-role nav menu | ❌ | — | Everyone sees same menu |
| User activity timeline | ❌ | — | Login events logged but not exposed |

---

## 7. Technical Implementation

### 7a. `auth.py` (282 lines)

Clean, narrow module. Public interface:
- `ROLES` dict (config)
- `hash_password(password) -> str` — bcrypt
- `verify_password(password, hash) -> bool`
- `get_user(user_id) -> Dict|None`
- `create_user(user_id, password, role, full_name, created_by) -> bool`
- `update_password(user_id, new_password, clear_must_change=True) -> bool`
- `authenticate(user_id, password) -> Dict|None`
- `has_permission(user_role, permission) -> bool` — **dead**
- `can_access_page(user_role, page_path) -> bool` — **dead**
- `list_users(role=None) -> List[Dict]`
- `disable_user(user_id) -> bool`
- `enable_user(user_id) -> bool`

**DDB operations:** uses low-level `boto3.client("dynamodb")` with explicit type annotations `{"S": ..., "BOOL": ...}` rather than resource-level. Consistent but verbose.

**Error handling:** everything returns `None` or `False` on error, with a `print()` for logging. No exceptions raised to callers. This means callers can't tell "user not found" from "DDB threw" — failure mode is lossy.

### 7b. `main.py` auth surface
- `APP_SECRET` (main.py:102) — env var with insecure default + warning
- `SESSION_COOKIE = "br_sess"` — fixed name
- `SESSION_MAX_AGE_SECONDS = 7 * 24 * 3600` = 604800 (7 days)
- `SECURE_COOKIES = "1"` by default (good)
- `ADMIN_USERS = {"tma@jrk.com", "cbeach@jrk.com", "claude-qa@jrk.com"}` — hardcoded
- `signer = TimestampSigner(APP_SECRET)` — itsdangerous
- `set_session`, `get_current_user`, `require_user`, `require_admin`

### 7c. Endpoints covered (9)
- `GET /login` → `login_form` (main.py:3169)
- `POST /login` → `login` (main.py:3174) — authenticates, sets cookie, redirects
- `GET /change-password` → `change_password_form` (main.py:3219)
- `POST /change-password` → `change_password` (main.py:3225)
- `GET /config/users` → `users_page` (main.py:3266)
- `GET /api/users` → `api_list_users` (main.py:3285)
- `POST /api/users` → `api_create_user` (main.py:3296)
- `POST /api/users/{user_id}/disable` → (main.py:3328)
- `POST /api/users/{user_id}/enable` → (main.py:3341)
- `POST /api/users/{user_id}/reset-password` → (main.py:3354)
- `POST /api/users/{user_id}/role` → (main.py:3386)
- `POST /logout` → (main.py:3424)
- `GET /health` → (main.py:3418) — no auth

### 7d. Password handling
- Bcrypt via `bcrypt.hashpw()` — standard, secure
- `verify_password` catches exceptions and returns False — safe

### 7e. Session token
- `itsdangerous.TimestampSigner` — HMAC-signed timestamp+payload
- Decent but:
  - No token rotation on privilege change
  - Can't revoke individual sessions (would need token allowlist/denylist)
  - Secret shared across AppRunner instances (OK, but couple to APP_SECRET env var)

---

## 8. Data Touchpoints

### DDB: `jrk-bill-review-users`
- **Primary key:** `user_id` (string) — the email address
- **GSI:** `role-index` — PK=`role` — for role-based queries
- **Attributes:** `password_hash`, `role`, `full_name`, `enabled` (bool), `must_change_password` (bool), `created_utc`, `last_login_utc`, `created_by`, `password_changed_utc`
- **NOTE:** This table was NOT in the initial DDB inventory in `04_data_architecture.md` — needs to be added. Also not in `CLAUDE.md` DDB table list. Drift.

### DDB: `jrk-bill-drafts`
- Login events written to this table with PK `login#{username}#{timestamp}` — abuse of table semantics.

### DDB: `jrk-bill-config`
- Not touched by auth code directly.

### Environment variables
- `APP_SECRET` (load-bearing; weak default "dev-secret-change-me" with warning)
- `ADMIN_USER` (fallback for auth bypass mode; defaults to "admin")
- `DISABLE_AUTH`, `DISABLE_AUTH_SECRET` — backdoor
- `SESSION_COOKIE` — implicit via constant, not env
- `AWS_REGION` — for DDB client
- `USERS_TABLE` — env var, defaults to `jrk-bill-review-users`
- `SECURE_COOKIES` — defaults to "1"

---

## 9. Drift vs. Existing Docs

| Claim | Source | Code reality | Verdict |
|---|---|---|---|
| "Role-based ACL system with 4 roles" | `docs/archive/2025-11/AUTHENTICATION_SETUP.md` | 4 roles exist in code, but ACL not wired up (permissions dead code) | 🔴 DRIFT |
| "System_Admins gets all pages" | same | Partially — some pages check `ADMIN_USERS` not role | 🔴 DRIFT |
| "UBI_Admins pages: /, /ubi, /ubi_mapping, etc." | same | Code doesn't enforce page restrictions at all; all authenticated users see all pages | 🔴 DRIFT |
| "Global Secondary Index: role-index" | same | ✅ GSI exists, used by `list_users(role=...)` | Accurate |
| CLAUDE.md's DDB table list | `CLAUDE.md:38-45` | `jrk-bill-review-users` missing from list | 🔴 DRIFT |
| `04_data_architecture.md`'s table inventory | Our new doc | Same missing | 🔴 DRIFT (inherited from upstream data) |

---

## 10. Issues Flagged (summary)

Will be propagated to `ISSUES.md`:

| ID | Severity | Scope | Title |
|---|---|---|---|
| ISSUE-001 | P1 | INTEGRATION/JTBD | Two admin systems (ADMIN_USERS set vs System_Admins role) don't align |
| ISSUE-002 | P2 | DEAD | `has_permission` and `can_access_page` are dead code |
| ISSUE-003 | P3 | TECH-DEBT | Admin check duplicated in every /api/users endpoint (no `require_system_admin` dep) |
| ISSUE-004 | P2 | SECURITY | No password complexity beyond length-8 |
| ISSUE-005 | P3 | SECURITY | No password history check |
| ISSUE-006 | P2 | SECURITY | No failed-login lockout (brute-force) |
| ISSUE-007 | P2 | SECURITY | No idle session timeout |
| ISSUE-008 | P1 | SECURITY | No 2FA for financial application |
| ISSUE-009 | P2 | JTBD | No self-service forgot-password |
| ISSUE-010 | P1 | SECURITY | Auth bypass via env vars is dangerous backdoor |
| ISSUE-011 | P4 | BUG | change-password side-effects last_login_utc |
| ISSUE-012 | P4 | DEAD | HR_Admins role has pages=[] — dead role |
| ISSUE-013 | P3 | BUG | /api/users/{id}/role only accepts 3 of 4 roles |
| ISSUE-014 | P3 | PERF | list_users uses DDB Scan by default |
| ISSUE-015 | P3 | TECH-DEBT | Login events written to drafts table (wrong table) |
| ISSUE-016 | P2 | SECURITY | No CSRF protection on state-changing POSTs |
| ISSUE-017 | P2 | COMPLIANCE | No audit log of admin actions (beyond stdout) |
| ISSUE-018 | P1 | JTBD/INTEGRATION | Pages have no role-based access control |

Top 3 to prioritize if fixing auth module first:
1. **ISSUE-001** (P1): unify admin systems
2. **ISSUE-018** (P1): wire up page-level ACL using existing ROLES config
3. **ISSUE-010** (P1): remove env-var auth bypass (or replace with proper break-glass)

---

## 11. Open Questions for User

**[Q-1]** **Role-based ACL intent:** Was the `ROLES` + `can_access_page` framework intended to be used for page-level access control? If yes, we should wire it up. If no (and admin-only is the real model), we should delete the dead code. Which is it?

**[Q-2]** **Hardcoded ADMIN_USERS vs System_Admins role:** Is there a reason the hardcoded set exists separately from the role system? (E.g., "these are the only 3 people who can break glass"?) Or is it historical from before the role system was added?

**[Q-3]** **HR_Admins role:** Is this actually used? If not, can we delete it?

**[Q-4]** **2FA:** Is 2FA a requirement? Compliance obligation? User preference? Or out of scope for now?

**[Q-5]** **Self-service password reset:** Would email-based reset fit the JRK workflow? (Would need SES integration and reset-token handling.)

**[Q-6]** **Session duration:** 7 days is long for a financial app. Should we add idle timeout? What would break workflows you care about?

**[Q-7]** **Break-glass pattern:** What's the intended use of `DISABLE_AUTH=1`? If it's "cbeach needs to recover access when locked out", there are better patterns (per-user IAM-level reset via AWS CLI). Can we delete?

**[Q-8]** **Admin action audit log:** Any compliance obligation? Monthly review by IT?

**[Q-9]** **Service accounts:** `claude-qa@jrk.com` is the smoke-test user. Should automated callers (Lambda, scheduled tasks) have distinct service-account credentials, or continue impersonating a human?

---

## 12. Dead / Unused Code

- `auth.can_access_page()` — never called
- `auth.has_permission()` — never called
- `ROLES["HR_Admins"]` — role defined but no endpoint checks for it, and `/api/users/{id}/role` doesn't accept it
- `ROLES["UBI_Admins"].pages` — list defined but `can_access_page` never called so list is unused data
- `ROLES["UBI_Admins"].permissions` — list defined but `has_permission` never called

---

## Observations for the Current-State Synthesis

1. **This module exemplifies the "clunkiness" theme:** Features are half-built (role-based ACL designed, never wired). The UI lets admins manage roles but the roles don't actually gate access. Two admin systems coexist without the user being told which one matters for which action.

2. **Gap between intent and implementation:** The `AUTHENTICATION_SETUP.md` design doc describes a role-based system. The code ships a ~50% implementation of it. Classic "design complete, implementation partial" drift.

3. **Low blast-radius fixes available:** Many issues here are small code changes. Unifying the admin check is a function + refactor. Wiring up `can_access_page` is middleware. 2FA requires a real implementation.

---

## References

- `../02_endpoint_inventory.md` — endpoint table
- `../03_module_taxonomy.md` — Module 1 block
- `../04_data_architecture.md` — DDB tables (needs `jrk-bill-review-users` added)
- `../../archive/2025-11/AUTHENTICATION_SETUP.md` — original design doc (drift source)
