# DumDev — local X bot

This repository is the DumDev bot. The source checkout may be named
`twitter-agent`; it is not a separate product from the Mac app.

## Mac download and updates

Download from <https://dumdev-eight.vercel.app>. Unzip, move DumDev.app into
Applications, and open it. Python and the browser driver are bundled. Install
Ollama separately, then use guided setup to download `qwen2.5:3b`, select the
browser/account, enter your context/voice/topics and caps, and grant permissions.
Arc supports posts/search/replies with foreground control; Brave/Chrome support
posts only. The dashboard remains at <http://127.0.0.1:8765/> while the app runs.
Quitting the app stops its bot; closing the browser dashboard does not.

Packaged user data lives in `~/Library/Application Support/DumDev/`, separate
from the app bundle, and is preserved when replacing the app for an update.
No maintainer settings, browser profiles, logs, or sessions are bundled.
Only one local instance can use port 8765 at a time.

The app checks the public `downloads/dumdev/latest.json` feed at launch and
hourly. **Check for updates** runs a manual check. A newer version produces a
download banner; users quit and replace the app themselves. This is update
notification, not unattended installation or Apple notarization. Builds are
Apple-silicon-only and ad-hoc signed; macOS may require an explicit Open Anyway
confirmation after you review the download. Never disable Gatekeeper globally.

## Maintainer release console

Install `requirements-build.txt` in the project virtualenv, then run
`npm run release:console` (or double-click `dumdev-dev-console/start.command`).
The local developer console is <http://127.0.0.1:8781/> and is not included in
user app bundles. The source folder should be this Git checkout.

1. Commit and push source changes. The builder refuses dirty source trees and
   archives the latest commit, not a copy of the maintainer's working directory.
2. Choose the next semantic version and click **Build macOS package**. It compiles
   and pixel-tests the native helper, bundles the real bot/dashboard/runtime,
   verifies ad-hoc signing and isolated fresh-install startup, and writes a ZIP
   plus checksum manifest under `release/`.
3. Click **Publish download & update**. This uses the local authenticated Vercel
   CLI and the separate `dumdev` Vercel project. It deploys DumDev's standalone
   static landing page, verified package, and update feed without touching the
   tanny portfolio. The GitHub source repository is public; maintainer settings,
   browser profiles, logs, and sessions are excluded from the package.
4. Verify the public download/checksum/manifest. Installed copies discover the
   newer manifest at their next launch, hourly check, or manual check.

`npm run release:build` stages the version declared in `runtime_paths.py`; the
console allows a newer package version without editing personal configuration.

## Start the configured agent

Run `npm run dev`. No npm dependencies or extra shell flags are needed. It
restarts the local services and opens <http://127.0.0.1:8765/>. A new
installation opens guided setup before starting any automation. Existing
configured installations keep their settings and start normally.
Running the configured bot authorizes automatic posts/replies within your
settings. There are no routine dashboard approval prompts. Keep your logged-in
X tab open in Arc. `--require-review` opts into exact-draft review for manual
runs; `--require-control-approval` separately opts into a dashboard control gate.

The npm command runs the campaign for your configured browser using your
existing `logs/settings.json`, browser session and publication ledger. After
startup, due work begins without an initial delay; daily caps and
discovery spacing still apply. An existing draft is shown for review before
new work, even between scheduled opportunities. Saved undecided drafts resume
automatically; explicit rejections are still respected and verified duplicates
are not republished. `--dry-run` never consumes or publishes saved drafts.
Pending publication outcomes remain blocked for safe reconciliation.

Keep the terminal running. **Ctrl+C** stops both services; `npm run stop`
also stops them. Startup errors are reported in the terminal; detailed worker
logs are in `logs/twitter-agent.out`. The Python virtualenv, requirements,
and local Ollama model (`qwen2.5:3b` by default) must already be installed and
Ollama running. Arc requires macOS and the Swift compiler for its native helper.

## Guided setup

Choose **Run setup** in the dashboard to pause the worker and configure:

1. **Browser and automation account.** Arc supports search, contextual replies,
   and posts with visible foreground takeovers. Brave/Chrome use an isolated
   login and support standalone posts only. Enter the X handle you own.
2. **Your context.** Add a profile/page/project name, description and truthful
   facts, an optional exact HTTPS webpage, and 1–12 interests/search topics.
   Wizard-configured accounts use this context rather than legacy project
   defaults. No website is fetched or treated as verified product knowledge.
3. **Voice and rhythm.** Set the attitude/agent prompt and daily post/reply caps.
   A zero cap disables that surface. Post slots span 09:00–19:00 local time;
   reply search spacing follows the daily reply cap. Caps are not quotas.
   Lowercase, short text, no emoji, factual and publication safeguards still apply.
4. **Connect and permissions.** Open X login in the selected browser and manually
   select/add the account. Credentials are entered only on X. **Check signed-in
   account** verifies the actual account navigation, not a viewed profile.
   Brave/Chrome confirmation is handled in the wizard, not the terminal.
   Arc setup opens Accessibility, Input Monitoring, Screen Recording and
   Automation settings on request, explains the task, and checks available
   permission status. Enable JavaScript from Apple Events in Arc's developer menu.
   Native OS prompts/switches and login challenges must be handled by the user;
   the wizard cannot highlight or click controls inside System Settings.
5. **Review and start.** Finish verifies the account and required Arc permissions,
   saves setup, and starts autonomous publishing. Running the worker authorizes
   publication; no per-draft approval is needed by default. Setup/login checks
   themselves never publish.

Partially saved setup resumes on restart without starting the worker. Viewing
the dashboard without finishing leaves the worker paused. Settings remain local
to this installation, not a multi-user account service. Once there is publication
history or a draft, changing the account handle is refused: use a separate
installation for a different account so history/approvals cannot cross accounts.
Do not distribute `logs/`, browser profiles, or session data with the app.

Existing installations without wizard metadata use DumDev's own source tree
as project context (`TWITTER_AGENT_REPO` may explicitly select another project).
Configured profile fields take precedence, even for legacy settings files.
DumDev is the X automation app; Cruise Control remains a separate HeyClicky
companion. Min/max-gap settings apply to the legacy organic loop; the configured
campaign uses the daily-cap schedule described above.

Validation: `npm test` includes setup validation, account binding, permission
gates, context/voice propagation, and the existing publication regression tests.

The legacy commands below remain available and publish automatically by default.

The launcher uses installed **Brave**, controlled by Playwright, with its own
profile in `browser-profile-brave/`. It does not launch Chrome for Testing.
The same browser executable and profile are used for login and posting.
Arc is not supported by the background launcher; its personal session is untouched.
Normal operation is headless: it never activates Arc or sends desktop input.
It does not copy personal browser sessions, evade challenges, or automate follows.
Background Playwright replies remain disabled. Arc has a separate bounded
contextual discovery/reply flow described below.

## First login

From this directory run `bash start-twitter-agent.sh --login`. Sign in manually
in the dedicated browser, then press Enter in Terminal. This setup is visible
and sends no post. Close setup before starting the background agent.
This is a new, separate Brave login; saved logins from your personal browser
are not copied. If X refuses sign-in or presents a challenge, complete any
supported verification manually. Changing the browser is not a restriction bypass.

To use installed Google Chrome instead, set `TWITTER_AGENT_BROWSER=chrome`
on both login and start commands. It uses `browser-profile-chrome/`.
No browser installation or bundled-browser fallback occurs automatically.
The installed browser runs with its Chromium sandbox enabled. During manual
login, `logs/login-diagnostics.json` records the last 50 HTTP error statuses,
failed requests and JavaScript error events. Only hostnames, timestamps and
status codes are saved, never request bodies, credentials, full URLs or error
messages. These diagnostics can distinguish request failures from a stalled UI,
but cannot establish that X has accepted the account credentials.
Browser events continue to be processed while waiting for Terminal confirmation,
so diagnostics are written during the manual login attempt.

Run `bash start-twitter-agent.sh` to restart cleanly. Run
`bash stop-twitter-agent.sh` to stop. The first cycle runs immediately, subject
to the daily cap and duplicate/pending-submission protection.

Open <http://127.0.0.1:8765>. Only **Verified published** and an actual X post
URL count as proof. The dashboard links before/after screenshots captured from
the isolated browser, not from the desktop. Logs, screenshots and the session
profile are local and may contain private account information; do not share them.

If login or a platform challenge blocks operation, the agent stops and reports
the error. A submission whose outcome is unknown stays pending in
`logs/posts.sqlite3` and blocks retries even across restarts. Manually inspect
the account before reconciling that ledger entry. Never blindly clear it.
Legacy `posted` status records are not publication evidence.

Validation: `.venv/bin/python -m unittest test_background_poster.py`.

## Existing Arc session: opt-in foreground takeover

### Campaign health and recovery

`bash start-twitter-agent.sh --arc-campaign` enables a supervisor. Normal waiting
shows the next opportunity, verified daily totals, and the last cycle's result.
A useful-match skip is not a failure. The three standalone slots are 09:00,
14:00, and 19:00 local time; existing verified posts consume slots, including
deleted posts conservatively counted by the ledger.

Transient Arc scripting timeouts, local model service errors, and rejected model
reply output receive at most three cycle retries (30/60/120 seconds). The
supervisor relaunches unexpected crashes or workers with no status heartbeat for
five minutes, at most three times with the same backoff. It terminates only its
own child. A foreground-app focus loss pauses the same action for 30 seconds,
then retries with a fresh countdown and full account/tab checks, like input
interference. Pending submissions still block retries. Changed tabs/windows are
not automatically overridden. Clean shutdown, Escape, unsafe drafts, permission failures, and
exhausted retries are not overridden: the dashboard says Needs attention.
The stop script targets the supervisor, which also stops its worker.

An uncertain submission remains blocked. Every five minutes the agent attempts
read-only reconciliation in the original thread/profile using the control session.
Only exact own-account text and reply-parent verification can resolve it; failed
checks never clear the ledger or republish. This is bounded recovery, not an
unrestricted self-modifying agent, and cannot fix login challenges or permissions.
Publication verification normalizes presentation whitespace and line breaks
inside a displayed link only when the complete displayed URL appears in the
expected draft. It does not infer destinations from shortened t.co links or
accept different wording/URLs. Account and reply-parent checks remain required.

Run `bash start-twitter-agent.sh --arc` with your own X profile open in Arc.
If `account_handle` is explicitly configured in settings, the active existing
X tab (including a composer) can be reused. Its actual signed-in profile must
match that configured handle before any content changes.
This compiles a native `takeover-helper`. It shows a soft blue/violet edge glow
with a translucent status capsule on every screen. The glow has a continuous
48-point falloff, no solid border, subtle breathing and fade transitions.
Reduced Motion disables the breathing/fades. A five-second countdown runs
before the first foreground action in a worker's control session, not between
searches, source reads, and posts. A worker restart or interrupted/failed
session requires a fresh countdown.
It does not hard-lock input: normal input is allowed during the countdown.
Escape cancels the countdown. After the countdown, the helper confirms its
input monitor is armed and displays ARC UNDER CONTROL; Escape or any observed
keyboard input, mouse clicks, scrolling, or meaningful mouse movement then pause
control. Mouse movement uses a six-pixel accumulated displacement threshold:
zero-delta cursor notifications and tiny back-and-forth jitter do not interrupt.
The loop retries the same action after 30 seconds with a fresh countdown.
The pause banner distinguishes interrupted control from a failed run; logs include event type,
source process ID and movement deltas, not typed text or clipboard contents.
Input Monitoring permission is required for the helper; Screen Recording is
required for Arc-window evidence. If monitoring fails, no takeover proceeds.
The glow and input-interruption monitoring hide/disarm between actions, including
model generation and scheduled waits, so normal desktop use is possible. The
session remains available and re-arms without another countdown when work resumes.
Escape remains a stop signal even while idle. Input during active control pauses
the action and retries only after 30 seconds with a fresh countdown. This does
not make Arc a headless browser: it still foregrounds the pinned Arc tab when
acting. Changed account/window/tab identity is never silently adopted.
An active action times out after 90 seconds; idle sessions do not. The helper
exits if its controller disappears.

Arc must allow JavaScript from Apple Events (its developer menu). The script
pins one existing profile tab, verifies the signed-in account, foreground app,
window and tab, and only fills a standalone empty post dialog using DOM commands.
One native foreground paste shortcut is sent after checking Arc's exact
window, tab and tagged composer. Clipboard items and formats are saved locally, temporarily
replaced with the draft, and restored unless another application changes the
clipboard meanwhile. No clipboard contents are logged. The exact tagged
composer must remain focused before paste. Python needs Accessibility permission.
Foreground input has a small interruption race: already-sent input cannot be
recalled. The input monitor and per-operation focus checks stop further actions,
but this is not a hard input lock or an absolute guarantee against interference.
The helper ignores only its controller's specially tagged text events.
Any unexpected focus/input change
stops operation; no uncertain submission is automatically retried. Already-sent
commands cannot be undone by an abort, so publication may remain unverified.
Screenshots capture the Arc window only. Do not change tabs during takeover.
An actual matching post on the account profile is required before success is
recorded. A border alone does not establish that a tweet was published.
Arc generates one draft per cycle, not a multi-post campaign. Each draft is
lowercase and at most 240 characters. An empty composer or an explicitly tagged
agent-owned draft is required. Native Select All/Delete resets the editor's real
state before a single native paste; exact text is checked before submission.
Shortcut keys are released even on abort. Unowned drafts are never overwritten.

## Search-driven contextual replies (Arc)

With `approved_webpage` set to `https://cruise.kolektivkrog.si`, a separate
read-only takeover searches Top and Recent for explicit "shill/pitch your
startup", "post/drop your landing page", and one-word/one-sentence invitations.
Up to six invitation candidates join HeyClicky and thread candidates.
Only the target author's own invitation authorizes a product pitch. Ordinary
HeyClicky conversations remain nonpromotional. Links are allowed only when the
author asks for a website/landing page/link, and only the exact approved URL;
"no links" excludes URLs. One-word responses must be one alphabetic word, and
one-sentence requests are validated. Full-source verification reclassifies the
invitation before drafting. Invitation wording is not permission to disregard
platform rules, post repeatedly, or force the daily reply ceiling.

`check_public_responses: true` checks the public Mentions tab in the same control session
and the two most recent verified publication threads. No DMs are opened.
Public responses must pass relevance, source verification and duplicate checks;
they are prioritized over new outreach when useful, not answered automatically
just because a notification exists. Thread context is labeled separately and
may refer to a conversation, not necessarily a direct reply to the root.

The local `outreach` table records publication categories and observable reply/
like counts from own published threads. Missing/localized/abbreviated labels
remain unknown, not zero. The dashboard shows sample sizes and average replies
by category. After at least five measured publications in a category, its
observed reply average can prioritize relevant candidates within new outreach.
Existing conversations still come first; relevance, caps and model selection
remain enforced. These are exploratory observations, not causal growth evidence,
conversion analytics, or a guarantee of results. No volume escalation occurs.

Activity is persisted separately from heartbeat in `activity_events` (last 1,000
events). The dashboard shows actual verified publication text/permalinks, the
last prepared reply, last publication timestamp, and target-specific rejection
reasons. A current heartbeat does not imply a recent comment was published.
Historical ledger rows without event timestamps display their recorded day;
the agent never invents precise publication times.

Each reply cycle may inspect up to three separately selected candidates when
full-source reading rejects a candidate or its draft duplicates prior text.
Targets reuse the active control session without additional countdowns. Only explicit pre-submission rejections
advance the shortlist; possible submissions, focus changes and unknown errors
never authorize trying another publication. Rejected targets are cooled down
for six hours. Invitation previews with known timestamps older than seven days
are excluded, as are zero-word and unsupported multiword format requests.
Invalid model output is redrafted at most three times against the same cached
candidates or full source. It does not restart discovery. Exhausted drafts skip
the target/cycle with recorded reasons, rather than creating repeated search
batches. Service failures still use the separate bounded recovery policy.
Redrafting includes the previous rejection as corrective feedback. Each
invitation prompt includes only its applicable format rule: pitch requests
never receive website-sharing instructions intended for link invitations.
For explicit website invitations, the model writes prose only and code appends
the configured approved URL. Empty prose, other URLs/domains, and overlength
combined output are rejected rather than silently repaired or truncated.

Standalone posts rotate three labeled experiments based on verified post count:
one concrete problem, one useful workflow tip, and one honest product trade-off.
Only project-supported facts are allowed; no fabricated metrics, personal stories,
engagement bait, or image-dependent captions. Recent verified standalone posts
are supplied to the post model to avoid repetitive hooks. Previous reply bodies
remain in the ledger for duplicate checking, not in reply-generation prompts.
Replies should refer to the actual source and not assume permission delays.
DumDev is the configured X automation app; Cruise Control is a separate
HeyClicky companion. The model may not describe one as the other or claim
HeyClicky ownership. Praise for another product is not accepted as DumDev
credit; only explicit DumDev feedback can receive a brief acknowledgement.
Own-thread context alone does not establish which product is being praised.
Rejected full-source drafts advance the bounded shortlist without publication.
Experiment categories are recorded with verified publications for later measured
comparison; there is not yet sufficient evidence to claim an algorithmic winner.

Run `.venv/bin/python twitter_agent.py --repo . --post-mode arc --discover-reply` for one search/reply cycle, or
`bash start-twitter-agent.sh --arc-replies` for recurring reply discovery.
This uses one rotating seed and up to two evidence-derived follow-up queries
in both **Top** and **Recent**, reading at most 12 visible posts per search.
It retains up to six unique Top and six Recent candidates, interleaved with
Recent first. The same control session opens at most two result threads,
preferring one Recent and one Top, and inspects up to 12 visible posts following each root,
then makes one bounded scroll below the root and reads up to 12 more,
retaining up to six relevant conversation candidates overall. The exact root must be
visible; ancestors and the root itself are not treated as reply candidates.
Self-posts, ads, ambiguous quoted posts and already
replied targets are excluded. The local model selects a relevant post and writes
a short direct question/comment based on that actual text. Marketing claims and
invented fixes are not appropriate. Thread comments may qualify through relevant
task/permission/agent context even without repeating HeyClicky. Root context is
labeled separately, never attributed to the commenter; the reply targets the
comment's own permalink. Full-source reading retains that context. Up to 18
candidates reach selection, and still only one reply can be published per cycle.
Top ordering is supplied by X, not a guarantee of measured engagement. No useful
match means no submission.

Discovery, reading the selected full source, and publication share one control
session and initial countdown; model drafting occurs while desktop input is released.
Successful read-only takeovers show READ COMPLETE, not a stopped/failed-post banner.
Search previews may truncate long posts: the selected permalink is opened and
its full text read before the final reply is generated. An expanded source must
retain the exact preview prefix; changed or ambiguous sources are skipped.
The selected source permalink and exact full
text are rechecked before opening its Reply dialog. The dialog's source text is
checked, allowing only presentation whitespace and appended media-link differences.
If the dialog truncates the source too, its Show more control is expanded and the
full text must match before any reply is entered. Failed verification skips the
target without stopping the campaign.
If the source cannot be found and reverified before opening Reply, the cycle
logs a skipped target and the campaign stays running for its next scheduled cycle.
One reply is entered, never a batch. The published own-account reply must be found
and its original parent visible in the thread before it counts as verified.
Verification searches the original thread rather than the sometimes-stale
profile Replies tab. Reply caps are tracked separately and only one reply per original post is allowed
across restarts. Any pending submission, of either kind, blocks further publication.
Do not clear unknown outcomes to make retries happen.
`--loop --initial-delay-minutes 60` resumes discovery after a verified manual
run without immediately sending another reply. A cap is a ceiling, not a quota:
unhelpful opportunities are skipped.

## Combined campaign

`bash start-twitter-agent.sh --arc-campaign` runs both surfaces, reading live
dashboard settings each cycle. With caps of 3 posts and 100 replies, standalone
opportunities are 09:00, 14:00 and 19:00 local time; reply discovery is spaced at
14 minutes 24 seconds across 24 hours. Existing verified posts/replies count
toward today's caps. Missed post slots can run on startup, but caps persist.
Each discovery sends at most one relevant reply, and no match is skipped; 100
is not a guaranteed daily total. Failed/interrupted or pending submissions stop
the campaign rather than blindly retrying. Midnight resets daily counts, not
duplicate-target protection. Foreground takeovers remain visible and abortable.
The dashboard displays verified daily post/reply totals and configured caps.
Reply drafting uses a 16K-token local model context so the candidate list,
project facts, identity rules and rejection feedback remain in the same request.
Rejected drafts are retried against cached candidates, with feedback supplied
to both candidate selection and final writing; they never bypass publication checks.
Each cycle considers up to three ranked targets individually. A skipped or
repeatedly invalid draft advances to the next cached target instead of ending
the entire cycle. Skipped targets enter the existing six-hour cooldown, so one
unanswerable mention cannot monopolize every search.

Discovery now starts with a rotating, date-bounded seed for the configured
interests (HeyClicky for the existing account), reads both Top and Recent,
and derives up to two follow-up queries from words and phrases actually
observed in those results. Query history and observations persist in the local
ledger across restarts. Recent and top candidates are interleaved, and up to
one root from each mode is read for conversation context. Those thread details
also inform later searches. Invitation searches rotate separately.

The dashboard lists recent queries and observed conversation themes with source
permalinks. A theme requires at least two distinct authors; themes appearing in
both modes rank first. Old posts, promoted posts, quoted/ambiguous sources and
repeated sightings from one author do not manufacture a trend. A one-author
detail may guide an exploratory query, but is not reported as a trend. These are
sampled conversations, not a claim about platform-wide popularity. Thread context
never substitutes for the target author's actual words when drafting a reply.
Neutral thanks for sharing information are allowed; accepting another product's
credit, invented numbers, repeated text and uncertain publications remain blocked.
Previous reply bodies are kept out of generation prompts to prevent the local
model copying unrelated facts. The ledger still checks them for duplicates.

Mouse/keyboard interference before submission keeps the agent alive: it waits
30 seconds and retries the same draft/target or discovery action with a fresh
countdown. Escape, permission failures and lost input monitoring are not retried.
If a submission could have happened, the loop stays alive in
`pending_reconciliation` without publishing again; verify/reconcile that outcome
before it resumes. Pending entries are never automatically cleared.

Validation: `.venv/bin/python -m unittest test_background_poster.py test_arc_poster.py test_organic_replies.py`.
