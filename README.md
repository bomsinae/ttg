# Terminal Telegram Third-Party Client

This project provides a terminal Telegram client for a normal Telegram account
using MTProto (`Telethon`), not the Bot API.

## 1) Create Telegram API credentials

1. Open `https://my.telegram.org`
2. Sign in with your phone number
3. Go to `API development tools`
4. Create an app and copy:
   - `api_id`
   - `api_hash`

## 2) Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set your real values:

```bash
TG_API_ID=...
TG_API_HASH=...
TG_SESSION_NAME=tg_terminal
TTG_CONFIG_PATH=ttg_config.json
```

## 3) Optional app config (`ttg_config.json`)

```bash
cp ttg_config.example.json ttg_config.json
```

You can tune refresh intervals, history batch size, key bindings, and logging target.
If `TTG_CONFIG_PATH` is missing or the file does not exist, defaults are used.

## 4) Run

Recommended:

```bash
./run.sh
```

`run.sh` will:
- create `.venv` if needed
- install/update dependencies when `requirements.txt` changes
- start `tg_client.py`

Manual run is also possible:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python tg_client.py
```

On first run, Telegram asks for phone/code (and 2FA password if enabled).
Session is saved as `<TG_SESSION_NAME>.session`.

## 5) Package Install (Optional)

You can install this project as a CLI package:

```bash
python3 -m pip install .
ttg
```

For isolated user install:

```bash
pipx install .
ttg
```

## Controls (TUI)

- App opens in dialog list screen first
- Up / Down: move selection
- PageUp / PageDown: move selection by one page in dialog list
- `Enter`: open selected dialog
- `Esc`: quit (from dialog list)
- `r`: refresh dialogs (from dialog list)
- Chat input area has 2 lines
- Left / Right: move cursor inside the input box
- Up / Down: scroll chat history
- PageUp / PageDown: scroll chat by one page
- Reaching the top and continuing Up loads older history automatically
- `Enter`: if scrolled up, jump to bottom first; otherwise send message
- `Ctrl+N`: insert newline
- `Ctrl+E`: select latest message; press again for older messages
- `Ctrl+R`: move selection toward newer messages
- `p`: preview selected image media in the terminal
- `Ctrl+W`: save media from currently selected message (`Ctrl+E`/`Ctrl+R` selection)
- `Ctrl+D`: delete currently selected own message (with confirm dialog)
- `Ctrl+G`: cancel edit mode
- `/s <query>`: start search and jump to latest match
- `/file <path> [caption]`: send a local file
- In search mode: `Ctrl+N` / `Ctrl+P` to move through matches
- In search mode: first `Esc` clears search, second `Esc` returns to dialogs
- Outgoing messages show `✓` (sent) / `✓✓` (read by peer, mainly 1:1 chats)

## Logging

- Default log file: `logs/ttg.log`
- Log rotation defaults: 1 MB × 3 backups
- Log text redacts configured secrets and phone-like numbers by default
- Log level/file path/rotation/redaction can be changed in `ttg_config.json` under `logging`
- Cleanup command: `ttg --clean-logs` (or `./run.sh --clean-logs`)

## Tests

```bash
python3 -m py_compile tg_client.py
./.venv/bin/python -m unittest -v tests/test_chat_key_bindings.py
```

## Notes

- Keep `.env` and `.session` private.
- Many terminals cannot distinguish some modified key combos, so `Ctrl+N` is the reliable newline key.
- Image preview tries `sixel` first when `img2sixel` is available. Outside `tmux`, `auto` mode now attempts `sixel` by default. Inside `tmux`, it only does so if `tmux` reports `sixel` in the client terminal features; otherwise it falls back to ANSI blocks.
- Override preview backend with `TTG_IMAGE_PREVIEW_MODE=auto|sixel|ansi`. If your terminal renders garbage, use `ansi`.
