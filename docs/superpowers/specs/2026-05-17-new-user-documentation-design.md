# New user documentation design

## Goal

Improve the project documentation for new users who already have NZBHydra2 or
Prowlarr and nzbdav running, and who need a clear path from "services are
available" to "TMDBHelper can play through NeNeTeePee-Stream-Kodi."

The primary failure point to address is TMDBHelper integration: installing the
NeNeTeePee-Stream-Kodi player file, refreshing TMDBHelper's player list, setting NeNeTeePee-Stream-Kodi as the
default movie and TV player, and recognizing when the setup succeeded.

## Audience

Primary audience:

- Users with Kodi 21 or later.
- Users who already operate NZBHydra2 or Prowlarr.
- Users who already operate nzbdav and have its API and WebDAV credentials.
- Users who want exact setup order more than architecture details.

Secondary audience:

- Existing users troubleshooting why TMDBHelper does not show NeNeTeePee-Stream-Kodi or why a
  play request never reaches the NeNeTeePee-Stream-Kodi result dialog.
- Contributors who need the README to route readers to the right deeper docs.

## Documentation shape

Use a modest split rather than a large documentation rewrite:

- `README.md`: overview, requirements, install entry points, links to first-time
  setup and troubleshooting, short configuration reference, short playback
  behavior explanation, and contributor links.
- `docs/quickstart.md`: the first-time setup path for users with existing
  backend services.
- `docs/troubleshooting.md`: user-facing failure diagnosis, led by TMDBHelper
  player setup issues.

Keep the existing contributor and architecture docs:

- `docs/architecture.md`: contributor-level flow overview.
- `docs/proxy-architecture.md`: deep proxy internals.
- `CHANGELOG.md`: release history.
- `CONTRIBUTING.md`, `SUPPORT.md`, and `AGENTS.md`: process docs.

## README design

The README should answer "what is this?" and then route new users into the
quickstart before presenting advanced internals.

Recommended top-level order:

1. Short description of NeNeTeePee-Stream-Kodi and the TMDBHelper flow.
2. Requirements.
3. Install via the Kodi repository, with manual install as a fallback.
4. Prominent link to `docs/quickstart.md` for first-time setup.
5. Compact configuration reference.
6. Compact playback behavior section explaining that playback goes through a
   local proxy and linking to `docs/proxy-architecture.md`.
7. Links to troubleshooting, architecture, changelog, contributing, and support.

De-emphasize or move later:

- Long release-highlight blocks.
- Long proxy/fallback internals.
- Independent TMDBHelper cache warmup material.

The TMDBHelper cache warmup material can remain in the README for the first pass
if moving it would make the documentation change too broad. A later pass should
move it to a dedicated doc such as `docs/tmdbhelper-cache-warmup.md`.

## Quickstart design

`docs/quickstart.md` should be a linear golden path:

1. Confirm prerequisites:
   - Kodi 21 or later.
   - TMDBHelper installed or installable.
   - NZBHydra2 or Prowlarr URL and API key.
   - nzbdav URL and API key.
   - nzbdav WebDAV username and password.
2. Install NeNeTeePee-Stream-Kodi through the Kodi repository.
3. Open NeNeTeePee-Stream-Kodi settings and enter service credentials.
4. Use the NeNeTeePee-Stream-Kodi **Install Player File** button.
5. Restart Kodi or use TMDBHelper **Update players**.
6. Set NeNeTeePee-Stream-Kodi as the default player for both movies and TV shows.
7. Verify with one known movie or episode.
8. Link to troubleshooting if NeNeTeePee-Stream-Kodi does not appear or playback does not start.

The quickstart should treat the **Install Player File** button as the normal and
recommended path. Manual TMDBHelper player file placement should not appear in
the main flow.

## TMDBHelper setup details

The TMDBHelper section should be verification-oriented:

1. Install TMDBHelper from the official Kodi repository, with GitHub releases as
   a fallback only.
2. Configure TMDBHelper enough to browse movies and episodes.
3. Install the NeNeTeePee-Stream-Kodi player file from NeNeTeePee-Stream-Kodi settings.
4. Refresh TMDBHelper players or restart Kodi.
5. Confirm NeNeTeePee-Stream-Kodi appears in TMDBHelper player choices.
6. Set NeNeTeePee-Stream-Kodi as the default player for movies and TV shows.
7. Verify that selecting a known title opens the NeNeTeePee-Stream-Kodi result dialog or starts
   the auto-select resolve path.

Manual player-file placement belongs only in troubleshooting as an advanced
recovery step after the button path, player refresh, and Kodi restart have been
tried.

## Troubleshooting design

`docs/troubleshooting.md` should start with the most common setup problem and
then proceed toward playback issues:

1. NeNeTeePee-Stream-Kodi does not appear as a TMDBHelper player.
2. TMDBHelper opens, but the NeNeTeePee-Stream-Kodi result dialog never appears.
3. The result dialog appears, but no results are shown.
4. nzbdav submission waits for too long or fails.
5. WebDAV or authentication errors.
6. Playback starts and then fails.
7. CoreELEC, large-file, proxy, and ffmpeg notes.
8. What to include in a GitHub issue.

For "NeNeTeePee-Stream-Kodi does not appear as a TMDBHelper player," the first recovery steps
should be:

1. Re-run **Install Player File** from NeNeTeePee-Stream-Kodi settings.
2. Use TMDBHelper **Update players**.
3. Restart Kodi.
4. Confirm the TMDBHelper addon data directory exists.
5. Check `kodi.log` for player install messages.
6. Use manual player-file placement only as an advanced recovery path.

## Consistency fixes

The documentation pass should also fix known stale references:

- `SUPPORT.md` links to `troubleshooting.md`, which does not exist yet.
- The README proxy deep-dive link points at `TODO.md` even though
  `docs/proxy-architecture.md` exists.
- `TODO.md` has a stale addon version reference.
- README and `AGENTS.md` test counts differ from current command output.

These are documentation consistency fixes only; they should not change addon
behavior.

## Out of scope

This design does not include:

- Code changes.
- Kodi UI changes.
- New screenshots, unless a later implementation pass decides they are worth
  adding.
- A full rewrite of architecture documentation.
- A full migration of the TMDBHelper cache warmup material.

## Success criteria

The documentation change succeeds when a new user with running backend services
can follow one path from README to quickstart and understand:

- Which credentials they need before starting Kodi setup.
- Where to click to install the TMDBHelper player file.
- How to refresh TMDBHelper's player list.
- How to set NeNeTeePee-Stream-Kodi as the default player for both movies and TV shows.
- What success looks like on first playback.
- Where to go when NeNeTeePee-Stream-Kodi does not appear in TMDBHelper.
