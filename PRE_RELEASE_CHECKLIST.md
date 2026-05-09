# Lobbying Signals Prerelease Checklist

Status date: 2026-05-09

This is the practical checklist for releasing Lobbying Signals as a low-stakes personal project. It intentionally excludes nice-to-have polish and focuses on things that would break the site, make the data look unserious, or make the launch feel unfinished.

## Must Fix Before Sharing

- [x] Make the public URL work.
  - Target URL: `https://umqadir.github.io/lobbying-signals/`.
  - GitHub Pages serves from `/docs`; the public URL returns `HTTP 200`.

- [x] Connect this local repo to the GitHub repo.
  - Remote `origin` points to `git@github.com:umqadir/lobbying-signals.git`.
  - GitHub repo is public at `https://github.com/umqadir/lobbying-signals`.

- [x] Fix refresh idempotency.
  - Re-ingesting a filing can currently append duplicate activities because existing filings are skipped but their activities are inserted again.
  - This is the main real pipeline bug to fix before scheduled autoupdates.
  - Add a uniqueness guard or skip activity insertion when the filing already exists.

- [x] Run one clean manual refresh in GitHub Actions.
  - Verify the workflow can download/upload `data/filings.db`, run `07_refresh.py`, commit `docs/data/`, and update Pages.
  - Manual run `25609765180` succeeded on 2026-05-09.
  - Required repo secrets exist: `GEMINI_API_KEY`, `GOOGLE_API_KEY`, and `LDA_API_KEY`.

- [x] Make sure the launch data is current enough.
  - Public `docs/data/stats.json` shows `generated_at` `2026-05-09T19:50:43.655166`.
  - Filing date range now ends at `2026-05-09T15:45:30-04:00`.

- [x] Check the database storage limit.
  - Local `data/filings.db` is about `1.8GB`.
  - GitHub release assets have a `2 GiB` per-file limit, so this is close to the ceiling.
  - For a personal launch, it is okay to proceed if upload still works, but note that this will need a follow-up storage plan soon.

## Should Fix So It Looks Credible

- [x] Add a short methodology/data note.
  - Minimum note: source is Senate LDA filings; mentions are activity-level, not unique filings; comparisons are directional signals, not causal claims.
  - This can be a small note in the UI or README.

- [x] Address obvious label weirdness in the top results.
  - Examples seen locally: `VA`, `H.R. 1`, and fragmentary legislation labels like `and Related Agencies Appropriations`.
  - You do not need perfect normalization, but the top visible entries should not look broken.

- [x] Replace old screenshots if using screenshots in the post.
  - No old dashboard screenshots are tracked or referenced by the published site.
  - The only tracked PNG is the current `docs/social-preview.png` used for social metadata.

- [x] Add LinkedIn/social preview metadata.
  - Add Open Graph title, description, and preview image to `docs/index.html`.
  - This prevents the link from unfurling as a generic or blank page.

## Quick Final Smoke Test

- [x] Public URL returns `200`.
- [x] Dashboard loads with no console errors.
- [x] Data timestamp is recent.
- [x] Top few signals look plausible to a human.
- [x] GitHub Action has succeeded once manually.
- [x] Re-running ingestion does not create duplicate activity rows.
- [x] README matches what the app actually does.

## Not Required For This Release

- Full mobile redesign.
- Perfect payload optimization.
- Perfect taxonomy/normalization.
- A production database architecture.
- Automated quality gates.
- A highly polished landing page.
- Full test suite.

Those are good follow-ups, but they should not block a low-stakes personal-project release.
