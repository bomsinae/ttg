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

## 한국어 안내

### 개요

이 프로젝트는 일반 텔레그램 계정을 위한 터미널 클라이언트입니다.
봇 API가 아니라 MTProto(`Telethon`)를 사용합니다.

### 1) 텔레그램 API 정보 만들기

1. `https://my.telegram.org` 에 접속합니다.
2. 전화번호로 로그인합니다.
3. `API development tools` 로 이동합니다.
4. 앱을 만든 뒤 아래 값을 복사합니다.
   - `api_id`
   - `api_hash`

### 2) 환경 변수 설정

```bash
cp .env.example .env
```

`.env` 파일을 열고 실제 값으로 바꿉니다.

```bash
TG_API_ID=...
TG_API_HASH=...
TG_SESSION_NAME=tg_terminal
TTG_CONFIG_PATH=ttg_config.json
```

### 3) 선택 설정 파일 (`ttg_config.json`)

```bash
cp ttg_config.example.json ttg_config.json
```

이 파일에서는 새로고침 주기, 히스토리 개수, 키 바인딩, 로그 위치 등을 조절할 수 있습니다.
`TTG_CONFIG_PATH` 가 없거나 파일이 없으면 기본값으로 실행됩니다.

### 4) 실행

권장 실행 방법:

```bash
./run.sh
```

`run.sh` 는 다음 일을 합니다.
- 필요하면 `.venv` 생성
- `requirements.txt` 가 바뀌었으면 의존성 설치/업데이트
- `tg_client.py` 실행

직접 실행할 수도 있습니다.

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python tg_client.py
```

최초 실행 시 텔레그램이 전화번호, 인증 코드, 필요하면 2차 비밀번호를 요청합니다.
세션은 `<TG_SESSION_NAME>.session` 파일에 저장됩니다.

### 5) 패키지 설치 (선택)

CLI 패키지 형태로 설치할 수 있습니다.

```bash
python3 -m pip install .
ttg
```

격리된 사용자 설치를 원하면:

```bash
pipx install .
ttg
```

### 조작법

- 앱은 대화 목록 화면에서 시작합니다.
- `Up` / `Down`: 대화 선택 이동
- `PageUp` / `PageDown`: 대화 목록에서 페이지 단위 이동
- `Enter`: 선택한 대화 열기
- `Esc`: 대화 목록 화면에서 종료
- `r`: 대화 목록 새로고침
- 채팅 입력창은 2줄입니다.
- `Left` / `Right`: 입력 커서 이동
- `Up` / `Down`: 채팅 히스토리 스크롤
- `PageUp` / `PageDown`: 채팅을 페이지 단위로 스크롤
- 위쪽 끝까지 올린 상태에서 계속 `Up` 을 누르면 이전 메시지를 더 불러옵니다.
- `Enter`: 위로 스크롤된 상태면 먼저 맨 아래로 이동, 아니면 메시지 전송
- `Ctrl+N`: 줄바꿈
- `Ctrl+E`: 가장 최근 메시지부터 선택, 한 번 더 누르면 더 오래된 메시지 선택
- `Ctrl+R`: 더 최신 메시지 쪽으로 이동
- `p`: 선택한 이미지 메시지를 터미널에서 미리보기
- `Ctrl+W`: 선택한 미디어 메시지 저장
- `Ctrl+D`: 내가 보낸 선택 메시지 삭제(확인 대화상자 포함)
- `Ctrl+G`: 메시지 선택/수정 모드 취소
- `/s <query>`: 검색 시작 후 가장 최근 검색 결과로 이동
- `/file <path> [caption]`: 로컬 파일 전송
- 검색 모드에서 `Ctrl+N` / `Ctrl+P`: 검색 결과 이동
- 검색 모드에서 첫 번째 `Esc`: 검색 해제, 두 번째 `Esc`: 대화 목록으로 복귀
- 내가 보낸 메시지에는 `✓`(전송됨), `✓✓`(상대가 읽음, 주로 1:1 대화)가 표시됩니다.

### 로그

- 기본 로그 파일: `logs/ttg.log`
- 기본 로그 회전: 1MB x 3개 백업
- 로그에는 기본적으로 민감정보와 전화번호 형태의 문자열을 마스킹합니다.
- 로그 파일 경로, 레벨, 회전, 마스킹 설정은 `ttg_config.json` 의 `logging` 항목에서 바꿀 수 있습니다.
- 로그 정리 명령:

```bash
ttg --clean-logs
```

또는

```bash
./run.sh --clean-logs
```

### 테스트

```bash
python3 -m py_compile tg_client.py
./.venv/bin/python -m unittest -v tests/test_chat_key_bindings.py
```

### 참고

- `.env` 와 `.session` 파일은 외부에 공개하지 않는 것이 좋습니다.
- 많은 터미널은 수정 키 조합을 정확히 구분하지 못하므로, 줄바꿈은 `Ctrl+N` 을 사용하는 것이 가장 안정적입니다.
- 이미지 미리보기는 `img2sixel` 이 있고 터미널이 맞으면 `sixel` 을 먼저 시도합니다.
- `tmux` 밖에서는 `auto` 모드가 기본적으로 `sixel` 을 시도합니다.
- `tmux` 안에서는 `tmux` 가 client terminal features 에 `sixel` 을 보고할 때만 `sixel` 을 시도하고, 아니면 ANSI fallback 으로 내려갑니다.
- 미리보기 백엔드를 강제로 바꾸고 싶으면 `TTG_IMAGE_PREVIEW_MODE=auto|sixel|ansi` 를 사용할 수 있습니다.
- 터미널에서 이미지가 깨져 보이면 `TTG_IMAGE_PREVIEW_MODE=ansi` 로 실행하면 됩니다.
