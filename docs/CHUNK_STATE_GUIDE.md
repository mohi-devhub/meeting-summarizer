# Chunk State Manager - Quick Start Guide

This guide shows how to integrate the chunk state manager into your component.

## Basic Setup

```python
from chunk_state_manager import ChunkStateManager, ChunkState

# Initialize (usually done once at bot startup)
manager = ChunkStateManager(state_dir="state")

# Load any persisted state (for crash recovery)
manager.load_state()
```

## Recording Component Integration

When your recording component finishes writing a chunk to disk:

```python
# Register the chunk
chunk = manager.register_chunk(
    chunk_id="unique-chunk-id",      # Generate unique ID
    meeting_id="meeting-session-id",  # Current meeting ID
    file_path="/path/to/chunk.wav"    # Path to audio file
)

# Chunk is now in RECORDED state and persisted to disk
```

## Transcription Component Integration

When processing chunks for transcription:

```python
# Get all chunks ready for processing
pending_chunks = manager.get_chunks_by_state(
    ChunkState.RECORDED,
    meeting_id="meeting-id"  # Optional: filter by meeting
)

for chunk in pending_chunks:
    # Skip if too many retries
    if chunk.retry_count >= manager.max_retries:
        continue
    
    # Apply retry delay if this is a retry
    if chunk.retry_count > 0:
        delay = manager.get_retry_delay(chunk.retry_count)
        await asyncio.sleep(delay)
    
    # Mark as sent before calling API
    manager.transition_chunk(chunk.chunk_id, ChunkState.SENT)
    
    try:
        # Call your transcription API
        result = await your_transcription_api(chunk.file_path)
        
        # Store result
        manager.set_transcription_result(chunk.chunk_id, result)
        
        # Mark as transcribed
        manager.transition_chunk(chunk.chunk_id, ChunkState.TRANSCRIBED)
        
    except Exception as e:
        # Mark failed (automatically transitions back to RECORDED if should retry)
        should_retry = manager.mark_chunk_failed(chunk.chunk_id, str(e))
        
        if not should_retry:
            # Exceeded max retries - log for manual intervention
            logger.error(f"Chunk {chunk.chunk_id} permanently failed")
```

## Bot Startup Integration

Add this to your bot's startup sequence:

```python
from recovery_manager import RecoveryManager

# Initialize managers
chunk_manager = ChunkStateManager()
recovery = RecoveryManager(chunk_manager)

# Perform crash recovery
recovery_summary = recovery.recover_on_startup()

# Log recovery report
recovery.log_recovery_report()

# Get pending work
pending = recovery.get_pending_work()
if pending:
    logger.info(f"Found {len(pending)} pending chunks to process")
    # Schedule processing...
```

## Checking Meeting Completion

Before generating a summary:

```python
# Check if all chunks are transcribed
is_complete, status = manager.is_meeting_complete(meeting_id)

if is_complete:
    # Safe to generate summary
    chunks = manager.get_chunks_by_meeting(meeting_id)
    transcripts = [c.transcription_result for c in chunks]
    # Generate summary...
    
    # Clean up state after summary is posted
    manager.clear_meeting(meeting_id)
else:
    # Not ready yet
    logger.warning(f"Meeting not complete: {status}")
    
    # Check for permanently failed chunks
    failed = manager.get_failed_chunks(meeting_id)
    if failed:
        # Alert: manual intervention needed
        logger.error(f"{len(failed)} chunks permanently failed")
```

## Error Handling Patterns

### Pattern 1: Simple Retry

```python
async def process_with_retry(chunk_id: str) -> bool:
    chunk = manager.get_chunk(chunk_id)
    
    manager.transition_chunk(chunk_id, ChunkState.SENT)
    
    try:
        result = await transcribe(chunk.file_path)
        manager.set_transcription_result(chunk_id, result)
        manager.transition_chunk(chunk_id, ChunkState.TRANSCRIBED)
        return True
    except Exception as e:
        should_retry = manager.mark_chunk_failed(chunk_id, str(e))
        return False
```

### Pattern 2: Batch Processing with Retry

```python
async def process_meeting(meeting_id: str):
    while True:
        pending = manager.get_chunks_by_state(
            ChunkState.RECORDED,
            meeting_id=meeting_id
        )
        
        # Filter out permanently failed
        pending = [c for c in pending if c.retry_count < manager.max_retries]
        
        if not pending:
            break
        
        for chunk in pending:
            # Apply backoff
            if chunk.retry_count > 0:
                delay = manager.get_retry_delay(chunk.retry_count)
                await asyncio.sleep(delay)
            
            await process_with_retry(chunk.chunk_id)
```

### Pattern 3: With Progress Tracking

```python
async def process_with_progress(meeting_id: str):
    summary = manager.get_summary(meeting_id)
    total = summary['total']
    
    while True:
        summary = manager.get_summary(meeting_id)
        completed = summary['transcribed']
        failed = summary['failed']
        
        logger.info(f"Progress: {completed}/{total} completed, {failed} failed")
        
        pending = manager.get_chunks_by_state(
            ChunkState.RECORDED,
            meeting_id=meeting_id
        )
        pending = [c for c in pending if c.retry_count < manager.max_retries]
        
        if not pending:
            break
        
        for chunk in pending:
            await process_with_retry(chunk.chunk_id)
```

## Configuration

Adjust retry behavior in your code:

```python
manager = ChunkStateManager()

# Customize retry settings
manager.max_retries = 3                    # Default: 5
manager.retry_base_delay = 5               # Default: 2 seconds
manager.use_exponential_backoff = True     # Default: True

# Exponential backoff delays:
# Retry 1: 5s
# Retry 2: 10s  
# Retry 3: 20s
```

## Common Operations

### Get Summary
```python
summary = manager.get_summary(meeting_id)
# Returns: {'total': 10, 'recorded': 2, 'sent': 1, 'transcribed': 7, 'failed': 0}
```

### Get Failed Chunks
```python
failed = manager.get_failed_chunks(meeting_id)
for chunk in failed:
    print(f"{chunk.chunk_id}: {chunk.last_error}")
```

### Clear Completed Meeting
```python
# Only clears chunks in terminal states (TRANSCRIBED or permanently failed)
cleared = manager.clear_meeting(meeting_id)
print(f"Cleared {cleared} chunks")
```

### Manual State Inspection
```python
chunk = manager.get_chunk(chunk_id)
print(f"State: {chunk.state.value}")
print(f"Retries: {chunk.retry_count}")
print(f"Last error: {chunk.last_error}")
print(f"Created: {chunk.created_at}")
print(f"Updated: {chunk.updated_at}")
```

## Testing Your Integration

Use the test script:

```bash
python examples/test_chunk_state.py
```

Or the integration example:

```bash
python examples/integration_example.py
```

## Troubleshooting

**Chunks not persisting:**
- Check that `state_dir` exists with write permissions
- Verify `_persist_chunk()` is being called (check logs)

**Invalid state transition errors:**
- Review valid transitions in `ChunkMetadata.transition_to()`
- Use the transition methods, don't set state directly

**Chunks stuck in SENT:**
- Check if process crashed before transition to TRANSCRIBED
- Recovery manager will reset to RECORDED on restart

**Meeting never completes:**
- Run `get_summary()` to see chunk states
- Check for failed chunks with `get_failed_chunks()`
- Verify all chunks were registered

## Next Steps

See [CONTRIBUTING.md](../CONTRIBUTING.md) for detailed developer guide.

See [README.md](../README.md) for full system documentation.
