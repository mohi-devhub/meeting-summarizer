# Meeting Summarizer Bot

An internal Discord bot for recording and summarizing voice meetings. Built for RUXAILAB to help contributors catch up on missed meetings.

## Overview

The bot records Discord voice channel meetings in fixed-size chunks, transcribes them using a cloud STT API (Groq), and posts structured summaries to text channels.

**Key Features:**
- Chunk-based audio recording with state tracking
- Automatic retry on transcription failures
- Crash recovery (survives process/VM restarts)
- Post-meeting batch processing
- Near-zero cost operation (free tier APIs)

**Deployment Target:**
- Google Cloud e2-micro VM (always-on)
- Focus on stability and reliability over performance

## Project Structure

```
meeting-summarizer/
├── bot/
│   ├── main.py                  # Discord bot entry point
│   ├── meeting_state.py         # Meeting session management
│   ├── audio_recorder.py        # Audio recording logic
│   ├── chunk_state_manager.py   # Chunk state tracking & retry
│   └── recovery_manager.py      # Crash recovery system
├── state/                       # Chunk state persistence (JSON files)
├── recordings/                  # Audio recordings by meeting
├── requirements.txt
└── README.md
```

## Setup Instructions

### Prerequisites

- Python 3.8+
- Discord bot token
- Voice-capable Discord server for testing

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd meeting-summarizer
   ```

2. **Create a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   ```
   
   Edit `.env` and add your Discord bot token:
   ```
   DISCORD_BOT_TOKEN=your_bot_token_here
   ```

5. **Create required directories**
   ```bash
   mkdir -p recordings state
   ```

### Running the Bot

```bash
cd bot
python main.py
```

The bot will:
- Load persisted chunk state (if any)
- Connect to Discord
- Register slash commands
- Begin crash recovery if needed

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start-meeting` | Join your voice channel and start recording |
| `/end-meeting` | Stop recording and leave voice channel |

## How It Works

### Recording Flow

1. User runs `/start-meeting` while in a voice channel
2. Bot joins voice channel and creates a meeting session
3. Audio is recorded per-user in the `recordings/<meeting-id>/` directory
4. Each recording chunk is tracked with state: `RECORDED` → `SENT` → `TRANSCRIBED`
5. User runs `/end-meeting` to stop recording

### Chunk State System

Each audio chunk has an explicit state:

- **RECORDED** - Chunk fully written to disk
- **SENT** - Chunk submitted for transcription
- **TRANSCRIBED** - Transcription completed successfully

**State transitions:**
- `RECORDED → SENT` (when submitting to API)
- `SENT → TRANSCRIBED` (on success)
- `SENT → RECORDED` (on failure, for retry)

All state is persisted to `state/<chunk-id>.json` files.

### Retry Mechanism

Failed chunks are automatically retried with:
- Independent retry per chunk
- Exponential backoff (2s, 4s, 8s, 16s, 32s)
- Maximum 5 retry attempts
- Clear logging of each attempt

Chunks exceeding max retries are marked as permanently failed and require manual intervention.

### Crash Recovery

On bot startup:
1. All chunk states are loaded from `state/` directory
2. Incomplete meetings are identified
3. Pending chunks (in `RECORDED` state) are queued for processing
4. Failed chunks are retried if under max retry limit

## Testing Guide

### Testing Locally

1. **Start the bot**
   ```bash
   cd bot
   python main.py
   ```

2. **Join a voice channel in Discord**

3. **Start a test meeting**
   ```
   /start-meeting
   ```

4. **Speak or play audio** (bot records all participants)

5. **End the meeting**
   ```
   /end-meeting
   ```

6. **Check recordings**
   ```bash
   ls -la recordings/<meeting-id>/
   ```

### Testing Chunk State Tracking

See [examples/test_chunk_state.py](examples/test_chunk_state.py) for a standalone test demonstrating:
- Chunk registration
- State transitions
- Retry handling
- Persistence and recovery

Run the test:
```bash
python examples/test_chunk_state.py
```

### Testing Crash Recovery

**Simulate a crash during recording:**

1. Start a meeting with `/start-meeting`
2. Kill the bot process (Ctrl+C or `kill <pid>`)
3. Restart the bot
4. Check logs for recovery report

**Expected behavior:**
- Bot loads persisted chunk states
- Identifies incomplete meetings
- Logs a recovery report with counts
- Pending chunks remain visible until processed

**Verify recovery:**
```bash
# Check state files
ls -la state/

# Inspect a chunk state file
cat state/<chunk-id>.json
```

**Simulate chunk processing failure:**

This requires integration with the transcription API (out of scope for this sub-issue), but you can manually test state transitions:

```python
from bot.chunk_state_manager import ChunkStateManager, ChunkState

manager = ChunkStateManager()
manager.load_state()

# Simulate failed chunk
chunk_id = "test-chunk-001"
manager.mark_chunk_failed(chunk_id, "Simulated API timeout")

# Check retry count
chunk = manager.get_chunk(chunk_id)
print(f"Retry count: {chunk.retry_count}")
print(f"State: {chunk.state}")
```

### Testing Meeting Completion

A meeting is only complete when **all chunks are TRANSCRIBED**.

```python
from bot.chunk_state_manager import ChunkStateManager

manager = ChunkStateManager()
manager.load_state()

# Check meeting completion
is_complete, status = manager.is_meeting_complete("<meeting-id>")
print(f"Complete: {is_complete}")
print(f"Status: {status}")

# Get summary
summary = manager.get_summary("<meeting-id>")
print(f"Summary: {summary}")
```

## Debugging

### Where to Look When Something Fails

| Issue | Where to Check |
|-------|----------------|
| Bot won't start | `bot.log` (check token, permissions) |
| Recording not working | `bot.log` (audio sink errors) |
| Chunks not tracked | `state/` directory (should have .json files) |
| Chunks stuck in SENT | Check retry count in state file |
| Chunks permanently failed | `bot.log` (search for "exceeded max retries") |
| Meeting not completing | Run `get_summary()` to see chunk states |

### Log Locations

- **Bot logs:** `bot.log` (in the directory where you run the bot)
- **Chunk state files:** `state/<chunk-id>.json`
- **Audio recordings:** `recordings/<meeting-id>/`

### Useful Log Searches

```bash
# Find failed chunks
grep "exceeded max retries" bot.log

# Find retry attempts
grep "will be retried" bot.log

# Find state transitions
grep "transitioned:" bot.log

# Find recovery operations
grep "Recovery" bot.log
```

### Inspecting State

```bash
# List all chunk states
ls -la state/

# View a specific chunk
cat state/<chunk-id>.json | python -m json.tool

# Count chunks by meeting
for file in state/*.json; do
  meeting_id=$(jq -r '.meeting_id' "$file")
  echo "$meeting_id"
done | sort | uniq -c
```

### Common Issues

**Issue:** Chunks not persisting after crash
- **Cause:** State directory doesn't exist or insufficient permissions
- **Fix:** Ensure `state/` directory exists with write permissions

**Issue:** Chunks stuck in SENT state after restart
- **Cause:** Bot crashed while waiting for transcription
- **Fix:** Recovery manager will reset to RECORDED on startup (check logs)

**Issue:** Meeting shows complete but summary is empty
- **Cause:** Chunks were cleared or state files deleted
- **Fix:** Check `recordings/` for audio files and regenerate state if needed

## Configuration

### Retry Settings

Edit `bot/chunk_state_manager.py`:

```python
class ChunkStateManager:
    def __init__(self, state_dir: str = "state"):
        # ...
        self.max_retries = 5                    # Maximum retry attempts
        self.retry_base_delay = 2               # Base delay in seconds
        self.use_exponential_backoff = True     # Enable exponential backoff
```

### Reconnection Settings

Edit `bot/main.py`:

```python
class MeetingBot(commands.Bot):
    def __init__(self):
        # ...
        self.max_reconnect_attempts = 3
        self.reconnect_base_delay = 2
```

## Architecture Notes

### Design Decisions

1. **JSON persistence over database**
   - Simpler deployment (no DB setup)
   - File-based is sufficient for low volume
   - Easy to inspect/debug manually

2. **Chunk-based processing**
   - Limits memory usage
   - Enables incremental progress
   - Makes failures recoverable

3. **Explicit state transitions**
   - No silent failures
   - Clear audit trail
   - Easy to debug

4. **Independent retry per chunk**
   - One failed chunk doesn't block others
   - Idempotent retry logic
   - Prevents cascading failures

### State Persistence

Each chunk has a JSON file in `state/`:

```json
{
  "chunk_id": "abc123",
  "meeting_id": "def456",
  "file_path": "recordings/def456/chunk_001.wav",
  "state": "recorded",
  "created_at": "2026-01-25T10:00:00",
  "updated_at": "2026-01-25T10:00:05",
  "retry_count": 0,
  "last_error": null,
  "transcription_result": null
}
```

### Meeting Completion Guarantee

A meeting is considered complete **only when**:
- All chunks exist in state
- All chunks are in `TRANSCRIBED` state
- No chunks have permanently failed (exceeded max retries)

This is validated with `ChunkStateManager.is_meeting_complete()`.

## Contributing

This is internal tooling for RUXAILAB. See [CONTRIBUTING.md](CONTRIBUTING.md) for developer guidelines, integration patterns, and testing strategies.

## License

See [LICENSE](LICENSE) for details.
