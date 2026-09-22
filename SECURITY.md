# Security

LoL Analytics is intended for use on your own computer. It is not an authenticated, multi-user hosted service. Do not expose the Streamlit server to the public internet without a separate security review.

## Report a vulnerability privately

Use [GitHub's private vulnerability report](https://github.com/urattems/lol-analytics/security/advisories/new) for this repository. Include the affected version, impact and a minimal reproduction with invented data. There is no guaranteed response time or formal support SLA.

Do **not** post API keys, tokens, `.env`, a personal database, raw match exports or real-player screenshots in public issues. If private reporting is unavailable, open a minimal issue asking for a private reporting channel without disclosing the vulnerability or sensitive material.

## If a key is exposed

Revoke or regenerate it at its provider immediately, then update your local configuration. Deleting a file or rewriting a Git commit does not revoke the key and cannot undo copies already made. Never paste a replacement key into an issue or pull request.

## Local data and exports

- Keep the Riot API key in your local `.env`, outside version control.
- Databases and exports may contain personal game history. Protect them like other personal files.
- AI exports exclude external player identities, but retain the analyzed player's Riot ID and match IDs. Context may still allow identification; inspect the bundle before sharing.
- The app does not automatically send data to an AI service. Uploading a downloaded bundle is your decision.
- Bug reports should use the synthetic demo or a minimal invented fixture.
- In v2.6.0, Journal notes/goals and automatic SQLite backups are local but not encrypted by the application. Protect the folder and its backups. Erasing an active annotation does not erase older backups.
- Journal sharing is a separate, allowlisted format with a reviewed preview and opt-in annotations. Redaction removes known identities and common sensitive patterns, not every identifying statement a person could write. Avoid personal details; context can still identify players. The original private annotations are not automatically added to historical AI/session exports.

Security fixes target the latest release. Older development snapshots do not have a separate maintenance commitment.
