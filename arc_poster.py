from __future__ import annotations

import json
import os
import queue
import sqlite3
from contextlib import closing
import re
import subprocess
import threading
import time
import uuid
from pathlib import Path

from background_poster import finish_attempt, reserve_attempt
from organic_replies import discover_posts, discover_thread_replies, discover_responses, growth_summary, invitation_kind, record_outreach
from discovery_memory import discovery_scope, discovery_snapshot


COMPOSER_JS = '''
const candidates=[...document.querySelectorAll('[role="dialog"] [data-testid="tweetTextarea_0"]')];
const editor=candidates.reverse().find(e=>{
  const r=e.getBoundingClientRect();
  if(r.width<=0||r.height<=0)return false;
  const hit=document.elementFromPoint(r.x+r.width/2,r.y+Math.min(r.height/2,10));
  return hit===e||e.contains(hit);
});
'''

PUBLICATION_TEXT_JS = '''
const publicationMatches=(article,expected)=>{
  const body=article.querySelector('[data-testid="tweetText"]');
  if(!body)return false;
  let actual=body.innerText;
  for(const anchor of body.querySelectorAll('a')){
    const displayed=anchor.innerText.replace(/\\s+/g,"");
    if(displayed && /^https?:\\/\\//.test(displayed) && expected.includes(displayed)){
      actual=actual.replace(anchor.innerText,displayed);
    }
  }
  const normalize=s=>s.replace(/\\s+/g," ").trim();
  return normalize(actual)===normalize(expected);
};
'''


def native_key(code: int, down: bool, flags: int, guard: Takeover):
    import Quartz
    guard.check()
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    event = Quartz.CGEventCreateKeyboardEvent(source, code, down)
    Quartz.CGEventSetFlags(event, flags)
    Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventSourceUserData, os.getpid())
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)
    time.sleep(0.05)


def native_command(code: int, guard: Takeover):
    import Quartz
    try:
        native_key(55, True, Quartz.kCGEventFlagMaskCommand, guard)
        native_key(code, True, Quartz.kCGEventFlagMaskCommand, guard)
        native_key(code, False, Quartz.kCGEventFlagMaskCommand, guard)
    finally:
        release_native_key(code)
        release_native_key(55)


def release_native_key(code: int):
    import Quartz
    source = Quartz.CGEventSourceCreate(Quartz.kCGEventSourceStatePrivate)
    event = Quartz.CGEventCreateKeyboardEvent(source, code, False)
    Quartz.CGEventSetIntegerValueField(event, Quartz.kCGEventSourceUserData, os.getpid())
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def decode_arc_result(output: str):
    result = json.loads(output)
    # Arc wraps JSON.stringify output in an additional JSON string.
    return json.loads(result) if isinstance(result, str) else result


class UserInputInterruption(RuntimeError):
    pass


class FocusInterruption(UserInputInterruption):
    pass


class ReplyTargetUnavailable(RuntimeError):
    pass


class TransientArcError(RuntimeError):
    pass


REPLY_SOURCE_JS = '''
const url=EXPECTED_URL;
const article=[...document.querySelectorAll('[data-testid="tweet"]')].find(a=>
  a.querySelector('a:has(time)')?.getAttribute("href")===url);
if(!article)return null;
const text=article.querySelector('[data-testid="tweetText"]')?.innerText.trim();
if(!text)return null;
return {url:"https://x.com"+url,text,
        promoted:!!article.querySelector('[data-testid="placementTracking"]'),
        quoted:article.querySelectorAll('[data-testid="tweetText"]').length!==1};
'''


REPLY_DIALOG_JS = COMPOSER_JS + '''
const dialog=editor?.closest('[role="dialog"]');
const normalize=s=>s.replace(/https:\\/\\/\\s*pic\\.x\\.com\\/\\S+/g,"").replace(/\\s+/g," ").replace(/\\s+([,.;!?])/g,"$1").trim();
const expected=normalize(EXPECTED_TEXT);
if(!dialog)return false;
if([...dialog.querySelectorAll('[data-testid="tweetText"]')].some(e=>normalize(e.innerText)===expected))return true;
const expand=dialog.querySelector('[data-testid="tweet-text-show-more-link"]');
if(expand && !expand.hasAttribute('data-cruise-control-expanded')){
  expand.setAttribute('data-cruise-control-expanded',"true");
  expand.click();
}
return false;
'''


def verify_reply_dialog(js, guard, text: str):
    code = REPLY_DIALOG_JS.replace("EXPECTED_TEXT", json.dumps(text))
    for _ in range(25):
        if js(code):
            return
        guard.wait(0.2)
    raise ReplyTargetUnavailable("Reply dialog does not contain the verified full source; no submission attempted.")


def read_reply_source(js, guard, target: dict, *, allow_expansion: bool = False) -> dict:
    code = REPLY_SOURCE_JS.replace("EXPECTED_URL", json.dumps(target["url"].removeprefix("https://x.com")))
    for _ in range(25):
        guard.wait(0.3)
        source = js(code)
        if not source:
            continue
        if source["url"] != target["url"] or source["promoted"] or source["quoted"]:
            raise ReplyTargetUnavailable("Reply source is ambiguous or promoted; no submission attempted.")
        expected = target["text"].strip()
        actual = source["text"].strip()
        if actual == expected or (allow_expansion and expected and actual.startswith(expected)):
            return dict(target, **source)
        raise ReplyTargetUnavailable("Reply source text changed; no submission attempted.")
    raise ReplyTargetUnavailable("Discovered reply target could not be reverified; no submission attempted.")


class Takeover:
    def __init__(self, helper: Path):
        self.process = subprocess.Popen([str(helper), str(os.getpid())], stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.events: queue.Queue[str] = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()
        try:
            message = self.events.get(timeout=5)
        except queue.Empty as exc:
            self.close(False)
            raise RuntimeError("Takeover indicator did not become ready.") from exc
        if message != "READY":
            self.close(False)
            raise RuntimeError(message)

    def _read(self):
        for line in self.process.stdout:
            self.events.put(line.strip())

    def check(self):
        try:
            event = self.events.get_nowait()
        except queue.Empty:
            event = None
        if event:
            if event.startswith("ABORT: input event type "):
                raise UserInputInterruption(f"Observed input paused takeover ({event}).")
            raise RuntimeError(f"Takeover interrupted ({event}); no automatic retry.")
        if self.process.poll() is not None:
            raise RuntimeError("Takeover interrupted or input monitoring unavailable; no automatic retry.")

    def message(self, text: str):
        self.check()
        self.process.stdin.write(text + "\n")
        self.process.stdin.flush()

    def wait(self, seconds: float):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.check()
            time.sleep(0.05)

    def arm(self):
        self.message("ARM")
        try:
            event = self.events.get(timeout=2)
        except queue.Empty as exc:
            raise RuntimeError("Takeover input monitor did not arm; no control attempted.") from exc
        if event != "ARMED":
            if event.startswith("ABORT: input event type "):
                raise UserInputInterruption(f"Observed input paused takeover ({event}).")
            raise RuntimeError(f"Takeover stopped before control: {event}")
        self.check()

    def close(self, success: bool, *, read_only: bool = False):
        if self.process.poll() is None:
            try:
                self.process.stdin.write(("READ_DONE\n" if read_only else "DONE\n") if success else "STOP\n")
                self.process.stdin.flush()
                self.process.wait(timeout=4)
            except (BrokenPipeError, subprocess.TimeoutExpired):
                self.process.terminate()
                self.process.wait()


class ControlSession:
    """Keep the input monitor alive across actions, without holding idle desktop input."""
    def __init__(self):
        self.guard: Takeover | None = None
        self.identity: tuple[str, str, str] | None = None
        self.countdown_complete = False

    def acquire(self, helper: Path, identity: tuple[str, str, str]) -> tuple[Takeover, bool]:
        if self.guard is not None:
            self.guard.check()
            if identity != self.identity:
                self.close(False)
                raise RuntimeError("Arc window, tab or account changed during the control session; refusing to switch targets.")
        else:
            self.guard = Takeover(helper)
            self.identity = identity
        return self.guard, not self.countdown_complete

    def arm(self, guard: Takeover, countdown: bool, status_file: str, report):
        begin_control(guard, countdown, status_file, report)
        self.countdown_complete = True

    def release(self, success: bool):
        if not success:
            self.close(False)
        elif self.guard is not None:
            self.guard.message("IDLE")

    def close(self, success: bool = True):
        guard = self.guard
        self.guard = None
        self.identity = None
        self.countdown_complete = False
        if guard is not None:
            guard.close(success, read_only=True)


def begin_control(guard: Takeover, countdown: bool, status_file: str, report):
    if countdown:
        report(status_file, {"state": "takeover_countdown", "error": None})
        for seconds in range(5, 0, -1):
            guard.message(f"Arc control in {seconds}s · Escape to cancel")
            guard.wait(1)
    guard.arm()
    report(status_file, {"state": "takeover_active"})


def run_script(script: str, guard: Takeover | None = None) -> str:
    process = subprocess.Popen(["osascript", "-e", script], stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    deadline = time.monotonic() + 8
    try:
        while True:
            if guard:
                guard.check()
            if time.monotonic() > deadline:
                raise TransientArcError("Arc scripting timed out. Check Arc's permission to allow JavaScript from Apple Events; no post is verified.")
            try:
                output, error = process.communicate(timeout=0.1)
                if process.returncode:
                    error = error.strip()
                    if re.search(r"execution error: Arc lost foreground focus \(-2700\)$", error):
                        raise FocusInterruption("Arc lost foreground focus; takeover paused before further commands.")
                    if re.search(r"Can(?:'|’)t get active tab of window \d+\.?\s*\(-1728\)$", error, re.I):
                        raise TransientArcError("Arc active tab is temporarily unavailable: " + error)
                    raise RuntimeError("Arc scripting failed: " + error)
                return output.strip()
            except subprocess.TimeoutExpired:
                continue
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()


def find_x_tab(account: str | None = None) -> tuple[str, str, str]:
    if account and not re.fullmatch(r"[A-Za-z0-9_]{1,15}", account):
        raise RuntimeError("Configured account_handle is not a valid X handle.")
    output = run_script('''
    tell application "Arc"
      set found to ""
      set activeID to id of active tab of front window
      repeat with w in windows
        repeat with t in tabs of w
          set u to URL of t
          if u starts with "https://x.com/" then
            set marker to "inactive"
            if id of t is activeID then set marker to "active"
            set found to found & (id of w) & character id 9 & (id of t) & character id 9 & u & character id 9 & marker & linefeed
          end if
        end repeat
      end repeat
      return found
    end tell''')
    candidates = []
    active_candidates = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) not in {3, 4}:
            continue
        if account and parts[2].startswith("https://x.com/"):
            item = (parts[0], parts[1], "/" + account)
            candidates.append(item)
            if len(parts) == 4 and parts[3] == "active":
                active_candidates.append(item)
        elif re.fullmatch(r"https://x.com/[A-Za-z0-9_]+/?", parts[2]):
            path = "/" + parts[2].rstrip("/").rsplit("/", 1)[1]
            if path not in {"/home", "/explore", "/notifications", "/messages", "/settings", "/login", "/search"}:
                candidates.append((parts[0], parts[1], path))
    if not candidates or len({item[2].lower() for item in candidates}) != 1:
        raise RuntimeError("Open your own X profile in Arc. No unique account profile tab was found; refusing to guess.")
    return (active_candidates or candidates)[0]


def post_in_arc(args, text: str, settings: dict, report, *, reply_target: dict | None = None, discover: bool = False, reconcile: bool = False, inspect_target: dict | None = None, scan_threads: list[dict] | None = None, invitations: bool = False, responses: bool = False):
    if not discover and not responses and not reconcile and inspect_target is None and scan_threads is None and (not text.strip() or len(text) > 240 or text != text.lower()):
        raise RuntimeError("Arc accepts exactly one nonempty lowercase draft of at most 240 characters.")
    source_target = inspect_target or reply_target
    if source_target and not re.fullmatch(r"https://x\.com/[A-Za-z0-9_]+/status/\d+", source_target.get("url", "")):
        raise RuntimeError("Reply target must be an exact discovered X post permalink.")
    helper = Path(__file__).parent / "takeover-helper"
    if not helper.is_file():
        raise RuntimeError("Compile takeover.swift with swiftc before using Arc mode.")
    window, tab, profile = find_x_tab(settings.get("account_handle"))
    target = f'tab id {applescript_string(tab)} of window id {applescript_string(window)}'
    session = getattr(args, "control_session", None)
    if session is not None:
        try:
            guard, countdown = session.acquire(helper, (window, tab, profile))
        except UserInputInterruption:
            session.close(False)
            raise
    else:
        guard, countdown = Takeover(helper), True
    success = False
    root = Path(args.status_file).resolve().parent
    database = root / "posts.sqlite3"
    evidence_id = uuid.uuid4().hex
    evidence = root / "evidence" / evidence_id
    evidence.mkdir(parents=True)

    def js(code: str, focus: bool = True, operation: str | None = None):
        check = ""
        if focus:
            check = f'''
            tell application "System Events"
              set frontApp to name of first application process whose frontmost is true
              if frontApp is not "Arc" then error "Arc lost foreground focus"
            end tell
            tell application "Arc"
              set frontWindowID to id of front window
              set frontTabID to id of active tab of front window
              if frontWindowID is not {applescript_string(window)} then error "Arc window changed"
              if frontTabID is not {applescript_string(tab)} then error "Arc tab changed"
            end tell'''
        action = '(()=>{' + code + '})()'
        if operation:
            key = json.dumps("cruise-control:" + evidence_id + ":" + operation)
            action = '''(()=>{
              const key=''' + key + ''';
              const prior=sessionStorage.getItem(key);
              if(prior!==null)return JSON.parse(prior);
              sessionStorage.setItem(key,"null");
              const result=''' + action + ''';
              sessionStorage.setItem(key,JSON.stringify(result));
              return result;
            })()'''
        wrapped = 'JSON.stringify(' + action + ')'
        output = run_script(check + f'\ntell application "Arc" to tell {target} to execute javascript {applescript_string(wrapped)}', guard)
        guard.check()
        return decode_arc_result(output)

    def own_account():
        identity = js('return {origin:location.origin,profile:document.querySelector(\'[data-testid="AppTabBar_Profile_Link"]\')?.getAttribute("href")};')
        if (not isinstance(identity, dict) or identity.get("origin") != "https://x.com" or
                str(identity.get("profile", "")).casefold() != profile.casefold()):
            raise RuntimeError("Signed-in account does not match the selected X profile.")

    def capture(name: str):
        import Quartz
        guard.check()
        own_account()
        windows = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID)
        arc = [w for w in windows if w.get("kCGWindowOwnerName") == "Arc" and w.get("kCGWindowLayer") == 0]
        if not arc:
            raise RuntimeError("Cannot identify Arc window for evidence capture.")
        subprocess.run(["/usr/sbin/screencapture", "-x", "-l", str(arc[0]["kCGWindowNumber"]),
                        str(evidence / name)], check=True, timeout=5)
        guard.check()

    try:
        if session is not None:
            session.arm(guard, countdown, args.status_file, report)
        else:
            begin_control(guard, countdown, args.status_file, report)
        run_script(f'tell application "Arc"\n activate\n select {target}\nend tell', guard)
        own_account()
        capture("before.png")
        if reconcile:
            with closing(sqlite3.connect(database)) as db:
                pending = db.execute("SELECT id,text,kind,parent FROM posts WHERE state='pending'").fetchall()
            results = []
            for attempt, pending_text, kind, parent in pending:
                js('location.href=' + json.dumps(parent if kind == "reply" else "https://x.com" + profile) + ';return true;')
                url = None
                for _ in range(25):
                    guard.wait(0.3)
                    url = js(PUBLICATION_TEXT_JS + '''
                    const expected=''' + json.dumps(pending_text) + ''';
                    for(const article of document.querySelectorAll('[data-testid="tweet"]')){
                      if(!publicationMatches(article,expected))continue;
                      const href=article.querySelector('a:has(time)')?.getAttribute("href");
                      if(href?.startsWith(''' + json.dumps(profile + "/status/") + '''))return "https://x.com"+href;
                    }return null;
                    ''')
                    if url:
                        break
                if not url:
                    raise RuntimeError("Pending submission cannot be safely reconciled automatically.")
                if kind == "reply":
                    if not js('return [...document.querySelectorAll(\'[data-testid="tweet"] a:has(time)\')].some(a=>a.getAttribute("href")===' +
                              json.dumps(parent.removeprefix("https://x.com")) + ');'):
                        raise RuntimeError("Pending reply parent is not visible in its original thread.")
                finish_attempt(database, attempt, "verified", url)
                capture("after.png")
                report(args.status_file, {"state":"verified_published","post_url":url,"last_verified_tweet":pending_text,
                                         "last_reply_target":parent,
                                         "posted_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
                                         "evidence_before":f"/evidence/{evidence_id}/before.png",
                                         "evidence_after":f"/evidence/{evidence_id}/after.png","error":None})
                results.append(url)
            success = True
            return results
        if discover or responses or scan_threads is not None:
            if js('return [...document.querySelectorAll(\'[data-testid="tweetTextarea_0"]\')].some(e=>e.innerText.trim());'):
                raise RuntimeError("Discovery would leave a nonempty draft; refusing navigation.")
            selected_topics = settings.get("topics", [])
            topic_options = {"topics": selected_topics} if settings.get("onboarding_completed") else {}
            report(args.status_file, {"state": "checking_public_responses" if responses else "scanning_top_threads" if scan_threads is not None else "discovering_posts",
                                     "search_topics": [],
                                     "search_modes":["top","recent"], "discovery_category":"invitations" if invitations else "selected_topics" if selected_topics else "heyclicky"})
            candidates = (discover_responses(js,guard,profile,database, **topic_options) if responses else discover_thread_replies(js, guard, scan_threads, profile, database, **topic_options)
                          if scan_threads is not None else discover_posts(js, guard, profile, database,
                                                                         invitations=invitations,
                                                                         topics=settings.get("topics", []),
                                                                         on_query=lambda query, mode: report(
                                                                             args.status_file, {
                                                                                 "search_topics": [query],
                                                                                 "search_mode": mode,
                                                                                 "note": "Reading observed results to choose the next query.",
                                                                                 "error": None})))
            report(args.status_file, {"thread_candidates" if scan_threads is not None else "discovered_posts": candidates,
                                     "state": "discovery_complete"})
            success = True
            if responses:
                report(args.status_file, {"response_candidates":candidates,"growth_summary":growth_summary(database)})
            elif not invitations:
                discovery = discovery_snapshot(database, discovery_scope(selected_topics))
                report(args.status_file, {"search_topics": discovery["queries"],
                                         "discovery_trends": discovery["trends"],
                                         "discovery_note": discovery["note"]})
            return candidates
        if inspect_target:
            if js('return [...document.querySelectorAll(\'[data-testid="tweetTextarea_0"]\')].some(e=>e.innerText.trim());'):
                raise RuntimeError("Reading the source would leave a nonempty draft; refusing navigation.")
            report(args.status_file, {"state": "reading_reply_source", "takeover_step": "reading_full_source"})
            js('location.href=' + json.dumps(inspect_target["url"]) + ';return true;')
            source = read_reply_source(js, guard, inspect_target, allow_expansion=True)
            report(args.status_file, {"state": "reply_source_read", "reply_context": source})
            success = True
            return source
        report(args.status_file, {"takeover_step": "opening_reply_composer" if reply_target else "opening_standalone_composer"})
        if reply_target:
            js('location.href=' + json.dumps(reply_target["url"]) + ';return true;')
            read_reply_source(js, guard, reply_target)
            matched = js('''
                const url=''' + json.dumps(reply_target["url"].removeprefix("https://x.com")) + ''';
                const expected=''' + json.dumps(reply_target["text"]) + ''';
                const article=[...document.querySelectorAll('[data-testid="tweet"]')].find(a=>a.querySelector('a:has(time)')?.getAttribute("href")===url);
                if(!article)return false;
                if(article.querySelector('[data-testid="tweetText"]')?.innerText.trim()!==expected)throw Error("Target post changed; reply refused");
                const button=article.querySelector('[data-testid="reply"]');
                if(!button)throw Error("Target reply button missing");
                button.click();return true;
                ''', operation="open_reply")
            if not matched:
                raise ReplyTargetUnavailable("Discovered reply target could not be reverified; no submission attempted.")
        else:
            js('if(location.pathname!=="/compose/post")location.href="https://x.com/compose/post";return true;')
        ready = False
        for _ in range(25):
            guard.wait(0.2)
            if js(COMPOSER_JS + 'return !!editor;'):
                ready = True
                break
        if not ready:
            raise RuntimeError("Standalone composer did not open. Refusing to type into another surface.")
        own_account()
        if reply_target:
            verify_reply_dialog(js, guard, reply_target["text"])
        report(args.status_file, {"takeover_step": "filling_verified_composer"})
        cleared = js(COMPOSER_JS + '''
        const expected=''' + json.dumps(text) + ''';
        if (!editor) throw Error("Composer missing");
        const priorOwned=!!editor.getAttribute("data-cruise-control-editor");
        const current=editor.innerText.trim();
        if (current && !priorOwned)throw Error("Composer contains an unowned draft; refusing to change it");
        editor.setAttribute("data-cruise-control-editor",''' + json.dumps(evidence_id) + ''');
        editor.focus();
        return true;
        ''', operation="clear_own_duplicate")
        if not cleared:
            raise RuntimeError("Owned draft could not be prepared.")
        import Quartz
        if not Quartz.CGPreflightPostEventAccess():
            raise RuntimeError("Python requires Accessibility permission for native composer input.")
        if not js(COMPOSER_JS + 'return editor?.getAttribute("data-cruise-control-editor")===' +
                  json.dumps(evidence_id) + ' && document.activeElement===editor;'):
            raise RuntimeError("Composer focus changed before replacing the owned draft.")
        if not js(COMPOSER_JS + 'return editor.innerText.trim()===' + json.dumps(text) + ';'):
            native_command(0, guard)
            try:
                native_key(51, True, 0, guard)
                native_key(51, False, 0, guard)
            finally:
                release_native_key(51)
        guard.wait(0.3)
        prepared = js(COMPOSER_JS + '''
        const expected=''' + json.dumps(text) + ''';
        if(!editor || editor.getAttribute("data-cruise-control-editor")!==''' + json.dumps(evidence_id) + ''')throw Error("Composer changed");
        if(editor.innerText.trim()===expected)return "already_filled";
        if(editor.innerText.trim())throw Error("Composer is not empty; refusing to append");
        editor.focus();
        return "ready_for_native_input";
        ''')
        if prepared == "ready_for_native_input":
            import Quartz
            import AppKit
            if not Quartz.CGPreflightPostEventAccess():
                raise RuntimeError("Python requires Accessibility permission to send process-addressed text to Arc.")
            if not js(COMPOSER_JS + 'return editor?.getAttribute("data-cruise-control-editor")===' +
                      json.dumps(evidence_id) + ' && document.activeElement===editor;'):
                raise RuntimeError("Composer focus changed before native paste.")
            pasteboard = AppKit.NSPasteboard.generalPasteboard()
            saved = []
            for original in pasteboard.pasteboardItems() or []:
                item = AppKit.NSPasteboardItem.alloc().init()
                for kind in original.types():
                    data = original.dataForType_(kind)
                    if data is not None:
                        item.setData_forType_(data, kind)
                saved.append(item)
            pasteboard.clearContents()
            if not pasteboard.setString_forType_(text, AppKit.NSPasteboardTypeString):
                pasteboard.clearContents()
                pasteboard.writeObjects_(saved)
                raise RuntimeError("Native pasteboard could not be prepared.")
            owned_change = pasteboard.changeCount()
            try:
                native_command(9, guard)
                guard.wait(0.5)
            finally:
                if pasteboard.changeCount() == owned_change:
                    pasteboard.clearContents()
                    if saved and not pasteboard.writeObjects_(saved):
                        raise RuntimeError("Native clipboard restoration failed.")
        elif prepared != "already_filled":
            raise RuntimeError("Composer preparation did not succeed.")
        guard.wait(0.3)
        own_account()
        if not js(COMPOSER_JS + 'return editor?.getAttribute("data-cruise-control-editor")===' +
                  json.dumps(evidence_id) + ' && editor.innerText.trim()===' + json.dumps(text) + ';'):
            raise RuntimeError("Active composer changed after filling; refusing to submit.")
        attempt = reserve_attempt(database, text, int(settings["replies_per_day"] if reply_target else settings["posts_per_day"]),
                                  "reply" if reply_target else "post", reply_target["url"] if reply_target else None)
        report(args.status_file, {"state": "posting_tweet"})
        submitted = js(COMPOSER_JS + '''
        let dialog=editor?.closest('[role="dialog"]');
        while(dialog && !dialog.querySelector('[data-testid="tweetButton"]')){
          dialog=dialog.parentElement?.closest('[role="dialog"]');
        }
        const button=dialog?.querySelector('[data-testid="tweetButton"]');
        if (!editor || editor.getAttribute("data-cruise-control-editor")!==''' + json.dumps(evidence_id) + ''' || editor.innerText.trim()!==''' + json.dumps(text) + ''' || !button || button.disabled || button.getAttribute("aria-disabled")==="true") return false;
        button.click();return true;
        ''', operation="submit")
        if not submitted:
            finish_attempt(database, attempt, "not_submitted")
            raise RuntimeError("Composer or submit button changed; no submission made.")
        guard.wait(2)
        own_account()
        js('location.href=' + json.dumps(reply_target["url"] if reply_target else "https://x.com" + profile) + ';return true;')
        url = None
        for _ in range(25):
            guard.wait(0.4)
            url = js(PUBLICATION_TEXT_JS + '''
            const expected=''' + json.dumps(text) + ''', profile=''' + json.dumps(profile) + ''';
            for(const article of document.querySelectorAll('[data-testid="tweet"]')){
              if(!publicationMatches(article,expected))continue;
              const href=article.querySelector('a:has(time)')?.getAttribute("href");
              if(href?.toLowerCase().startsWith(profile.toLowerCase()+"/status/") && /^\\/[^/]+\\/status\\/\\d+$/.test(href))return "https://x.com"+href;
            }return null;
            ''')
            if url:
                break
        if not url:
            raise RuntimeError("Submission outcome unverified. Pending ledger blocks retries.")
        if reply_target:
            js('location.href=' + json.dumps(url) + ';return true;')
            related = False
            for _ in range(25):
                guard.wait(0.3)
                related = js('''
                  const expected=''' + json.dumps(reply_target["url"].removeprefix("https://x.com")) + ''';
                  return [...document.querySelectorAll('[data-testid="tweet"] a:has(time)')].some(a=>a.getAttribute("href")===expected);
                ''')
                if related:
                    break
            if not related:
                raise RuntimeError("Published reply parent could not be verified; outcome remains pending.")
        own_account()
        capture("after.png")
        finish_attempt(database, attempt, "verified", url)
        category = ("invitation_" + invitation_kind(reply_target["text"]) if reply_target and invitation_kind(reply_target["text"])
                    else reply_target.get("discovery_source","conversation") if reply_target
                    else "standalone_" + settings.get("content_experiment","unclassified"))
        record_outreach(database,url,category)
        report(args.status_file, {"state": "verified_published", "post_url": url,
                                 "last_verified_tweet": text,
                                 "last_reply_target": reply_target["url"] if reply_target else None,
                                 "posted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                 "evidence_before": f"/evidence/{evidence_id}/before.png",
                                 "evidence_after": f"/evidence/{evidence_id}/after.png", "error": None, "note": None})
        success = True
        return url
    finally:
        if session is not None:
            session.release(success)
        else:
            guard.close(success, read_only=discover or responses or inspect_target is not None or scan_threads is not None)
