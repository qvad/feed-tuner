# Feed Tuner

Feed Tuner is a local Playwright bot that searches X and Threads for topics you choose, ranks matching posts with your model provider, and opens selected posts with pauses and scrolling.

The aim is to influence your recommendations through your own browsing activity. It does not train either platform's model directly, and its effect on recommendations has not been measured. Both site adapters are experimental: platform layout changes, login requirements, and challenges can interrupt a run.

## Setup

Python 3.11 or later is required.

```sh
git clone https://github.com/qvad/feed-tuner.git
cd feed-tuner
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[test]'
playwright install chromium
```

On Linux, Playwright may also need system libraries: `playwright install-deps chromium`.

## Privacy and account access

Use a dedicated browser profile and sign in manually. Profiles contain session credentials: keep them private. Do not commit profiles, logs, API keys, or your personal configuration.

The model receives your configured topics and extracted post snippets. A remote provider receives that text outside your computer; choose a local provider if you want inference to stay local. Browser traffic still goes to X or Threads.

The bot opens posts and scrolls. It does not like, follow, repost, publish, or send messages. Pauses and varied scroll distances set the browsing pace. The bot does not include fingerprint spoofing or challenge bypasses. Review the platforms' current terms before using it with your account. X documents its [automation rules](https://help.x.com/en/rules-and-policies/x-automation).

## Limits

Searches and views can themselves affect account activity. A run without `--apply` still performs searches and views, even though it does not open selected posts.

A successful local fixture test demonstrates extraction and browser control against that fixture. It does not establish compatibility with a logged-in account or prove a change in recommendations.

Playwright's [persistent browser context documentation](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context) describes profile storage. Use a separate profile rather than your daily browser profile.

## Configure and run

```sh
cp config.example.toml config.toml
# Edit topics and model settings in config.toml.
feed-tuner login x --config config.toml
feed-tuner login threads --config config.toml
feed-tuner run --config config.toml
feed-tuner run --config config.toml --apply
```

The first run command searches, scrolls, and ranks results. Add `--apply` to open matching posts. Sign in through the visible browser during each login command. Run the platforms sequentially with the same configuration, or select just one in `platforms`.

The example limits a run to 5 minutes, 6 search snapshots, and 4 post opens. Limits apply across all platforms combined. Each topic gets up to 2 snapshots per platform; a small snapshot limit can end the run before later topics or platforms are reached. With `--apply`, it opens candidates scoring at least 0.7. These scores are model judgments, not calibrated probabilities. Edit `topics` to select what you want to read; `pause_seconds` controls pauses between actions. Press Ctrl+C to stop.

The default data directory is `~/.local/share/feed-tuner`. Use `data_dir` to change it. Profiles and JSONL run logs stay there, outside this repository by default.

## Custom model providers

Set an OpenAI-compatible base URL ending in `/v1` and the model name exposed by your server. For example, a local Ollama server can use:

```toml
[model]
base_url = "http://localhost:11434/v1"
name = "your-installed-model"
timeout_seconds = 30
```

For a hosted provider, use its HTTPS base URL and set `api_key_env` to the name of an environment variable containing your API key. The configuration stores the variable name, not the key. Unencrypted HTTP is accepted only for loopback endpoints.

The model can rank supplied post IDs. It cannot supply new navigation URLs or browser commands. Invalid responses stop the run rather than trigger a keyword fallback.

## Development

```sh
pytest
```

Tests use local fixtures and mocked model responses; they do not sign in to social accounts. Install Chromium before running browser fixture tests.
