# Security notes

## API keys

**Never paste API keys into chat.** Every paste lands in:
- The chat transcript (visible to the model)
- Tool-call logs on disk
- Possibly cloud-side telemetry depending on the platform

The dotenv flow exists specifically to keep keys off-screen.

### How to set keys

1. Edit `B:/LLM/.env` in a regular text editor (not in chat). Format:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   OPENAI_API_KEY=sk-proj-...
   ```
2. Quotes optional. The dotenv loader strips matched quote pairs (including smart/curly quotes from Word/Notepad) automatically.

### How `.env` interacts with the shell environment

- The dotenv loader runs at process start (in `chain_test_v2.py:_load_dotenv`)
- For each key in `.env`, it sets `os.environ[key]` **only if the shell has not already set the same key**
- This means: if you paste a key into your shell (`set ANTHROPIC_API_KEY=...` or via a shell-injected paste), the shell value wins forever. Your `.env` value is silently shadowed.

If a key in `.env` doesn't seem to take effect, you'll see this warning at startup:
```
[dotenv] WARNING: shell environment shadows .env value for: ANTHROPIC_API_KEY
  Your .env value is being IGNORED.
```

To clear the shadow:
- **PowerShell:** `Remove-Item Env:ANTHROPIC_API_KEY`
- **cmd.exe:** `set ANTHROPIC_API_KEY=`
- **bash/zsh:** `unset ANTHROPIC_API_KEY`

Then restart the process.

### Key rotation

After any key exposure (chat paste, accidental commit, share with a colleague):

1. Rotate at https://console.anthropic.com/settings/keys (or equivalent for other providers)
2. Update `B:/LLM/.env` with the new value
3. If the old key was set in your shell, clear it (see above) — otherwise the new `.env` value gets shadowed
4. Confirm by running `python -c "from chain_test_v2 import _load_dotenv; _load_dotenv(); import os; print(os.environ.get('ANTHROPIC_API_KEY','')[:10])"` — should print the first 10 chars of the new key

## Subprocess invocation

The harness invokes `claude` as a subprocess. By default it passes `--dangerously-skip-permissions` to avoid interactive permission prompts (which would hang non-interactive runs).

For tighter security, set `USE_PERMISSION_ALLOWLIST=1` to use the per-target allowlist at `<target_repo>/.claude/settings.json` instead. See `C:/projects/httpx/.claude/settings.json` for the chain-experiment template.

The allowlist denies: `rm`, `curl`, `wget`, `sudo`, `pip install`, `npm install`, `git push`, `git reset --hard`, `git clean`, `WebFetch`, `WebSearch`.

## What this project does NOT protect against

- A malicious `.env` file (you'd have to have committed one)
- A compromised target repo (the agent has full read/write within the target dir by design)
- Anyone with filesystem access reading `.env` directly (it's gitignored but plaintext)
- Network exfiltration via the model itself — out of scope for an experimental harness
