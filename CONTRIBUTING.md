# Contributing to Meeting Summarizer Bot

This document provides developer-focused guidance for working on the meeting summarizer bot.

## Development Philosophy

This project prioritizes **correctness and reliability** over performance. The bot runs on a low-resource VM and processes recordings in batches after meetings end, so:

- Focus on stability and crash recovery
- Prefer simple, debuggable code over clever optimizations
- Log extensively for troubleshooting
- Make failures visible and explicit

## Code Organization

### Module Responsibilities

| Module | Purpose | State? |
|--------|---------|--------|
| `main.py` | Discord bot, commands, reconnection | Stateless |
| `meeting_state.py` | Active meeting tracking | In-memory |
| `audio_recorder.py` | Voice recording | Stateless |
| `chunk_state_manager.py` | Chunk state, retry, persistence | **Persisted** |
| `recovery_manager.py` | Crash recovery logic | Stateless |

## Chunk State System

### Core Concepts

**Chunk:** A recorded audio segment that must be transcribed

**State:** Current processing stage (RECORDED, SENT, TRANSCRIBED)

**Persistence:** All chunk state survives crashes

**Retry:** Failed chunks are retried independently up to max attempts

### State Transitions

```
RECORDED ──> SENT ──> TRANSCRIBED
              │
              └──> RECORDED (on failure, for retry)
```

Invalid transitions are rejected and logged.

### Integration Points

**When recording completes a chunk:**
```python
from chunk_state_manager import ChunkStateManager, ChunkState

manager = ChunkStateManager()
chunk = manager.register_chunk(
    chunk_id="unique-id",
    meeting_id="meeting-id",
    file_path="/path/to/chunk.wav"
)
# Chunk is now in RECORDED state
```

**When sending chunk for transcription:**
```python
# Mark as sent
manager.transition_chunk(chunk_id, ChunkState.SENT)

# Call transcription API...
try:
    result = await transcribe_api(chunk.file_path)
    manager.set_transcription_result(chunk_id, result)
    manager.transition_chunk(chunk_id, ChunkState.TRANSCRIBED)
except Exception as e:
    # Mark failed
    should_retry = manager.mark_chunk_failed(chunk_id, str(e))
    if should_retry:
        # Schedule retry with backoff
        delay = manager.get_retry_delay(chunk.retry_count)
        await asyncio.sleep(delay)
        # Process again...
```

**On bot startup:**
```python
from recovery_manager import RecoveryManager

manager = ChunkStateManager()
recovery = RecoveryManager(manager)

# Load state and get recovery summary
summary = recovery.recover_on_startup()

# Get pending work
pending = recovery.get_pending_work()
for chunk in pending:
    # Process chunk...
```

## Testing Your Changes

### Unit Testing

For isolated component tests, create files in `examples/`:

```bash
python examples/test_your_component.py
```

### Integration Testing

Test with the live bot:

1. Start bot: `cd bot && python main.py`
2. Join a voice channel in Discord
3. Run `/start-meeting`
4. Test your feature
5. Check logs: `tail -f bot.log`
6. Inspect state: `ls -la state/`

### Crash Testing

Simulate crashes during different stages:

```bash
# Start meeting
/start-meeting

# Kill bot
kill <pid>

# Restart bot
python main.py

# Check recovery logs
grep "Recovery" bot.log
```

## Debugging Tips

### Enable Verbose Logging

Edit module logger level:

```python
logging.getLogger('chunk_state_manager').setLevel(logging.DEBUG)
```

### Inspect Chunk State

```python
from chunk_state_manager import ChunkStateManager

manager = ChunkStateManager()
manager.load_state()

# Get specific chunk
chunk = manager.get_chunk("chunk-id")
print(chunk.to_dict())

# Get all chunks for a meeting
chunks = manager.get_chunks_by_meeting("meeting-id")
for chunk in chunks:
    print(f"{chunk.chunk_id}: {chunk.state.value}")

# Check completion
is_complete, status = manager.is_meeting_complete("meeting-id")
print(f"Complete: {is_complete}, Status: {status}")
```

### Useful Log Filters

```bash
# Failed chunks
grep "failed" bot.log | grep chunk

# State transitions
grep "transitioned" bot.log

# Retries
grep "will be retried" bot.log

# Recovery
grep -A 20 "RECOVERY REPORT" bot.log
```

### Manual State Inspection

State files are human-readable JSON:

```bash
# View a chunk
cat state/<chunk-id>.json | python -m json.tool

# Count states
jq -r '.state' state/*.json | sort | uniq -c
```

## Code Style

### General Guidelines

- **No emojis** in code, logs, or user messages (use clear text)
- Use explicit variable names (`chunk_state` not `cs`)
- Log state changes at INFO level
- Log errors with context (chunk ID, meeting ID)
- Validate inputs and fail fast with clear messages

### Error Handling

```python
# Good: Clear, actionable error
if chunk_id not in self._chunks:
    logger.error(f"Cannot transition unknown chunk: {chunk_id}")
    return False

# Bad: Silent failure
if chunk_id not in self._chunks:
    return False
```

### Logging

```python
# Good: Structured, searchable
logger.info(
    f"Chunk {chunk_id} transitioned: {old_state} -> {new_state}"
)

# Bad: Unstructured
logger.info("Changed state")
```

## Common Pitfalls

### 1. Forgetting to Persist State

**Wrong:**
```python
chunk.state = ChunkState.TRANSCRIBED  # Not persisted!
```

**Right:**
```python
manager.transition_chunk(chunk_id, ChunkState.TRANSCRIBED)
```

### 2. Not Checking Retry Count

**Wrong:**
```python
# Infinite retries!
manager.mark_chunk_failed(chunk_id, error)
# Process again...
```

**Right:**
```python
should_retry = manager.mark_chunk_failed(chunk_id, error)
if should_retry:
    # Process again
else:
    # Log permanent failure, alert, etc.
```

### 3. Assuming State Loaded

**Wrong:**
```python
manager = ChunkStateManager()
chunk = manager.get_chunk(chunk_id)  # May be None!
```

**Right:**
```python
manager = ChunkStateManager()
manager.load_state()  # Load persisted state first
chunk = manager.get_chunk(chunk_id)
if chunk is None:
    logger.error(f"Chunk {chunk_id} not found")
    return
```

### 4. Not Validating Transitions

**Wrong:**
```python
chunk.state = new_state  # Bypasses validation
```

**Right:**
```python
success = chunk.transition_to(new_state)
if not success:
    logger.error("Invalid transition")
    return
```

## Performance Considerations

This bot is **not** performance-critical. Optimize for correctness first.

**Acceptable:**
- One chunk at a time processing
- JSON file per chunk
- Synchronous I/O for state persistence

**Not Worth It:**
- Parallel chunk processing
- In-memory caching layers
- Async everything

**Watch Out For:**
- Accumulating state files (clear completed meetings)
- Large transcription results (store references, not full text)
- Unbounded retry loops

## Extending the System

### Adding New Chunk States

1. Add state to `ChunkState` enum
2. Update valid transitions in `ChunkMetadata.transition_to()`
3. Update `get_summary()` to count new state
4. Update documentation

### Adding New Retry Strategies

Edit `ChunkStateManager.get_retry_delay()`:

```python
def get_retry_delay(self, retry_count: int) -> float:
    # Linear backoff
    return self.retry_base_delay * retry_count
    
    # Exponential with cap
    return min(
        self.retry_base_delay * (2 ** retry_count),
        60  # Max 60 seconds
    )
```

### Adding Metrics/Monitoring

```python
def get_metrics(self, meeting_id: str) -> dict:
    chunks = self.get_chunks_by_meeting(meeting_id)
    return {
        "total_chunks": len(chunks),
        "avg_retry_count": sum(c.retry_count for c in chunks) / len(chunks),
        "max_retry_count": max(c.retry_count for c in chunks),
        "failed_chunks": len(self.get_failed_chunks(meeting_id))
    }
```