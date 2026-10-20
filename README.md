# CrossMatch Browser 6.32.64

> Secure first-install bootstrap release. Existing accounts and ordinary administrator-approved registration are preserved.

> Regression-tested maintenance release: stricter invalid CSV-header validation, date-condition parsing for Excel datetime values, and a private-data-free distribution. Name/DOB row editors, matching modes, account approvals, and exports are preserved. See the separate hard-test report for workload coverage and limits.


## One-time first administrator key (v6.32.64)

**First-time Windows setup (`START_DOCKER.bat`)**

1. On a fresh installation, `START_DOCKER.bat` calls `GENERATE_SETUP_KEY.bat`, which uses Windows PowerShell's cryptographic random-number generator to generate a **64-character hexadecimal key** and save it to the local `.env` file as `CROSSMATCH_SETUP_KEY`. The key is also displayed in the server's Windows console. It never appears in the public app HTML or API.
2. The first administrator opens `/setup`, enters their username, display name, password (at least 10 characters), and the **one-time setup key**. Submit **Create administrator**.
3. The app creates the admin account and atomically marks first-time setup complete in the SQLite database. The setup key **cannot be reused**, even if someone later deletes the last user row while retaining the database. The password and key are distinct.
4. Subsequent users select **Create account** on the sign-in page and stay **Pending Approval** until an administrator approves them. They cannot sign in before approval.

**Server setup and protection**

- Keep `.env` private; do not upload, commit, or email it. Keep the first-admin key confidential and access setup over HTTPS when using a remote browser. After creating the first administrator, you may remove the `CROSSMATCH_SETUP_KEY` line from `.env` and restart the Docker stack; existing accounts are unaffected. Do not remove other `.env` settings.
- If you start Docker Compose directly (without `START_DOCKER.bat`), **first** run `GENERATE_SETUP_KEY.bat` on your Windows server, or provide your own cryptographically random `CROSSMATCH_SETUP_KEY` of 32–256 ASCII letters/numbers, `_` or `-` through the server environment or `.env` file. The server **refuses first-admin creation** when the key is absent or invalid.
- `GENERATE_SETUP_KEY.bat` preserves an existing valid key and existing `.env` values; it does not rotate or overwrite an existing key. For an unusual state where a database file already exists but has no accounts, run the generator manually if necessary.
- The API enforces the key server-side, checks it using constant-time comparison, and limits invalid attempts to 8 within 15 minutes per client IP. The setup is one-time and an attempted second bootstrap is rejected, even with the correct key.
- **Upgrades:** keep your existing `database`, `certificates`, `.env`, and Compose customizations. Existing administrators log in normally; they do not need to regenerate a key. Existing SQLite authentication records and prior-approved users are preserved.
- If the account database was initialized then all user records were deleted, first-time setup remains blocked; restore an account database backup or use appropriate local recovery. Deleting the database to reactivate setup risks losing accounts and audit history and is **not** recommended.
- `.env` is never included in this distribution ZIP or Docker image. The Windows key generator is not run or tested in this Linux test environment; it should be verified on a Windows machine before production use.

## Account page usability update (v6.32.63)

- Sign-in page uses a clear **Create account** action with separate notice that administrator approval is required.
- Registration shows three stages: enter details, administrator review, and sign in after approval.
- Password fields have accessible show/hide controls, inline password confirmation feedback, and useful field hints.
- The success screen explains that the request was received, does not guarantee approval, and tells the applicant to contact an administrator for status.
- The existing registration endpoint, duplicate-request privacy behavior, administrator queue, login permissions and database are unchanged.

## Hard-tested maintenance fixes (v6.32.62)

- **Malformed CSV replacement:** reject files whose detected column headers are empty or contain binary/control characters; keep the previously accepted upload when the replacement is invalid.
- **Advanced date conditions:** evaluate Excel-staged datetime text (e.g. `2025-01-20 00:00:00`) as a valid calendar date, including common datetime timestamp formats in CSV.
- **Release hygiene:** this ZIP does **not** contain any previously deployed SQLite authentication database or TLS key/certificate. On a new install, the first admin can be created as usual, and the nginx certificate helper generates a local certificate. On an upgrade, preserve your existing database, certificates and any local `.env`.

The hard-test evidence includes 500,000-record and 1,000,000-record synthetic CSV runs, 1,000,000-record Excel workbook conversion, smaller fuzzy/swap and cross-file runs, varied condition cases, and browser control tests. Testing is not a guarantee for all input variants or hardware.

## Start

Install Docker Desktop, select Linux containers, and start Docker Desktop.
Extract this ZIP into a folder, then run **START_DOCKER.bat**. The first build
needs internet access. No Python installation is required on Windows.
Create the first administrator account with the one-time server setup key when the browser opens.

Default addresses: http://127.0.0.1:18090 and https://localhost:18444.
HTTPS uses a locally generated certificate. Configure ports and settings using
`.env.example` as the template for an optional `.env` file.

## Included helpers

- START_DOCKER.bat: build, start, verify the version, and open the app.
- OPEN_APP.bat: reopen an already running app.
- STOP_DOCKER.bat: stop the app without deleting its stored accounts.
- RESET_ADMIN.bat: recover an existing administrator account using Docker.

Diagnostics: run `docker compose logs --tail 100` in this folder.
Status: run `docker compose ps`.

## Upgrade safely

Keep your existing `database`, `certificates`, `.env`, and any customized
Compose settings. Replace application files and run START_DOCKER.bat to rebuild.
The registration-request tables are automatically added to the existing SQLite database. Do not delete existing data folders just
because they are not included in this clean distribution.

## Account registration and admin approval

After the first administrator has been created, the **Sign in** page has a
**Create account** link. Applicants enter a username,
full name and password. Submission creates only a pending request; it does not
create an active user or permit sign-in. The password is stored as a salted
scrypt hash, never in plaintext. An administrator opens **Administration →
Pending account approvals**, verifies the applicant, and chooses **Approve**
(to create a standard-user account) or **Reject**. The applicant can sign in
with the submitted password only after approval. This workflow does not send
emails or automatic approval notifications.

Direct administrator-created accounts continue to work without requests.
Registration requests and decisions are retained in the existing local SQLite
database and audit trail. The new tables are created automatically on startup
in the existing database; do not delete or replace the `database` directory.
The public request form has rate limits but does not include CAPTCHA, so avoid
exposing this app to an untrusted public network without additional protection.

## Row-specific header editing (v6.32.61)

Under **Unified Header Mapping & Rules**, select **Edit** on the **Name** row to
edit only detected Name headers (Full Name, First, Middle, Last, Suffix) or
**Edit** on the **DOB** row to edit only DOB headers (Full Birthdate, Birth
Month/Day/Year). Only headers detected and assigned to that group appear.
The top-level **Edit headers** button still shows all columns for reviewing
uncertain or incorrectly classified headers.

Inside the mapping pop-up, **+ Add condition** beside a header creates a new
condition for that specific column, then opens the existing **Rule Builder**
so you can choose the operator, value and result. In Cross Match mode it
prefills the corresponding File B comparison where available. A rule is not
completed until its remaining settings are configured. These are *optional*
advanced checks; they do not modify default Name/DOB duplicate identity
matching. Existing rule profiles are not overwritten automatically.

## Conditions

Upload File A, review detected headers, and open Rules & saved setups.
Enable Conditions and add text, numeric, date, or missing-value checks.
Cross Match also supports File B checks and comparisons on accepted A/B pairs.

Rules in the same group run in priority order. Each rule's AND/OR selection
combines it with the previous result in that group. The first rule's selection
has no effect. Separate groups trigger independently. File B-only checks must
use a separate group from File A and A/B checks; invalid mixed groups now show
an error instead of silently evaluating incomplete groups.

Accepted duplicate matches remain Duplicate even when a rule adds a Review
finding. The exported Reason records that finding. A Duplicate action cannot
create an identity match by itself. Review findings override Clean requests.

Saving with Conditions OFF preserves individual Enabled selections. Profiles
already saved by older versions with all rules disabled need those selections
restored manually once.

## Age limits

Minimum and maximum age default to **0 (OFF)**. A positive value enables that
bound. Valid out-of-range DOBs generate Review findings. Full DOB and separate
month/day/year mappings are supported. Existing duplicate precedence remains.

## This clean distribution

Contains runtime code, Docker configuration, four useful Windows helpers,
admin recovery, and this guide. Historical notes, duplicate guides, test and
benchmark folders, test dependencies, caches, and redundant diagnostic/test
batch files are excluded. No test accounts, uploaded data, populated database, or TLS private keys/certificates are included.
