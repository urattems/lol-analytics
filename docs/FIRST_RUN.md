# First launch — from ZIP to your own match history

[Back to README](../README.md#windows-double-click-and-follow-along)

The assistant is French-first, like the app. You do not need VS Code, Git or a terminal command. Download the current stable source archive and extract the folder containing `INSTALLER.bat`.


## 1. Keep a real folder, not an open ZIP

Extract **all** files into a folder you can keep, for example `Documents\LoL Analytics`. Avoid `Program Files`, temporary folders and network drives. Double-click `INSTALLER.bat`.

A preparation window explains what is happening. Python and all required components are checked. First-time downloads can take several minutes; subsequent starts reuse the installed environment. Existing environments are never silently deleted or replaced.

If Python is missing, a confirmation offers **Python 3.13 for your Windows account**, using the explicit `Python.Python.3.13` package from the WinGet source. This uses the official Python installer. You can decline. If WinGet is missing, the assistant opens [Python's Windows downloads](https://www.python.org/downloads/windows/): choose the Python 3.13 64-bit installer, keep Tcl/Tk enabled, install for yourself and run `INSTALLER.bat` again. Python 3.11 and 3.12 are also accepted; existing compatible environments are retained. No permanent execution-policy or firewall changes are made. [WinGet installation options](https://learn.microsoft.com/en-us/windows/package-manager/winget/install)

## 2. Identify the right account

Enter the **complete Riot ID**, including `#TAG`. Select your real LoL server from the list; a tag such as `#EUW` or `#NA1` does not determine the server.

Use **Obtenir / renouveler ma clé sur le portail Riot** to open Riot's official site. Sign in there, then copy your own API key into the masked field. Never enter your Riot password into the assistant. Your key is sent to Riot in HTTPS request headers, not placed in a command-line argument or a download URL.

Development keys deactivate every 24 hours. They are for development/prototyping, not authorization to operate a public production service. Personal and production access have separate Riot requirements. Distributing this source code does not imply Riot approval or include a shared key. [Riot key types and requirements](https://developer.riotgames.com/docs/portal#web-apis)

Choose **Vérifier mon compte**. The assistant checks Account V1, the selected platform through Summoner V4, then Match V5 history access. A valid account with no available games is accepted. These checks are read-only; they do not save configuration or import matches. [Official Riot endpoint and routing documentation](https://developer.riotgames.com/docs/lol)

## 3. Confirm, then open

Check the account and server shown on the confirmation page. You can go back before anything is saved. Choose whether to import up to 20 recent games and create a desktop shortcut. Changing an existing main account requires an additional confirmation.

**Enregistrer et préparer** writes `.env` atomically. Existing settings unrelated to the selected account, including a custom database path, are retained. The exact old `.env` bytes are kept in an ignored `.env.backup-*` file. A concurrent edit is refused rather than overwritten. No games or profiles are removed by installation.

The optional first import reuses existing matches. If it fails or is rate-limited, configuration remains saved and committed matches remain available; use **Synchroniser** to resume. You can also skip this first import altogether. The desktop shortcut uses Windows' actual Desktop location, including redirected/OneDrive desktops. A shortcut belonging to another folder is not overwritten.

Click **Ouvrir LoL Analytics**. Keep the launcher window open; closing it stops the server. The personal launcher binds to the local computer only and chooses an available port starting at 8501. If your browser does not open, follow the localhost address printed in the window. For later starts, use the desktop shortcut or `start_lol_analytics.bat`.

## Renew a key without starting over

Close the running app, then double-click `INSTALLER.bat`. Your Riot ID and server are prefilled. Paste a fresh key, verify and confirm; leave the key field empty to retain an existing key. Uncheck the optional import if you only want to update credentials. Your database is not reset. Restart the app so its cached configuration is reloaded.

`.env` and `.env.backup-*` contain **unencrypted credentials** protected by the permissions of your local folder, not a vault. Git ignores them, but cloud backup/sync software may still copy them. Never share your installed workspace as a ZIP; distribute a clean source archive. Review and remove old credential backups when no longer needed.

## When something goes wrong

| Message or symptom | What to do |
| --- | --- |
| Riot ID not found | Copy the full name and tag from the Riot client. |
| Account found, no LoL profile on that server | Correct the server; Account V1 alone does not identify it. |
| Key refused, 401/403 | Renew the key on Riot's portal and check its permissions. |
| Riot rate limit, 429 | Wait before retrying verification. It does not retry in a tight loop. |
| Network failure or Riot unavailable | Check Internet/proxy/firewall or try later. Nothing is saved by verification. |
| No history available | The account can still be configured. Riot may not provide eligible games yet. |
| Python/dependency preparation failed | Check Internet and disk space, then relaunch. No data is automatically deleted. |
| Existing `.venv` incompatible or incomplete | Close the app, rename only `.venv` to `venv-ancien`, then rerun the installer. Never delete `.env` or `data` as a repair step. |
| `.env` is invalid, unreadable or a symlink | It is preserved. Fix the file/permissions manually or restore your own backup. |
| Another assistant is open | Close it. After a forced interruption, remove only `.env.setup.lock` after confirming no assistant is running, then retry. |
| Shortcut unavailable | Use `start_lol_analytics.bat`. If you moved the project, rerun the installer to create a shortcut to the new folder. |
| Scripts blocked by company policy | Ask your administrator. Do not weaken machine-wide security settings. |

Offline tests cover HTTP failures, secret-safe messages, configuration quoting, literal Unicode names, backups, interrupted saves, profile selection and Windows shortcut/widget behavior using invented identities. They do not certify Riot uptime, every antivirus policy, or a real installation on a computer without Python/WinGet.
