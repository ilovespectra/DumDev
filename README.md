# DumDev

**A free, local-first writing companion.** DumDev uses a locally installed Ollama model to draft one social post at a time from notes you provide. You review and edit it, copy it if useful, and decide what to do yourself.

DumDev is not an autoposter or account bot. It does not read X pages, timelines, browser profiles, cookies, passwords, or account data; it does not automate clicks, publish posts or replies, follow accounts, or work around platform restrictions. The browser choice only opens X's sign-in page when you explicitly ask; DumDev does not handle login or control that browser. No X API key, developer account, browser extension, or extra site permission is required.

## Quick start

### One-time setup

1. Install **Python 3.10+** and [Ollama](https://ollama.com) on macOS.
2. In Terminal, download one local model, for example:

   ```sh
   ollama pull llama3.2:3b
   ```

### Start DumDev

1. Clone or download this repository and open its folder in Finder.
2. Double-click **`start.command`**. The local dashboard opens in your browser.
3. In the first-run wizard, choose the browser you use, select an installed Ollama model, and optionally set topics, your own voice notes, and a drafting cadence.

No Python packages need to be installed. If macOS blocks `start.command`, Control-click it, choose **Open**, and confirm only after reviewing the source. The Terminal alternative is `python3 app.py`. DumDev serves only on `127.0.0.1` (ports 8766–8780) and opens its local dashboard; press Control-C in its Terminal to stop it.

If Ollama is already running, DumDev can list installed models. If not, open Ollama and retry. The local API must be available at `127.0.0.1:11434`.

## First run and dashboard

- Browser selection is optional setup guidance. **Open X sign-in** only opens the login page in the chosen installed browser. Sign in and manage permissions there yourself; DumDev does not read or control the page.
- Choose a model already installed in Ollama. The example above is a small general-purpose model; use any local model compatible with Ollama.
- Add up to 12 topic keywords and up to 500 characters of notes describing **your own** writing style. Do not use another person's identity or private information.
- Drafting cadence is a planning preference only. DumDev has no scheduler and does not create or publish drafts on a timer.
- Enter your own source notes, ask for one draft, then review, edit, verify, and copy it manually if you want. Every generated result is marked unpublished.
- Pause blocks new draft requests. Stop asks DumDev to discard a result if it arrives late; Ollama may finish the local request already in progress before DumDev can discard it.

If X displays a challenge, rate limit, or restriction notice, stop and follow X's instructions. DumDev does not attempt to evade it.

## What was adapted

The original local bot combines Ollama drafting with browser automation, feed discovery, replies, follows, account-specific prompts, and saved browser/session state. DumDev keeps the portable ideas—local Ollama drafting, user-configurable topics and voice, a cadence preference, and a local status dashboard—but intentionally excludes account actions and account-specific material. The original bot folder and its live configuration are not part of this project.

## Privacy and local data

- Settings are stored on this Mac at `~/Library/Application Support/DumDev/config.json`, with restrictive local file permissions.
- The open page holds the current draft; DumDev does not create a draft-history or activity log.
- Draft notes and the prompt are sent only to the local Ollama service at `127.0.0.1:11434`. Ollama uses the model you installed; review that model's own privacy and license terms.
- DumDev does not make X API calls or retrieve X content. Opening external help pages or X sign-in is an explicit action in your browser.
- The repository contains no account settings, credentials, personal browser profiles, browser cookies, logs, or local model data.

## Verify

```sh
python3 -m unittest discover -s tests -v
```

Tests use temporary settings and a local-only mock Ollama service. They do not open a browser, contact X, or publish anything.

## Free software

DumDev is free software under the MIT License. It is independent and is not affiliated with or endorsed by X or Ollama. AI output can be wrong: verify every claim and follow the platform's current terms.
