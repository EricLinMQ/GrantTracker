# CorriLee Grant Ranker

A desktop grant finder that ranks public grant pages for CorriLee's regional
screenings and community education work. Runs without AI APIs, subscriptions or
API keys. Scoring is deterministic and each contribution has a supporting excerpt.

## What you get

- A desktop shortlist ordered by screening score, with possible conflicts below
  other candidates and closed rounds excluded.
- An Excel report with numeric ranks and scores, source links, outstanding checks,
  a **Screening evidence** sheet with all four contributions, and search coverage.
- The original bounded public-source crawler: 13 configured sources, national
  regional opportunities with NSW sources emphasised. NSW itself earns no bonus.

Scores measure relevant wording found, **not eligibility or the probability of
winning**. A low score can reflect unreadable or incomplete guidelines. Read the
source guidelines and confirm every essential requirement before applying.

## Screening rules (version 1.0)

| Category | Levels; highest matching level only |
| --- | --- |
| Activities / 35 | Screening or touring: 35; community education, awareness activities or facilitated discussion: 25; community events: 10 |
| Mission / 30 | Child sexual abuse or incest: 30; sexual violence/assault or child protection/safety: 20; broader community wellbeing, welfare, inclusion or mental health: 10 |
| Regional / 25 | Regional, rural or remote communities: 25 |
| Applicants / 10 | Charities or nonprofits explicitly mentioned as eligible applicants: 10 |

No supporting evidence earns zero **screening points**, not a judgement of mismatch.
There are no frequency bonuses. Deadlines, amounts, budget, application effort and
state names do not change the score. Invitations are a separate access flag.

The scorer prefers recognised objectives, eligible-activity and applicant sections.
Otherwise a matching sentence needs explicit funding/support wording; applicant
matches need an applicant/eligibility cue. Recognised history sections are ignored.
Exclusion sections and sentences with negative or restrictive wording are withheld
and flagged where relevant. Negation such as "film screenings are not eligible"
cannot earn points; "not-for-profit" is treated as an organisation type.

These are conservative English text rules, not a language-understanding system.
They can miss synonyms, infer section boundaries incorrectly, or flag harmless
restrictions (including "only") for review. Scanned PDFs, JavaScript pages and
unread guidelines may be unavailable. No score certifies complete evidence.

**Ordering:** current candidates without detected conflicts first, then candidates
with possible conflicts, each group sorted by score descending, title, and URL.
Known closed rounds are on a separate sheet. Existing profile conflicts and
unrelated program-title flags also affect grouping. The desktop and workbook share
one ordering function. An urgent deadline does not lift a weak fit above a stronger
one. Full score evidence remains in the workbook, including for closed rounds.

## Run from source

Use Python 3.10+ with Tk (Python 3.12 recommended). The basic HTML crawler, scorer,
Excel writer and offline tests use the standard library. `pypdf` enables readable
PDF extraction and `certifi` supplies certificates in packaged builds.

```sh
python -m pip install -r requirements-build.txt
python desktop_app.py
```

Search grants, then choose **Save Excel**. Double-click a result to open its source.
The CLI remains available through `python grant_finder.py --help`.
A legacy optional web-search integration in the CLI is disabled by default;
it is not an AI scoring service and is not used by the desktop app.

Edit `config.json` to confirm towns, dates, budget and registration details. No
host town, grant budget or event date is assumed. Editing a saved workbook does not
rerun the scorer; make profile changes before the next search.

## Tests and builds

```sh
python -m unittest discover -s . -v
```

GitHub Actions runs offline regression tests on pushes and pull requests. For
end-user downloads, select **Actions > Build desktop apps > Run workflow**.
The manual workflow builds Windows x64, Mac Intel and Mac Apple Silicon packages,
runs native GUI/Excel smoke tests, and uploads ZIPs plus SHA-256 checksums. Artifact
retention is 30 days; downloaded apps do not expire. No automatic native builds
are triggered by a source push.

The build is unsigned (Mac uses PyInstaller ad-hoc signing), not Apple notarised.
Native build results and clean-machine launch tests must be checked before release.
Previous Grant Finder ZIPs do not contain the new ranking; build this repository
to obtain Grant Ranker packages. `python build.py` is for Windows/macOS maintainers.

## Project files

- `screening.py`: versioned rules, evidence, flags and shared ordering.
- `grant_finder.py`: public-page discovery, existing eligibility/date checks and Excel export.
- `desktop_app.py`: Tk interface with ranked scores.
- `test_screening.py`: scoring and workbook integration regressions.
- `config.json`: public source list and organisation/project profile.

Based on the supplied CorriLee Grant Finder desktop source. The source repository
contains no project brief PDF, personal contact list, credentials or survivor stories.
