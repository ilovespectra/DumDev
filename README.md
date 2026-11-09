# DumDev

A free, local writing companion. **Ollama is required**: it is DumDev's writing engine and runs on your Mac. DumDev turns notes you provide into one draft at a time. You review, edit, and copy it yourself.

## Install (no Terminal needed)

1. On the DumDev webpage, click **Download DumDev for Mac**. Open the ZIP and drag `DumDev.app` into **Applications**.
2. Install [Ollama for Mac](https://ollama.com/download/mac) and open the Ollama app. Keep it running while using DumDev.
3. Open DumDev. In setup, download the suggested local model with the **Download this model** button. Ollama handles the download; no commands are needed.
4. Choose optional topics and your own voice notes, then create a draft in the local dashboard.

DumDev is currently built for Apple silicon (macOS arm64). The app is ad-hoc signed, not notarized; macOS may ask you to confirm opening it. Review the release and its checksum before running it.

## What it does

DumDev sends draft notes only to Ollama on this Mac at `127.0.0.1:11434`. It does not read X, use X APIs, sign in, control the browser, scrape, auto-post, reply, follow, schedule activity, or bypass platform restrictions. Browser choice is optional and only opens the X sign-in page when you explicitly ask. A cadence is a planning note, not a timer. If X shows a challenge or limit, stop and follow X's instructions.

Preferences stay in `~/Library/Application Support/DumDev/config.json`; drafts remain in the open page and are not saved as history. Ollama models have their own hardware needs, licenses, and privacy terms.

## Developer release tools

Run `start.command` from Finder to open DumDev's local developer dashboard. The **Update binaries** panel lets you set a newer `major.minor.patch` version and build a self-contained macOS ZIP. It stages the app and SHA-256 manifest under `release/`; it does not publish or upload them. Add the new package and manifest to the webpage download folder when you are ready to release. The builder needs an internet connection once to install its isolated packaging tool.

## Verify from source

```sh
python3 -m unittest discover -s tests -v
```

The tests use temporary settings and a local mock Ollama server; they do not contact X or publish anything. The user-facing installer requires no command line.

Free software under MIT. Independent and not affiliated with X or Ollama. Verify AI-generated claims and follow current platform terms.
