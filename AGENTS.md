# Repository Guidelines

This repository is a static GitHub Pages site for the MIND project. Most changes are direct edits to HTML/CSS/JS and content assets—there is no build pipeline in this repo.

## Project Structure & Module Organization

- `/*.html`: Top-level pages (e.g., `index.html`, `competition.html`, `program.html`).
- `assets/css/`: Site stylesheets (layout and global styles).
- `assets/js/`: Bundled JavaScript used by pages (treat as generated unless you know the source).
- `assets/images/`: Images and small data files used by pages (e.g., `hashes.json`).
- `assets/doc/`: Documents referenced by the site (`.pdf`, `.pptx`, and `introduction.md`).

## Build, Test, and Development Commands

There is no build step. Preview changes locally with a static file server:

```sh
python3 -m http.server 8000
# then open http://localhost:8000/
```

Quick repo checks:

```sh
git status
git diff
```

## Coding Style & Naming Conventions

- Indentation: follow existing formatting (HTML is typically 4 spaces).
- Keep edits minimal: avoid large reformat-only diffs.
- Paths/URLs: prefer relative paths like `./assets/...` so the site works on GitHub Pages.
- Asset names: prefer lowercase and hyphenated names (e.g., `news-885x732.png`); avoid renaming existing files unless necessary.

## Testing Guidelines

No automated test suite is configured. Do a manual QA pass before submitting:

- Load key pages (`index.html`, `competition.html`, `workshop.html`) via the local server.
- Check browser console for errors and verify navigation and tables render correctly.
- If you update links (datasets/docs), click through to confirm they resolve.

## Commit & Pull Request Guidelines

Commit messages in history are short and action-oriented (e.g., “update leaderboard”, “fix expired dataset links”). Follow that pattern:

- Use an imperative verb + concise target (optionally include a date for leaderboard/content refreshes).

Pull requests should include:

- A brief description of what changed and why, with links to referenced issues/threads when applicable.
- Screenshots for visible UI/content changes.
- Notes about any large binary additions under `assets/doc/` or `assets/images/` (keep them to the minimum needed).

