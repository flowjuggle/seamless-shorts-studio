# GitHub Pages Setup (Consent-Gated)

> **Important:** Creating a GitHub repository or publishing a live GitHub Pages site **requires your explicit consent** and is **never triggered automatically**. The template files in this project only prepare the build locally.

## What Is Already Prepared

- `tools/build_pages.sh`: refreshes `web/Seamless_Shorts_Player.html` via `python3 tools/build_standalone.py` and builds a clean static site folder in `dist/site/` containing:
  - `index.html`
  - `styles.css`
  - `app.js`
  - `jszip.min.js`
  - `Seamless_Shorts_Player.html`
  - `.nojekyll`
- `.github/workflows/pages.yml`: ready-to-use GitHub Actions workflow using `actions/checkout@v4`, `actions/setup-python@v5`, `bash tools/build_pages.sh`, `actions/configure-pages@v5`, `actions/upload-pages-artifact@v3`, and `actions/deploy-pages@v4`.

## How to Test the Static Bundle Locally

Run:

```bash
bash tools/build_pages.sh
```

Or to output into a custom folder:

```bash
bash tools/build_pages.sh /path/to/output_site
```

Even without hosting anywhere, double-clicking `web/Seamless_Shorts_Player.html` (or `dist/site/Seamless_Shorts_Player.html`) opens the full Standalone Player right on your computer.

## How to Publish on GitHub Pages (Only When You Decide To)

1. Confirm you want a GitHub repository and public static site created.
2. Create a new GitHub repository and push this project to the `main` branch.
3. On GitHub, open **Settings -> Pages**.
4. Under **Build and deployment -> Source**, select **GitHub Actions**.
5. Run the **Deploy Standalone Player to GitHub Pages** workflow (or push a change to `main`).
6. GitHub will publish your static `https://<username>.github.io/<repo>/` URL where anyone can use the browser-only player with zero installation.
