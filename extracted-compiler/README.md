# Telegram Theos Cloud Compiler (GitHub Actions)

Compile a Theos project ZIP from Telegram into a `.deb` package using a temporary Linux GitHub Actions runner. No always-on bot server is used: a scheduled workflow checks Telegram every 5 minutes.

## Important limitations

- Scheduled GitHub Actions workflows are not instant. Expect up to about 5 minutes before an upload is noticed.
- Telegram's hosted Bot API currently limits bot file downloads; this starter accepts ZIPs up to 19 MiB to stay below the usual 20 MB download limit.
- Theos projects are not all Linux-compatible. Swift/Xcode-dependent projects may require a macOS runner and may not build here.
- This compiles code; it does not install the tweak on your iPhone.
- **Treat ZIPs as executable code.** A Makefile can run arbitrary commands. The build job intentionally receives no Telegram token or GitHub API token. Only upload projects you trust.
- GitHub Actions minutes and repository usage limits apply.

## 1. Create a Telegram bot

1. In Telegram, open `@BotFather`.
2. Send `/newbot`, follow the prompts, and copy the bot token.
3. Open your new bot and send `/start` once.
4. Find your numeric Telegram user ID using a trusted user-ID bot or the `getUpdates` API after messaging your bot. Do not use your bot token in public chats.

## 2. Create a GitHub repository

Create a new repository on GitHub, preferably **private**, and upload all files in this folder preserving their paths. You can do this from the GitHub website on your iPhone.

## 3. Add repository secrets and variable

In your repository, go to **Settings → Secrets and variables → Actions**.

Add **Repository secrets**:
- `TELEGRAM_BOT_TOKEN` = the token from BotFather
- `TELEGRAM_ALLOWED_USER_ID` = your numeric Telegram user ID

Add a **Repository variable**:
- `TG_UPDATE_OFFSET` = `0`

The workflow uses the built-in `GITHUB_TOKEN` to update this variable. The workflow requests `actions: write` permission for this purpose.

## 4. Enable Actions

Open **Actions** in the repository and enable workflows if GitHub asks. The workflow runs on a 5-minute schedule and can also be started manually with **Actions → Telegram Theos Compiler → Run workflow**.

Scheduled workflows may be delayed by GitHub and may be disabled in inactive public repositories. Uploading a ZIP is not an instant webhook; this is polling by design.

## 5. Send a project ZIP

Zip the folder containing your Theos project. The ZIP should contain the `Makefile` at its root or inside one top-level project folder. Typical project files include:
- `Makefile`
- `control` or `layout/DEBIAN/control`
- source files such as `.xm`, `.x`, `.m`, `.mm`, `.c`, `.cpp`
- `Tweak.xm` or other files referenced by your Makefile

Send the `.zip` document to your bot. Use `/start` for instructions.

The workflow looks for a Makefile, tries to install Theos and common Linux build dependencies, then runs `make package`. If the build succeeds, the bot sends a `.deb` file. If it fails, it sends the tail of the build log.

## SDK/toolchain notes

The workflow attempts to install Theos and download the public Theos SDK repository. SDK selection and toolchain compatibility change over time, so some projects may need a project-specific SDK or extra dependencies. Review `.github/workflows/telegram-theos.yml` before relying on it for important builds.

## Security

- Keep the repository private if appropriate.
- Never paste the bot token into source code or commit it.
- Do not make the bot public: it only responds to the numeric user ID configured in `TELEGRAM_ALLOWED_USER_ID`.
- Do not upload projects you do not trust. The Makefile executes in a separate build job without secrets, but malicious code can still attack the temporary runner and falsify build outputs.
- Revoke/rotate the bot token with BotFather if it leaks.
- Keep workflow permissions as narrow as practical.

## Troubleshooting

- **No response:** wait up to 5 minutes, then check Actions → workflow run logs. Make sure you sent `/start` to the bot and the secret names are exact.
- **Unauthorized:** verify `TELEGRAM_ALLOWED_USER_ID` is the numeric ID of your Telegram account, not the bot's ID or username.
- **No Makefile:** put the project Makefile at the ZIP root or inside one top-level folder.
- **SDK not found / compiler errors:** project may need a different SDK, toolchain, architecture, or additional dependencies.
- **No `.deb`:** check the uploaded build log in Telegram.
