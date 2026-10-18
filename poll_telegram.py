import json
import os
import pathlib
import urllib.parse
import urllib.request
import zipfile

token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
allowed = os.environ.get("TELEGRAM_ALLOWED_USER_ID", "")
offset = int(os.environ.get("TG_UPDATE_OFFSET") or "0")
gh_token = os.environ.get("GH_TOKEN", "")
repo = os.environ.get("GH_REPOSITORY", "")
out = pathlib.Path(os.environ["GITHUB_OUTPUT"])
api = f"https://api.telegram.org/bot{8773407190:AAGuU55775YWpLdQHGfLzc0v3ofwrtu6ob0}/"

def telegram(method, data=None):
    url = api + method
    if data is not None:
        url += "?" + urllib.parse.urlencode(data)
    with urllib.request.urlopen(url, timeout=30) as response:
        payload = json.loads(response.read())
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API error in {method}")
    return payload["result"]

def send(chat_id, message):
    telegram("sendMessage", {"chat_id": chat_id, "text": message})

def set_output(name, value):
    with out.open("a") as f:
        f.write(f"{name}={value}\n")

def update_offset(new_offset):
    # Persist offset in repository Actions variable, preventing old uploads from rebuilding.
    url = f"https://api.github.com/repos/{repo}/actions/variables/TG_UPDATE_OFFSET"
    data = json.dumps({"name": "TG_UPDATE_OFFSET", "value": str(new_offset)}).encode()
    req = urllib.request.Request(url, data=data, method="PATCH", headers={
        "Authorization": f"Bearer {gh_token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()
    except Exception as e:
        raise RuntimeError("Could not update TG_UPDATE_OFFSET. Check workflow actions:write permission and that the repository variable exists.") from e

set_output("should_build", "false")
if not token or not allowed:
    raise SystemExit("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_ALLOWED_USER_ID secret.")

updates = telegram("getUpdates", {"offset": offset, "timeout": 0, "allowed_updates": json.dumps(["message"])})
if not updates:
    print("No Telegram updates.")
    raise SystemExit(0)

# Process updates in order; consume unauthorized and non-ZIP messages too.
next_offset = offset
chosen = None
for update in updates:
    next_offset = max(next_offset, int(update["update_id"]) + 1)
    message = update.get("message", {})
    sender = str(message.get("from", {}).get("id", ""))
    chat_id = str(message.get("chat", {}).get("id", ""))
    if sender != str(allowed):
        print("Ignored message from non-authorized user.")
        continue
    if message.get("text", "").strip() in ("/start", "/help"):
        send(chat_id, "Send me a Theos project as a .zip document. I will try to compile it on Linux and send back a .deb or build log. Checks run about every 5 minutes. Upload only projects you trust.")
        continue
    document = message.get("document")
    if document and document.get("file_name", "").lower().endswith(".zip"):
        if int(document.get("file_size", 0)) > 19 * 1024 * 1024:
            send(chat_id, "ZIP is too large. Please keep it below 19 MiB.")
            continue
        chosen = (chat_id, document)
        break
    if message:
        send(chat_id, "Please send the project as a .zip document, or use /help.")

# Save the offset through the chosen update only, so later updates remain queued.
if chosen:
    chat_id, document = chosen
    update_id = next(u["update_id"] for u in updates if u.get("message", {}).get("document", {}).get("file_id") == document["file_id"])
    update_offset(update_id + 1)
    info = telegram("getFile", {"file_id": document["file_id"]})
    file_path = info["file_path"]
    file_url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    with urllib.request.urlopen(file_url, timeout=60) as response:
        data = response.read(19 * 1024 * 1024 + 1)
    if len(data) > 19 * 1024 * 1024:
        send(chat_id, "ZIP exceeds the 19 MiB limit.")
        raise SystemExit(0)
    path = pathlib.Path("incoming/project.zip")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    set_output("should_build", "true")
    set_output("chat_id", chat_id)
    set_output("file_name", document.get("file_name", "project.zip").replace("\n", "_"))
else:
    update_offset(next_offset)
