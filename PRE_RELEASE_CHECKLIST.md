# Lobbying Signals Prerelease Checklist

Status date: 2026-05-08

This is the practical checklist for releasing Lobbying Signals as a low-stakes personal project. It intentionally excludes nice-to-have polish and focuses on things that would break the site, make the data look unserious, or make the launch feel unfinished.

## Must Fix Before Sharing

- [ ] Make the public URL work. *(requires GitHub Pages/repo setup)*
  - Target URL: `https://umqadir.github.io/lobbying-signals/`.
  - Configure GitHub Pages to serve from `/docs`, or publish the static dashboard somewhere else.

- [ ] Connect this local repo to the GitHub repo. *(requires knowing the intended remote)*
  - `git remote -v` currently returns no remotes.
  - Add the intended remote before trying to push or rely on Actions/Pages.

- [x] Fix refresh idempotency.
  - Re-ingesting a filing can currently append duplicate activities because existing filings are skipped but their activities are inserted again.
  - This is the main real pipeline bug to fix before scheduled autoupdates.
  - Add a uniqueness guard or skip activity insertion when the filing already exists.

- [ ] Run one clean manual refresh in GitHub Actions. *(requires pushed repo, secrets, and GitHub access)*
  - Verify the workflow can download/upload `data/filings.db`, run `07_refresh.py`, commit `docs/data/`, and update Pages.
  - Confirm required secrets exist: `GEMINI_API_KEY` or `GOOGLE_API_KEY`; `LDA_API_KEY` is optional but recommended.

- [ ] Make sure the launch data is current enough. *(do at launch after the hosted refresh path works)*
  - The stale local export is expected right now, but the public version should show a recent generated timestamp and filing range.
  - After refreshing, open the deployed site and confirm the UI does not say something like “Updated 84d ago.”

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

- [ ] Replace old screenshots if using screenshots in the post. *(only needed if those files are used in the LinkedIn post)*
  - Existing checked-in screenshots show an older briefing-style UI.
  - Current app is the two-panel signal browser.

- [x] Add LinkedIn/social preview metadata.
  - Add Open Graph title, description, and preview image to `docs/index.html`.
  - This prevents the link from unfurling as a generic or blank page.

## Quick Final Smoke Test

- [ ] Public URL returns `200`. *(requires hosted deployment)*
- [x] Dashboard loads with no console errors.
- [ ] Data timestamp is recent. *(requires launch refresh)*
- [x] Top few signals look plausible to a human.
- [ ] GitHub Action has succeeded once manually. *(requires GitHub access/secrets)*
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
