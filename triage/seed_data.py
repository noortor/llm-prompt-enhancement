"""
Synthetic bug-triage dataset, organized into "confusion themes": clusters of
reports that share a systematic way a zero-shot model tends to misjudge them
(tone-based severity miscalibration, or a component boundary that isn't
obvious from surface wording). Each theme has instances in BOTH the train
split (reviewers correct these) and the eval split (held out, gold-only), so
a correction made on a train example gives the retrieval step something
genuinely relevant to find when the eval example is triaged later.
"""

# (title, description, split, gold_severity, gold_component)
REPORTS = [
    # Theme 1: politely-worded but serious (data loss / security)
    (
        "Small thing - lost some data",
        "Hi team, just a heads up, I was editing a customer record earlier and "
        "after saving, all the notes I'd added over the past few weeks seem to "
        "have disappeared. Not urgent, whenever you get a chance.",
        "train", "Critical", "Database",
    ),
    (
        "Quick note about export",
        "Hey, not a big deal, but I noticed the nightly export job silently "
        "drops any row where the email field has more than one '@' symbol, and "
        "support tickets about missing invoices trace back to this. Happens "
        "for every affected account.",
        "train", "High", "Billing",
    ),
    (
        "Minor observation",
        "Just noticed that when I reset my password, the confirmation email "
        "includes the temporary password in plain text in the subject line, "
        "visible in notification previews. Probably nothing but flagging it.",
        "eval", "Critical", "Auth",
    ),
    # Theme 2: urgent-sounding but cosmetic
    (
        "URGENT!!! Site completely broken",
        "This is critical, the whole site is unusable! The 'Submit' button on "
        "the settings page is 2px lower than the label above it and it looks "
        "terrible on my monitor.",
        "train", "Low", "Frontend",
    ),
    (
        "CRITICAL BUG - please fix ASAP",
        "Extremely urgent, dropping everything. The footer copyright year "
        "still says 2024 instead of 2025 on the marketing site.",
        "train", "Low", "Frontend",
    ),
    (
        "EMERGENCY - breaks everything",
        "This absolutely breaks the whole experience, need a hotfix "
        "immediately. The loading spinner icon is slightly off-center on the "
        "dashboard while data loads.",
        "eval", "Low", "Frontend",
    ),
    # Theme 3: Auth vs API, login endpoint 500s, disambiguated by root cause
    (
        "Login endpoint returning 500s for some users",
        "POST /api/v2/login is throwing 500 errors intermittently. Looking at "
        "logs, it happens specifically when the password hash comparison "
        "throws because of a null salt value on older accounts migrated in "
        "2022.",
        "train", "High", "Auth",
    ),
    (
        "Intermittent 500 on login route",
        "Our monitoring shows POST /api/v2/login has a 3% error rate over the "
        "last 24h. Digging in, it looks like the shared rate-limiter "
        "middleware (used by every endpoint, not just login) is throwing when "
        "the Redis connection pool runs out under load.",
        "train", "High", "API",
    ),
    (
        "Users can't log in - 500 errors",
        "Getting reports that login fails with a server error. Traced it to "
        "the session token generator crashing when a user's account has more "
        "than one active MFA device registered.",
        "eval", "High", "Auth",
    ),
    # Theme 4: Billing vs Database, invoice bug caused by a migration
    (
        "Invoice totals incorrect after migration",
        "Since the database migration last week, several customers are "
        "seeing invoice totals that don't match their subscription tier. "
        "Support is fielding billing disputes.",
        "train", "High", "Billing",
    ),
    (
        "Wrong invoice amounts for enterprise tier",
        "Enterprise customers on annual plans are being charged the monthly "
        "rate on their latest invoice. Started right after last week's schema "
        "migration, but the actual bug is in the pricing calculation query.",
        "train", "High", "Billing",
    ),
    (
        "Customer billed incorrect amount",
        "A customer forwarded an invoice showing $0.00 due despite having an "
        "active paid subscription. Started after the recent data migration.",
        "eval", "High", "Billing",
    ),
    # Theme 5: deploy-related slowness is a user-facing Performance issue
    (
        "App feels slow since Tuesday's deploy",
        "Since Tuesday's release, page load times have roughly doubled across "
        "the dashboard. Users are complaining pages take 4-5 seconds to "
        "render instead of about 1.5s.",
        "train", "Medium", "Performance",
    ),
    (
        "Everything is sluggish after last deploy",
        "Multiple teams reporting the whole product feels laggy since "
        "yesterday's deploy - clicking anything has a noticeable delay before "
        "it responds.",
        "train", "Medium", "Performance",
    ),
    (
        "Slowness across the board since release",
        "Since this morning's release, every page in the app takes several "
        "seconds longer to load than usual, and it's affecting everyone.",
        "eval", "Medium", "Performance",
    ),
    # Theme 6: intermittent/flaky bugs land at Medium, not Low or High
    (
        "Search sometimes returns nothing",
        "About 1 in 10 searches on the product catalog page return zero "
        "results even for terms that definitely exist. Refreshing and "
        "searching again usually works.",
        "train", "Medium", "Search",
    ),
    (
        "Intermittent empty search results",
        "Randomly, searching for a customer by email returns 'no results "
        "found' even though the customer exists. Doesn't happen every time, "
        "maybe a caching issue.",
        "train", "Medium", "Search",
    ),
    (
        "Flaky search behavior",
        "Search occasionally comes back empty for valid queries, then works "
        "fine a moment later if you try again.",
        "eval", "Medium", "Search",
    ),
    # Theme 7: blocking-for-one-customer is High, annoying-for-many is Low
    (
        "Enterprise customer can't export reports",
        "Our largest enterprise customer (Acme Corp) reports that clicking "
        "'Export to CSV' on their usage report shows a loading spinner for "
        "about 30 seconds, then the page just resets with no file and no "
        "error message. They need this for a board meeting tomorrow.",
        "train", "High", "API",
    ),
    (
        "Some users see delayed push notifications",
        "A handful of users mentioned notifications arrive 10-15 minutes late "
        "sometimes. Not blocking anything, just annoying.",
        "train", "Low", "Notifications",
    ),
    (
        "Key customer blocked from exporting data",
        "One of our paying customers says the 'Download Report' feature has "
        "been spinning indefinitely for two days and they can't get their "
        "monthly data out for a compliance deadline.",
        "eval", "High", "API",
    ),
    # Theme 8: "password" in the text doesn't automatically mean Critical/Auth
    (
        "Password field visible on mobile",
        "On the iOS app, the password field on the login screen briefly shows "
        "the typed characters in plain text for about half a second before "
        "masking them, instead of masking immediately as you type.",
        "train", "Low", "Mobile",
    ),
    (
        "Password shown in plaintext briefly",
        "When creating a new password in account settings on the web app, "
        "the 'confirm password' field shows plain text for a split second "
        "when you paste instead of type.",
        "train", "Low", "Frontend",
    ),
    (
        "Password field display issue",
        "On the Android app, the password input on the signup screen shows "
        "the last character typed unmasked for a moment, same as most apps "
        "do, but a user flagged it as a security concern.",
        "eval", "Low", "Mobile",
    ),
    # Theme 9: Notifications (delivery) vs Auth (auth-service logic) for 2FA
    (
        "Users not receiving 2FA code",
        "Multiple users report the 6-digit code email for two-factor login "
        "never arrives. Checked the email provider dashboard and our "
        "transactional emails for 2FA are being throttled due to hitting our "
        "daily sending quota.",
        "train", "High", "Notifications",
    ),
    (
        "2FA email delayed for many accounts",
        "Support says 2FA emails are taking 20+ minutes to arrive for a "
        "growing number of accounts. Our email queue worker has been falling "
        "behind since traffic increased this week.",
        "train", "Medium", "Notifications",
    ),
    (
        "Can't complete login - 2FA code missing",
        "Several users say they never receive the 2FA code needed to finish "
        "logging in. Investigating shows the auth service is failing to "
        "enqueue the 2FA code generation job due to a bug in the retry logic "
        "after a recent auth service deploy.",
        "eval", "High", "Auth",
    ),
    # Theme 10: Mobile vs Frontend, disambiguated by the platform named
    (
        "Button unresponsive on iOS app",
        "On the iOS app, tapping the 'Save' button on the profile screen does "
        "nothing the first time; you have to tap it twice.",
        "train", "Medium", "Mobile",
    ),
    (
        "Save button needs two clicks on the web dashboard",
        "On the web dashboard (tested in Chrome and Safari), clicking 'Save' "
        "on the profile page the first time does nothing; a second click "
        "actually saves.",
        "train", "Medium", "Frontend",
    ),
    (
        "Have to tap Save twice in the app",
        "Using the Android app, saving profile changes requires tapping the "
        "Save button twice - the first tap seems to be ignored.",
        "eval", "Medium", "Mobile",
    ),
    # Filler: straightforward, unambiguous reports (sanity-check the baseline
    # and make sure improvement on the tricky themes doesn't cost accuracy here)
    (
        "Complete outage - API returning 503 for all requests",
        "Every API call across all endpoints has been returning 503 Service "
        "Unavailable for the last 10 minutes. No customers can use the "
        "product at all right now.",
        "train", "Critical", "API",
    ),
    (
        "Database connection pool exhausted",
        "The main database is refusing new connections; the app is throwing "
        "'connection pool exhausted' errors and nothing is loading for any "
        "user.",
        "train", "Critical", "Database",
    ),
    (
        "Typo in footer text",
        "The word 'recieve' is misspelled in the footer's privacy policy "
        "link text. Should be 'receive'.",
        "train", "Low", "Frontend",
    ),
    (
        "Search filter dropdown has wrong label",
        "The 'Sort by' dropdown on the search results page is labeled 'Sort "
        "by' but the options listed are actually filter options, not sort "
        "options.",
        "train", "Low", "Search",
    ),
    (
        "Notification bell icon shows wrong count",
        "The unread notifications badge sometimes shows a count of 1 higher "
        "than the actual number of unread notifications in the dropdown.",
        "train", "Medium", "Notifications",
    ),
    (
        "Infra: one availability zone down",
        "One of our three availability zones went down for 20 minutes "
        "overnight; traffic failed over automatically and there was no "
        "customer-visible impact, but on-call got paged.",
        "train", "Medium", "Infra",
    ),
    (
        "Mobile app crashes on launch for some devices",
        "The iOS app is crashing immediately on launch for users on iOS 15 "
        "devices specifically, per crash reports. Users on iOS 16+ are "
        "unaffected.",
        "train", "High", "Mobile",
    ),
    (
        "Billing charged customers twice",
        "A batch billing job ran twice last night due to a scheduler "
        "misconfiguration, and roughly 200 customers were charged twice for "
        "their subscription.",
        "eval", "Critical", "Billing",
    ),
    (
        "Dashboard chart rendering slow with large datasets",
        "For accounts with more than 100k rows of data, the analytics "
        "dashboard chart takes 8-10 seconds to render, compared to under 1 "
        "second for smaller accounts.",
        "eval", "Medium", "Performance",
    ),
    (
        "Deployment pipeline failing intermittently",
        "About 1 in 5 deploys fail at the build step with an out-of-memory "
        "error in CI, requiring a manual retry. No customer impact, just "
        "slows down releases.",
        "eval", "Low", "Infra",
    ),
]


def seed(conn) -> int:
    """Insert seed reports if the table is empty. Returns the number inserted."""
    existing = conn.execute("SELECT COUNT(*) AS n FROM bug_reports").fetchone()["n"]
    if existing > 0:
        return 0

    conn.executemany(
        """
        INSERT INTO bug_reports (title, description, split, gold_severity, gold_component)
        VALUES (?, ?, ?, ?, ?)
        """,
        REPORTS,
    )
    return len(REPORTS)
