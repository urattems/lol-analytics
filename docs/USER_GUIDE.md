# User guide

[Installation and demo](../README.md#try-it-without-a-riot-key) · [Screenshots](../README.md#your-personal-analysis-workspace) · [Security](../SECURITY.md)

The interface is French-first. Labels below match the buttons you will see.

New on Windows? Double-click `INSTALLER.bat` in the extracted source folder. It prepares the environment, verifies your Riot ID/server/key, sets your main account and can create a desktop shortcut. Rerun it to renew a key without resetting your library. [First-launch walkthrough](FIRST_RUN.md)

## Find a match

Open **Historique** and filter by champion, role, queue, result or period. Advanced filters include patch, timeline availability and encountered players on either side. Cards are paginated in batches of 20; that is not an import limit.

Both teams are visible in compact view. Click a player's name for their card; **Équipes & détails** expands scores and item details. Long names expose their complete label on hover. Missing identities use a neutral local label, not the raw PUUID.

**Timeline** opens that match. The return action preserves your history filters, page and expanded card. **Session** opens its session chronology.

## Review a session

Open **Sessions**, or choose **Revoir la dernière session** sur Accueil. A session uses a maximum gap between the end of one game and the start of the next: 45 minutes by default, with 30/60/90-minute options.

The session is built from the full local library of the active profile. Comparisons use strictly earlier games on the same champion and role, up to 20 reference games. Highlighted comparisons require at least two eligible session games and five reference games, including availability of the metric being compared.

One-game sessions remain readable without inventing a trend. Missing timelines can be enriched explicitly for the retryable games in that session. Previous/next actions and the return link keep you in the same session.

## Read a timeline

Gold, CS, XP and team advantage use stored frames. Checkpoints select actual frames near the requested time; a game ending before 20 minutes does not acquire an invented checkpoint at 20.

For team advantage, the sign is relative to your team. The fill color identifies the side leading: blue for Blue Side, red for Red Side. Hover names the leader. Zero-crossing interpolation is only for drawing the colored areas, not a new analytical observation.

Final inventories do not imply purchase order. Use the item-event view for timestamped purchases and undo operations; a missing timestamp remains unavailable.

## Keep several profiles

In **Profils**, enter a Riot ID and explicitly select its server; the tag is not a server. Import 20 or 50 recent games, then choose **Analyser ce profil**. **Profil analysé** identifies the active player; **Mon profil principal** returns to your main account.

Imports and exports are scoped to the selected profile. Shared matches are reused, and changing profiles resets incompatible filters and Timeline selections. Participants in a match are not automatically registered library profiles.

**Contexts → Coéquipiers → Voir nos parties** opens shared history. **Retrouver les Riot IDs** in History or Teammates explicitly revisits stored match details to recover missing names when the API provides them. It does not alter performance statistics.

**Options du profil → Supprimer le profil et ses données exclusives** previews the cleanup and asks for confirmation. It deletes that secondary registration, sync/job state and games that contain **no other registered profile**, together with their participants, timelines, frames, events, identity checkpoints and rank snapshots. Shared games remain intact, even when another secondary profile—not the main profile—is the one that needs them. The main profile is protected; an absent or ambiguous main identity blocks deletion. Active imports block cleanup, and the whole SQL deletion rolls back on error. There is no in-app undo. SQLite can reuse the released space even if its file does not immediately shrink.

**New in v2.6.0:** before deleting, the app creates and verifies a complete SQLite backup in `backups/` next to the database. If backup or verification fails, cleanup is cancelled. The confirmation result identifies the validated snapshot and its UTC date. Backups contain personal data: keep them private. This protects profile deletion, not every application update.

## Import hundreds of games without babysitting

**Synchroniser** and **Importer l’historique → Lancer l’import** create a local background job. For a 500-game target, the sidebar shows the actual local count, such as **85 / 500**. When Riot sends a 429, the failed request waits for `Retry-After` before retrying; the countdown is visible and does not block navigation. No key switching, parallel request flood or rate-limit bypass is used.

In v2.6.0, the panel stays visible when viewing another profile and names the job's owner. **Voir le profil de cet import** returns to that owner. When several jobs are paused, **Import à reprendre** selects which one to resume. Your analyzed profile and the import owner remain separate. Failed jobs expose a **Diagnostic expurgé** JSON that can be copied or downloaded without keys, identities, network responses or local paths.

**Annuler l’import** stops at the next safe boundary; an in-flight network request may finish first. Committed matches remain in SQLite. After an app/process restart, **Reprendre l’import** continues the saved job with the configured key. Discovered ID pages are retained, already committed match details are reused, and the persisted cooldown survives cancellation/restart. A new game played after discovery may require a subsequent fresh sync. Only one import/enrichment/cleanup worker operates on a library at once, protected by an OS lock that is released after a process crash.

A missing `Retry-After` uses a conservative two-minute pause. Valid numeric delays up to 24 hours and HTTP dates are supported; invalid, infinite, negative or excessively long delays pause the job without automatic retries. A zero delay still uses a small delay to avoid a tight loop. This relies on a correctly configured system clock after a restart. Authentication, exhausted network/server retries and invalid payloads pause with an actionable message, not an endless retry loop. Renew an expired key and restart the app before resuming. The small initial profile lookup and installer import remain bounded actions; use the history job for large imports.

The smaller explicit actions (profile lookup, identity recovery and Timeline enrichment) also share the library lock. After cancelling a rate-limited job, these actions refuse to send a request before its saved cooldown expires; the resumable job can wait for you automatically.

## Your observed rank and the lobby's average rank

In **Historique → Équipes & détails** or **Timeline**, eligible Ranked Solo/Duo (420) and Flex (440) games offer **Récupérer les rangs actuels**. This is an explicit background enrichment, not a request made merely by opening a page. It reads the official League V4 entries by participant PUUID on the match's platform, selects the matching ranked queue and saves each observation with its retrieval date. [Riot's League V4 reference](https://developer.riotgames.com/apis#league-v4/GET_getLeagueEntriesByPUUID)

You see **Votre rang**, **Lobby moyen**, how many participant ranks are usable, and the observation date (or date range for a resumed partial snapshot). A missing response is not silently converted to an unranked player or an Iron rank. Unknown/malformed fields remain unavailable. An unranked player is excluded from the average. In a partial local roster, the denominator is the number actually known, with an explicit warning that a normal game has ten players.

The descriptive average maps each regular division to a 100-LP interval and averages those positions. It is **not MMR**, a predicted result, or necessarily anyone's rank when the game was played. Master, Grandmaster and Challenger share the apex LP scale; an apex average is labeled **Master+**, never promoted to an invented Grandmaster/Challenger threshold. The exact observed personal badge remains visible.

Successful observations are kept when enrichment is interrupted, and a retry fills only missing rows. A complete snapshot is not silently refreshed on future visits. For old games this cannot reconstruct an old rank; for new games it records the rank known when you explicitly enrich them. Rank snapshots are currently local UI context and are not added to the existing AI export schema.

## Selections and exports

The page shows its selected count and scope. Overview, Champions, Contexts, Build Lab and Explorer apply their recent window before applicable filters. Insights applies its champion filter before the recent window. **Progression filters champion/role/queue and patch before slicing two disjoint periods.** Session Review uses the full session chronology.

Games shorter than five minutes are excluded from analytical summaries by default. This is a duration filter, not an assertion that Riot classified the game as a remake. Unknown duration is not converted to zero.

| Export | Contents and scope |
| --- | --- |
| Full AI bundle | All local games of the active profile, including short games. No Overview window or Explorer filter. |
| Custom AI bundle | The same `ExplorerSelection` model and selection function used by Explorer. |
| Session ZIP | The complete chosen session in play order, in `session_review.md` and `session_matches.jsonl`. |
| Journal review ZIP | Selected fields from the Journal's exact cohort, with optional dates/compositions/checkpoints and separately reviewed annotations. Four fixed files, or five with annotations. |

The full bundle retains schema **2.1** and exactly these 16 files:

```text
manifest.json               README_AI.md
summary.md                  matches.jsonl
champions.csv               builds.csv
presets.json                timeline_checkpoints.jsonl
timeline_events.jsonl       matchups.csv
teammates.csv               squads.csv
sessions.csv                context_insights.json
trajectories.csv            data_dictionary.md
```

Exports are prepared in memory and offered as downloads. No AI service is called. Opaque event extensions are excluded, external players use aliases, and CSV text cells are protected against formula interpretation. The analyzed profile's Riot ID and match IDs remain: this is not a promise of anonymity.

Finish imports before exporting when you need a reproducible snapshot. If the prepared dataset has become stale, the UI asks you to reload instead of offering an incomplete bundle.

## Page map

In v2.6.0, the native sidebar groups **Accueil / Champions / Progression / Journal**, **Historique / Sessions**, **Explorer / Export IA** and **Profils**. **Analyses complémentaires** retains Contexts, Insights, Build Lab and Timeline. The table below describes the available pages.

| Page | Use it for |
| --- | --- |
| Accueil | Recent form, champion overview and latest session |
| Sessions | A connected session chronology and personal reference points |
| Historique | Match cards, teams, builds and player navigation |
| Champions | Champion pool and individual champion views |
| Progression | Recent/previous periods, comparable acquisitions, dated personal rank |
| Journal | Private notes/tags, personal goals, recurring players and reviewed sharing |
| Insights | Descriptive comparisons, game trajectories and consistency |
| Contexts | Personal matchups, recurring teammates and sessions |
| Build Lab | Final inventories and available purchase sequences |
| Timeline | Match frames, checkpoints and events |
| Explorer | Precise filtered selections |
| Profils | Import and select local profiles |
| Export IA | Full or custom structured bundles |

## Troubleshooting

**Stuff & timings has no data:** open **Couverture & règles** on the Champion page. A missing patch catalog, incomplete Timeline, unknown item or ambiguous undo is excluded explicitly, never converted to 0:00. **Charger ce catalogue** fetches only public Data Dragon metadata for that patch, without a Riot key; subsequent timing reads are offline. The complete catalog version and retrieval date remain visible. In demo mode the same button is available; demo generation itself remains network-free.

**An import fails:** check your key, platform and routing region. Use the explicit retry/resume action; do not delete your database to fix an API error. Keys can expire and the API can rate-limit requests.

**A chart fails:** use **Réessayer**; other page content remains available. Version 2.6.0 logs a safe error category by default, not the raw exception. Explicit developer-mode details are not a sanitized export. Run:

```powershell
.venv\Scripts\python -m scripts.check_environment
.venv\Scripts\python -m pip check
```

Restart Streamlit after dependency updates. Do not uninstall pandas as a Plotly workaround: Streamlit requires it indirectly. `LOL_ANALYTICS_DEV_ERRORS=1` enables detailed browser errors for local development; leave it off when sharing your screen.

**Artwork is missing:** Data Dragon images need network access and the requested asset version. Text fallback remains usable.

**The demo refuses its database:** it detected non-synthetic content or an invalid database at its dedicated path. Preserve that file and inspect it locally; the launcher will not overwrite it.

## Reproduce analytical benchmarks

These commands generate their own synthetic data and do not need `.env` or Riot access:

```powershell
.venv\Scripts\python -m scripts.reliability_benchmark --sizes 500 1000 2500 --memory
.venv\Scripts\python -m scripts.session_review_benchmark --sizes 500 1000 2500
.venv\Scripts\python -m scripts.history_benchmark --sizes 500 1000 2500
.venv\Scripts\python -m scripts.stuff_benchmark --sizes 500 1000 2500
.venv\Scripts\python -m scripts.progression_benchmark --sizes 500 1000 2500
.venv\Scripts\python -m scripts.journal_benchmark --sizes 500 1000 2500
```

They measure local analytical preparation, not Riot latency or browser rendering. Synthetic scale and Python allocation measurements are not a production SLA or a bound on total process memory.

## Champion Experience

Choose a champion, role, patch and queue in the compact toolbar. **Période** retains the usual window, custom count, short-game opt-in and detailed champion-pool table. The initial role is the champion's most played role; the initial patch is the latest one present in that selection. All-role/all-patch selections are explicit. Timing groups still remain separated by role and patch.

- **Résumé:** champion performance and trends; existing context and Timeline summaries are in the complementary detail.
- **Stuff & timings:** first three distinct major item acquisitions and T2 boots, median and quartiles, sample counts, personal state at 10 minutes, and ordered paths. Sold items remain historical acquisitions; valid purchase undo removes the cancelled acquisition. Final inventories remain a separate detail, never an inferred purchase sequence.
- **Matchups:** observed same-declared-role opponents and links to their exact matches. An ambiguous opponent is unavailable.
- **Parties:** the exact matches behind the selected champion scope, with access to normal History cards and Timelines.

“En avance” means personal gold difference ≥ +500 at 10 minutes, “En retard” ≤ −500, and “Équilibré” strictly between them. Missing opponent/checkpoint means unavailable, not even. This is distinct from existing team-state metrics. An item bought before 10 minutes can appear in these descriptive comparisons; they do not prove that an advantage caused an item choice or a win.

Every **matchs sources** button stores the exact profile and match IDs. Normal History filters do not apply in that view. Follow **Timeline**, then return to sources and the same Champion tab/filters. **Quitter les matchs sources** restores ordinary History filtering. If matches disappear or the owner changes, the proof is not silently replaced with the whole library.

## Progression

**Accueil** offers up to three primary actions: revisit your latest session, inspect a qualified observation (or one explicitly described game), and open games missing a Timeline. Each card says which scope it uses. The observation uses the local champion/role history on one played patch, not necessarily the recent-window dropdown used by the coverage card. No Riot request starts on page open. Existing KPIs, form and recent match cards remain under **Forme récente, statistiques et dernières parties**.

In **Progression → Comparaison**, choose champion, role and queue, then 10 recent/20 previous, 20/20 or 20/50. Filters apply **before** the windows. The most-played champion/role is selected initially. Missing/invalid dates remain in History, not in a fictional period. You see actual N (for example 11/20), date ranges, patches and Timeline coverage for both windows. Each metric has its own usable count and exact-source links; duration-dependent metrics exclude missing or zero durations.

**Dernier patch présent · homogène** uses the latest dated known patch within that cohort. If it has too few games, the comparison stays small; it does not silently borrow another patch. **Un patch précis** chooses a single patch. **Mélanger les patchs explicitement** shows a warning, a per-patch breakdown of the selected metric, and patch-boundary markers on the plot. A post-patch difference is not automatically personal improvement. Deltas are recent minus previous in the metric's units (percentage points for win rate), not percentage change.

**Achats & timings** reuses qualified purchases from Champion Experience. Each group has the same champion, role, patch and item. Median, P25/P75, acquisition N and exact sources are shown separately for each window. N=0 means unavailable, not 0:00. Load missing public patch catalogs explicitly from **Champions → Stuff & timings**. This tab does not fetch them automatically.

**Rang daté → Capturer mon rang actuel** deliberately adds a personal League-V4 observation for the selected profile, server and Solo/Duo or Flex queue. The global job panel handles waits, cancellation and resume. The chart uses points only, dated when retrieved, with the exact rank/LP badge on hover. Unranked/unavailable statuses remain in the table; network/auth failures do not become unranked points. These are not the lobby snapshots and are not filtered by champion or game-window controls. No retroactive rank, interpolation, MMR, automatic capture on synchronization or new AI-bundle file is implied.

**Champions → Matchups** now shows median gold/CS checkpoints with usable N, personal @10 states including unavailable, and a first-item comparison against other identified opponents. Same champion/role/patch/item are required; queue scope follows the Champion filter. These are observations of declared same-role opponents, not certain lane duels or counter recommendations.

All three new evidence origins return through **Historique → Timeline → Historique → page d’origine**. Profile changes clear the evidence and filters. Personal rank observations are removed with their deleted secondary profile, while other profiles' observations remain.

## Journal

### Keep what matters about a game

Choose **Note & tags** on a History card or Timeline, or open **Journal → Notes & tags**. Write up to 5,000 characters and ten comma-separated tags, then click **Enregistrer la note et les tags**. There is no autosave: save before changing page, tab, match or profile. Tags are normalized and deduplicated; “duo” is your own label, not automatic premade detection.

If another window changed that annotation, your draft remains visible alongside a warning. Compare the stored version and explicitly reload it before trying again. Confirmed **Effacer note et tags** removes your annotation, not the match or another profile's notes. A shared match can have separate notes for each registered profile. Notes and backups are not encrypted by the app; erasing the current note does not erase old backups.

**Retrouver mes annotations et tags** shows your saved notes and descriptive tag groups across your entire local library, with N, results, usable KDA/deaths-per-minute counts and exact source matches. Small, overlapping groups do not establish a cause.

### Choose a personal goal

In **Objectifs → Créer un objectif**, choose a free-text goal or one of four measures: CS/min, deaths per game, gold difference at 15 minutes, vision/min. Set ≥/≤, a target and 5/10 future games. Champion/role/queue are optional. Fix a patch, or explicitly allow mixed patches; short games are excluded by default.

Only newly imported games **played after creation** can count. Existing matches and backfilled older games do not fill the horizon. The first eligible games are ordered by date; a missing measure shows ❔ and is not a failure, while ⚪ means no game yet. Each result includes scope, usable counts and sources. Later imports of eligible games or Timeline enrichment can update the results. Free-text goals never receive an invented automatic score. **Archiver** keeps the goal and matches, and stops counting games played after closure; create another goal to change its definition.

### Find familiar players

**Joueurs retrouvés** selects 20/50/100 games or Full, then applies champion/role/patch/queue and short-game filters. Choose allies or opponents and a minimum number of encounters within that selection. Results show your W/L, latest selected encounter, observed champions/roles and exact source matches. It does not import those accounts or infer a premade. Return from the source games to the same Journal tab.

### Share only what you choose

**Partager** uses that same window-then-filters selection, capped at 2,500 games. Choose final statistics, checkpoints, aliased compositions and exact dates. Dates increase the risk of identifying a game. **Préparer l’aperçu expurgé** creates an exact preview without uploading anything.

Original match IDs and player identities are not intentionally projected in this separate format; known identities, common secret patterns and local paths are additionally removed from text. **Proposer mes notes et tags dans l’aperçu** only proposes annotations for inspection. Read the sanitized text, then give the separate confirmation to enable their download. Changing profile, selection, options or database revision invalidates the prepared preview and consent.

The downloaded `lol-analytics-revue.zip` contains `manifest.json`, `report.md`, `matches.jsonl`, `matches.csv`, and optionally `annotations.jsonl`. Your original notes stay unchanged locally. Existing AI/session exports do not gain Journal fields. **Redaction is not universal anonymity**: a story, champion/patch or roster may identify people without their names. Share without notes when in doubt, and never put secrets or sensitive personal details in a shared review.

Try the fresh Journal demo through `start_demo.bat`: `data/demo-journal-v2.6.db` includes clearly fictional notes and goals, leaving older demos and personal data untouched.

## Interpréter les comparaisons et les rangs

Dans **Champions → Stuff & timings → État personnel @10**, l’observation compare les médianes avance/retard pour le même champion, rôle, patch, palier et objet. Une conclusion mise en avant exige cinq acquisitions dans chaque groupe. Les files observées sont indiquées, sans supposer leur identité. Le delta signé est avance moins retard : négatif signifie plus tôt. Sous cinq secondes d’écart absolu, les timings sont décrits comme très proches. Tableau complet et union exacte des sources restent accessibles. L’état @10 peut être postérieur à l’achat : aucune causalité. Avance ≥ +500 or, retard ≤ −500, équilibre strictement entre ces seuils.

Les nouveaux enrichissements conservent le nombre d’événements d’objet rejetés pour horodatage inexploitable. Stuff exclut alors la partie des timings qualifiés, quel que soit le participant concerné. Les événements perdus lors d’anciens imports ne peuvent pas être reconstruits depuis la base normalisée ; cette correction ne certifie pas rétroactivement leur absence.

Le panneau de rang détaillé affiche médiane, moyenne et plage sur les rangs exploitables, puis les effectifs observés, classés, non classés, indisponibles et non récupérés. Une médiane paire utilise la moyenne des deux scores centraux. Les agrégats apex restent Master+ ; les observations individuelles conservent leur tier Riot. Dates de première et dernière observation visibles, sans appel Riot à l’ouverture.

**Progression** présente jusqu’à trois écarts avant le tableau, avec dix valeurs exploitables minimum dans chaque période. Ordre fixe : gold @15, CS/min, morts/min, gold @10, vision/min, dégâts/min, KDA. Aucun score entre unités différentes. Effectifs, portée, mélange éventuel des patchs et sources exactes restent visibles.
