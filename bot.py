#!/usr/bin/env python3
"""Telegram bot that triggers the Theos build workflow on GitHub Actions.

Send it a .zip of a Theos project (or /build <git url>). The workflow compiles
it on a macOS runner and sends the .dylib back to the same chat.

Standard library only, Python 3.8+. Configuration comes from environment
variables or a .env file next to this script (see .env.example).
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ALLOWED_OPTIONS = ("deb", "rootless", "debug")
MAX_ZIP_BYTES = 20 * 1024 * 1024  # Telegram Bot API: bots can download files up to 20 MB
GIT_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(:\d+)?/[A-Za-z0-9._~%+/-]+$")
BRANCH_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._/-]{0,99}$")

HELP = """Send me a .zip of your Theos project and I'll build it on GitHub Actions, then send the .dylib back here.

Options (in the file caption, or after /build):
  deb       also send the .deb package
  rootless  build with THEOS_PACKAGE_SCHEME=rootless
  debug     debug build (default is FINALPACKAGE=1)

/build <https git url> [branch] [--deb] [--rootless] [--debug]
/status  last 3 builds
/id      show your Telegram ID

Tip: zip the folder that contains the Makefile. Files over 20 MB can't be downloaded by bots, so use /build with a git URL instead."""

BUILD_USAGE = "Usage: /build <https git url> [branch] [--deb] [--rootless] [--debug]"


class UsageError(Exception):
    """The user's message was malformed; the text is safe to show them."""


# --------------------------------------------------------------------------
# Parsing helpers (pure functions)
# --------------------------------------------------------------------------

def parse_options(tokens):
    """['--deb', 'rootless'] -> ['deb', 'rootless']. Raises UsageError on unknown ones."""
    options = []
    for token in tokens:
        name = token.lower().lstrip("-")
        if name not in ALLOWED_OPTIONS:
            raise UsageError(
                "Unknown option '%s'. Available: %s" % (token[:30], ", ".join(ALLOWED_OPTIONS))
            )
        if name not in options:
            options.append(name)
    return options


def parse_build_command(text):
    """'/build URL [branch] [--flag ...]' -> (url, branch, options)."""
    parts = text.split()
    if len(parts) < 2:
        raise UsageError(BUILD_USAGE)
    url = parts[1]
    if not GIT_URL_RE.match(url):
        raise UsageError("That doesn't look like a valid https:// git URL.")
    branch = ""
    flags = []
    for token in parts[2:]:
        if token.startswith("--"):
            flags.append(token)
        elif not branch:
            branch = token
        else:
            raise UsageError(BUILD_USAGE)
    if branch and not BRANCH_RE.match(branch):
        raise UsageError("That branch name isn't valid.")
    return url, branch, parse_options(flags)


def repo_label(url):
    name = url.rstrip("/").split("/")[-1]
    return name[:-4] if name.endswith(".git") else name


def clean_label(text):
    return re.sub(r"[\x00-\x1f\x7f]", "", text)[:100]


def parse_ids(raw):
    ids = set()
    for item in re.split(r"[,\s]+", raw.strip()):
        if not item:
            continue
        if not item.lstrip("-").isdigit():
            sys.exit("ALLOWED_USER_IDS must be numbers separated by commas, got: %r" % item)
        ids.add(int(item))
    return ids


def load_dotenv(path):
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------
# Bot
# --------------------------------------------------------------------------

class Bot:
    def __init__(self, token, gh_token, repo, ref, workflow, allowed_ids):
        self.token = token
        self.gh_token = gh_token
        self.repo = repo
        self.ref = ref
        self.workflow = workflow
        self.allowed = allowed_ids

    # -- Telegram ----------------------------------------------------------

    def tg(self, method, http_timeout=30, **params):
        url = "https://api.telegram.org/bot%s/%s" % (self.token, method)
        req = urllib.request.Request(
            url, json.dumps(params).encode(), {"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=http_timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as err:
            body = err.read().decode("utf-8", "replace")[:200]
            raise RuntimeError("Telegram %s failed: HTTP %s %s" % (method, err.code, body)) from None

    def say(self, chat_id, text, reply_to=None):
        params = {"chat_id": chat_id, "text": text[:4000], "disable_web_page_preview": True}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        try:
            self.tg("sendMessage", **params)
        except Exception as err:  # never let a failed reply kill the loop
            log("Could not send message: %s" % err)

    # -- GitHub ------------------------------------------------------------

    def gh(self, method, path, payload=None):
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(
            "https://api.github.com" + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self.gh_token,
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
                "User-Agent": "theos-builder-bot",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                body = json.loads(raw)
            except ValueError:
                body = {"message": raw.decode("utf-8", "replace")[:200]}
            return err.code, body

    def dispatch(self, inputs):
        """Start the workflow. Returns None on success, or a short error string."""
        status, body = self.gh(
            "POST",
            "/repos/%s/actions/workflows/%s/dispatches" % (self.repo, self.workflow),
            {"ref": self.ref, "inputs": inputs},
        )
        if 200 <= status < 300:
            return None
        message = body.get("message", "") if isinstance(body, dict) else ""
        hints = {
            401: "The GitHub token is invalid or expired.",
            403: "The GitHub token needs 'Actions: Read and write' on %s." % self.repo,
            404: "Repo or workflow not found, or the token can't see it (check GITHUB_REPO and WORKFLOW_FILE).",
            422: "GitHub rejected the request: %s (is the workflow on branch '%s'?)" % (message, self.ref),
        }
        return hints.get(status, "GitHub returned HTTP %s: %s" % (status, message))

    def recent_runs(self):
        status, body = self.gh(
            "GET",
            "/repos/%s/actions/workflows/%s/runs?per_page=3" % (self.repo, self.workflow),
        )
        if status != 200:
            return "Couldn't read runs (HTTP %s)." % status
        runs = body.get("workflow_runs", [])
        if not runs:
            return "No builds yet."
        icons = {"success": "✅", "failure": "❌", "cancelled": "⛔", "timed_out": "⏱"}
        lines = []
        for run in runs:
            if run.get("status") == "completed":
                icon = icons.get(run.get("conclusion"), "•")
            else:
                icon = "🔄" if run.get("status") == "in_progress" else "⏳"
            lines.append("%s %s\n%s" % (icon, run.get("display_title", "build"), run.get("html_url", "")))
        return "\n\n".join(lines)

    # -- Message handling --------------------------------------------------

    def start_build(self, chat_id, reply_to, source_type, source, label, branch, options):
        inputs = {
            "chat_id": str(chat_id),
            "reply_to": str(reply_to or ""),
            "source_type": source_type,
            "source": source,
            "file_name": clean_label(label),
            "branch": branch,
            "options": " ".join(options),
        }
        error = self.dispatch(inputs)
        if error:
            self.say(chat_id, "❌ Couldn't start the build.\n" + error, reply_to)
            return
        extra = " (%s)" % ", ".join(options) if options else ""
        self.say(
            chat_id,
            "🔨 Queued%s. A build usually takes 3-6 minutes; I'll send the result here.\n"
            "Progress: https://github.com/%s/actions" % (extra, self.repo),
            reply_to,
        )

    def handle_message(self, msg):
        chat_id = msg["chat"]["id"]
        user_id = (msg.get("from") or {}).get("id")
        mid = msg.get("message_id")
        text = (msg.get("text") or "").strip()
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""

        if command == "/id":
            self.say(chat_id, "Your Telegram ID: %s\nThis chat's ID: %s" % (user_id, chat_id), mid)
            return

        if user_id not in self.allowed:
            self.say(
                chat_id,
                "⛔ You're not allowed to use this bot.\n"
                "Owner: add %s to ALLOWED_USER_IDS and restart the bot." % user_id,
                mid,
            )
            return

        if command in ("/start", "/help"):
            self.say(chat_id, HELP, mid)
        elif command == "/status":
            self.say(chat_id, self.recent_runs(), mid)
        elif command == "/build":
            try:
                url, branch, options = parse_build_command(text)
            except UsageError as err:
                self.say(chat_id, str(err), mid)
                return
            self.start_build(chat_id, mid, "git", url, repo_label(url), branch, options)
        elif msg.get("document"):
            self.handle_document(chat_id, mid, msg)
        elif msg["chat"].get("type") == "private":
            self.say(chat_id, "Send me a .zip of a Theos project, or /help.", mid)

    def handle_document(self, chat_id, mid, msg):
        doc = msg["document"]
        name = doc.get("file_name") or "project.zip"
        if not name.lower().endswith(".zip"):
            self.say(chat_id, "Please send the project as a .zip file.", mid)
            return
        if doc.get("file_size", 0) > MAX_ZIP_BYTES:
            self.say(
                chat_id,
                "That zip is over 20 MB, which is the most a bot can download. "
                "Remove build folders (.theos, packages) or use /build with a git URL.",
                mid,
            )
            return
        try:
            options = parse_options((msg.get("caption") or "").split())
        except UsageError as err:
            self.say(chat_id, str(err), mid)
            return
        self.start_build(chat_id, mid, "telegram", doc["file_id"], name, "", options)

    # -- Startup / loop ----------------------------------------------------

    def check_setup(self):
        me = self.tg("getMe")["result"]
        log("Telegram OK: @%s" % me.get("username"))
        self.tg("deleteWebhook")  # long polling can't work while a webhook is set

        status, body = self.gh("GET", "/repos/%s/actions/workflows/%s" % (self.repo, self.workflow))
        if status == 200:
            log("GitHub OK: found workflow '%s' in %s" % (body.get("name"), self.repo))
        else:
            log(
                "WARNING: GitHub returned HTTP %s for %s/%s (%s). Builds will fail until this is fixed."
                % (status, self.repo, self.workflow, body.get("message", ""))
            )
        if not self.allowed:
            log("WARNING: ALLOWED_USER_IDS is empty, so only /id works. Send /id to the bot and add the number.")

    def run(self):
        self.check_setup()
        log("Listening for messages (Ctrl+C to stop)")
        offset = None
        while True:
            try:
                params = {"timeout": 50, "allowed_updates": ["message"]}
                if offset is not None:
                    params["offset"] = offset
                updates = self.tg("getUpdates", http_timeout=65, **params).get("result", [])
                for update in updates:
                    offset = update["update_id"] + 1
                    if "message" in update:
                        try:
                            self.handle_message(update["message"])
                        except Exception as err:
                            log("Error handling update: %r" % err)
            except KeyboardInterrupt:
                raise
            except Exception as err:
                log("Polling error: %s (retrying in 5s)" % err)
                time.sleep(5)


def log(text):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), text, flush=True)


def require(name):
    value = os.environ.get(name, "").strip()
    if not value:
        sys.exit("Missing %s. Copy .env.example to .env and fill it in." % name)
    return value


def main():
    load_dotenv(Path(__file__).with_name(".env"))
    bot = Bot(
        token=require("TELEGRAM_BOT_TOKEN"),
        gh_token=require("GITHUB_PAT"),
        repo=require("GITHUB_REPO"),
        ref=os.environ.get("BUILD_REF", "main").strip() or "main",
        workflow=os.environ.get("WORKFLOW_FILE", "build.yml").strip() or "build.yml",
        allowed_ids=parse_ids(os.environ.get("ALLOWED_USER_IDS", "")),
    )
    try:
        bot.run()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
