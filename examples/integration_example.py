"""
Example integration: Chunk state manager with mock transcription.

This example shows how to integrate the chunk state system with
a transcription handler (simulated with random failures).

Demonstrates:
- Registering chunks as they're recorded
- Processing chunks with retry on failure
- Handling permanent failures
- Checking meeting completion

Run with: python examples/integration_example.py
"""

import sys
import asyncio
import logging
import random
from pathlib import Path

# Add bot directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))

from chunk_state_manager import ChunkStateManager, ChunkState
from recovery_manager import RecoveryManager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class MockTranscriptionAPI:
    """Simulates a transcription API with random failures."""
    
    def __init__(self, failure_rate: float = 0.4):
        self.failure_rate = failure_rate
        self.call_count = 0
    
    async def transcribe(self, audio_path: str) -> str:
        """Simulate transcription with random failures."""
        self.call_count += 1
        
        # Simulate API latency
        await asyncio.sleep(0.1)
        
        # Random failure
        if random.random() < self.failure_rate:
            raise Exception("API timeout or rate limit")
        
        # Success
        return f"Transcribed text from {audio_path}"


async def process_chunk(
    chunk_id: str,
    manager: ChunkStateManager,
    api: MockTranscriptionAPI
) -> bool:
    """
    Process a single chunk through transcription.
    
    Returns:
        bool: True on success, False on failure
    """
    chunk = manager.get_chunk(chunk_id)
    if not chunk:
        logger.error(f"Chunk {chunk_id} not found")
        return False
    
    # Mark as sent
    manager.transition_chunk(chunk_id, ChunkState.SENT)
    
    try:
        # Call transcription API
        result = await api.transcribe(chunk.file_path)
        
        # Store result and mark as transcribed
        manager.set_transcription_result(chunk_id, result)
        manager.transition_chunk(chunk_id, ChunkState.TRANSCRIBED)
        
        logger.info(f"Successfully transcribed chunk {chunk_id}")
        return True
        
    except Exception as e:
        # Mark failed
        should_retry = manager.mark_chunk_failed(chunk_id, str(e))
        
        if not should_retry:
            logger.error(
                f"Chunk {chunk_id} permanently failed after "
                f"{manager.max_retries} attempts"
            )
        
        return False


async def process_meeting_with_retry(
    meeting_id: str,
    manager: ChunkStateManager,
    api: MockTranscriptionAPI
) -> dict:
    """
    Process all chunks for a meeting with automatic retry.
    
    Returns:
        Dictionary with processing statistics
    """
    logger.info(f"Starting processing for meeting {meeting_id}")
    
    stats = {
        "total_chunks": 0,
        "succeeded": 0,
        "failed": 0,
        "attempts": 0
    }
    
    while True:
        # Get pending chunks
        pending = manager.get_chunks_by_state(
            ChunkState.RECORDED,
            meeting_id=meeting_id
        )
        
        # Filter out permanently failed chunks
        pending = [
            c for c in pending
            if c.retry_count < manager.max_retries
        ]
        
        if not pending:
            break
        
        logger.info(f"Processing {len(pending)} pending chunk(s)")
        
        for chunk in pending:
            stats["attempts"] += 1
            
            # Apply retry delay if this is a retry
            if chunk.retry_count > 0:
                delay = manager.get_retry_delay(chunk.retry_count)
                logger.info(
                    f"Retrying chunk {chunk.chunk_id} after {delay}s "
                    f"(attempt {chunk.retry_count + 1}/{manager.max_retries})"
                )
                await asyncio.sleep(delay)
            
            # Process chunk
            success = await process_chunk(chunk.chunk_id, manager, api)
            
            if success:
                stats["succeeded"] += 1
    
    # Count permanently failed
    failed = manager.get_failed_chunks(meeting_id)
    stats["failed"] = len(failed)
    stats["total_chunks"] = stats["succeeded"] + stats["failed"]
    
    # Check completion
    is_complete, status = manager.is_meeting_complete(meeting_id)
    
    logger.info(f"Processing complete for meeting {meeting_id}")
    logger.info(f"  Status: {status}")
    logger.info(f"  Total attempts: {stats['attempts']}")
    logger.info(f"  API calls: {api.call_count}")
    
    return stats


async def main():
    """Run integration example."""
    print("\n" + "="*60)
    print("INTEGRATION EXAMPLE: Chunk State + Mock Transcription")
    print("="*60)
    
    # Initialize components
    manager = ChunkStateManager(state_dir="example_state")
    api = MockTranscriptionAPI(failure_rate=0.3)  # 30% failure rate
    
    meeting_id = "example-meeting"
    
    print("\n1. Simulating audio recording (creating chunks)...")
    
    # Simulate recording creating chunks
    chunk_ids = []
    for i in range(5):
        chunk_id = f"chunk-{i:03d}"
        manager.register_chunk(
            chunk_id=chunk_id,
            meeting_id=meeting_id,
            file_path=f"recordings/{meeting_id}/{chunk_id}.wav"
        )
        chunk_ids.append(chunk_id)
    
    print(f"Created {len(chunk_ids)} chunks")
    
    print("\n2. Processing chunks with automatic retry...")
    
    # Process all chunks
    stats = await process_meeting_with_retry(meeting_id, manager, api)
    
    print("\n3. Results:")
    print(f"  Total chunks: {stats['total_chunks']}")
    print(f"  Succeeded: {stats['succeeded']}")
    print(f"  Failed: {stats['failed']}")
    print(f"  Total attempts: {stats['attempts']}")
    print(f"  API calls: {api.call_count}")
    
    # Show summary
    summary = manager.get_summary(meeting_id)
    print(f"\n4. Final state summary:")
    print(f"  Transcribed: {summary['transcribed']}")
    print(f"  Failed: {summary['failed']}")
    
    # Check completion
    is_complete, status = manager.is_meeting_complete(meeting_id)
    print(f"\n5. Meeting completion:")
    print(f"  Complete: {is_complete}")
    print(f"  Status: {status}")
    
    # Show failed chunks if any
    failed = manager.get_failed_chunks(meeting_id)
    if failed:
        print(f"\n6. Permanently failed chunks (require manual intervention):")
        for chunk in failed:
            print(f"  - {chunk.chunk_id}: {chunk.last_error}")
            print(f"    Retry count: {chunk.retry_count}")
    
    # Simulate crash recovery
    print("\n7. Simulating bot restart (crash recovery)...")
    new_manager = ChunkStateManager(state_dir="example_state")
    recovery = RecoveryManager(new_manager)
    
    recovery_summary = recovery.recover_on_startup()
    print(f"  Loaded chunks: {recovery_summary['loaded_chunks']}")
    print(f"  Incomplete meetings: {len(recovery_summary['incomplete_meetings'])}")
    print(f"  Pending chunks: {len(recovery_summary['pending_chunks'])}")
    
    # Cleanup
    print("\n8. Cleaning up...")
    import shutil
    shutil.rmtree("example_state")
    print("  Removed example_state directory")
    
    print("\n" + "="*60)
    print("EXAMPLE COMPLETE")
    print("="*60)
    print("\nKey takeaways:")
    print("  - Chunks are registered as RECORDED")
    print("  - Failed chunks automatically retry with backoff")
    print("  - Permanent failures are tracked separately")
    print("  - State survives crashes and is recovered on restart")
    print("  - Meeting completion requires ALL chunks transcribed")


if __name__ == "__main__":
    asyncio.run(main())
