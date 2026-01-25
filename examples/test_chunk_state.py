"""
Test script demonstrating chunk state tracking and retry behavior.

This standalone script shows:
- Chunk registration
- State transitions
- Retry handling
- Persistence and recovery
- Meeting completion validation

Run with: python examples/test_chunk_state.py
"""

import sys
import logging
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


def test_chunk_registration():
    """Test basic chunk registration and state tracking."""
    print("\n" + "="*60)
    print("TEST 1: Chunk Registration")
    print("="*60)
    
    manager = ChunkStateManager(state_dir="test_state")
    
    # Register some chunks
    chunk1 = manager.register_chunk(
        chunk_id="chunk-001",
        meeting_id="meeting-alpha",
        file_path="recordings/meeting-alpha/chunk-001.wav"
    )
    
    chunk2 = manager.register_chunk(
        chunk_id="chunk-002",
        meeting_id="meeting-alpha",
        file_path="recordings/meeting-alpha/chunk-002.wav"
    )
    
    chunk3 = manager.register_chunk(
        chunk_id="chunk-003",
        meeting_id="meeting-beta",
        file_path="recordings/meeting-beta/chunk-003.wav"
    )
    
    print(f"\nRegistered 3 chunks:")
    print(f"  - {chunk1}")
    print(f"  - {chunk2}")
    print(f"  - {chunk3}")
    
    # Get chunks by meeting
    alpha_chunks = manager.get_chunks_by_meeting("meeting-alpha")
    print(f"\nChunks in meeting-alpha: {len(alpha_chunks)}")
    
    # Get summary
    summary = manager.get_summary("meeting-alpha")
    print(f"Summary: {summary}")
    
    return manager


def test_state_transitions(manager: ChunkStateManager):
    """Test valid and invalid state transitions."""
    print("\n" + "="*60)
    print("TEST 2: State Transitions")
    print("="*60)
    
    chunk_id = "chunk-001"
    
    # Valid transition: RECORDED -> SENT
    print(f"\nAttempting RECORDED -> SENT...")
    success = manager.transition_chunk(chunk_id, ChunkState.SENT)
    print(f"Result: {'Success' if success else 'Failed'}")
    
    # Valid transition: SENT -> TRANSCRIBED
    print(f"\nAttempting SENT -> TRANSCRIBED...")
    success = manager.transition_chunk(chunk_id, ChunkState.TRANSCRIBED)
    print(f"Result: {'Success' if success else 'Failed'}")
    
    # Invalid transition: TRANSCRIBED -> RECORDED (terminal state)
    print(f"\nAttempting TRANSCRIBED -> RECORDED (should fail)...")
    success = manager.transition_chunk(chunk_id, ChunkState.RECORDED)
    print(f"Result: {'Success' if success else 'Failed (expected)'}")
    
    chunk = manager.get_chunk(chunk_id)
    print(f"\nFinal state: {chunk.state.value}")


def test_retry_handling(manager: ChunkStateManager):
    """Test retry mechanism with failures."""
    print("\n" + "="*60)
    print("TEST 3: Retry Handling")
    print("="*60)
    
    chunk_id = "chunk-002"
    
    # Transition to SENT
    manager.transition_chunk(chunk_id, ChunkState.SENT)
    
    # Simulate multiple failures
    for attempt in range(1, 7):
        print(f"\nSimulating failure attempt {attempt}...")
        should_retry = manager.mark_chunk_failed(
            chunk_id,
            f"Simulated API error on attempt {attempt}"
        )
        
        chunk = manager.get_chunk(chunk_id)
        print(f"  Retry count: {chunk.retry_count}")
        print(f"  Current state: {chunk.state.value}")
        print(f"  Should retry: {should_retry}")
        
        if should_retry:
            # Retry: transition back to SENT
            manager.transition_chunk(chunk_id, ChunkState.SENT)
        else:
            print(f"  MAX RETRIES EXCEEDED - Manual intervention required")
            break
    
    # Show final state
    chunk = manager.get_chunk(chunk_id)
    print(f"\nFinal retry count: {chunk.retry_count}")
    print(f"Last error: {chunk.last_error}")


def test_meeting_completion(manager: ChunkStateManager):
    """Test meeting completion validation."""
    print("\n" + "="*60)
    print("TEST 4: Meeting Completion")
    print("="*60)
    
    meeting_id = "meeting-alpha"
    
    # Check completion (should be incomplete)
    is_complete, status = manager.is_meeting_complete(meeting_id)
    print(f"\nMeeting {meeting_id}:")
    print(f"  Complete: {is_complete}")
    print(f"  Status: {status}")
    
    # Complete the incomplete chunk
    chunk_id = "chunk-002"
    chunk = manager.get_chunk(chunk_id)
    
    if chunk.state != ChunkState.TRANSCRIBED:
        print(f"\nCompleting chunk {chunk_id}...")
        # Reset retry count for demo
        chunk.retry_count = 0
        chunk.state = ChunkState.RECORDED
        manager.transition_chunk(chunk_id, ChunkState.SENT)
        manager.transition_chunk(chunk_id, ChunkState.TRANSCRIBED)
    
    # Check again
    is_complete, status = manager.is_meeting_complete(meeting_id)
    print(f"\nMeeting {meeting_id} after completion:")
    print(f"  Complete: {is_complete}")
    print(f"  Status: {status}")
    
    # Show summary
    summary = manager.get_summary(meeting_id)
    print(f"  Summary: {summary}")


def test_persistence_and_recovery():
    """Test persistence and crash recovery."""
    print("\n" + "="*60)
    print("TEST 5: Persistence and Recovery")
    print("="*60)
    
    # Create new manager to simulate restart
    print("\nSimulating bot restart...")
    new_manager = ChunkStateManager(state_dir="test_state")
    
    # Load persisted state
    loaded_count = new_manager.load_state()
    print(f"Loaded {loaded_count} chunks from disk")
    
    # Show what was recovered
    summary = new_manager.get_summary()
    print(f"\nRecovered state summary:")
    print(f"  Total: {summary['total']}")
    print(f"  Recorded: {summary['recorded']}")
    print(f"  Sent: {summary['sent']}")
    print(f"  Transcribed: {summary['transcribed']}")
    print(f"  Failed: {summary['failed']}")
    
    # Use recovery manager
    print("\nUsing RecoveryManager...")
    recovery = RecoveryManager(new_manager)
    recovery_summary = recovery.recover_on_startup()
    
    print(f"\nRecovery summary:")
    print(f"  Loaded chunks: {recovery_summary['loaded_chunks']}")
    print(f"  Incomplete meetings: {len(recovery_summary['incomplete_meetings'])}")
    print(f"  Pending chunks: {len(recovery_summary['pending_chunks'])}")
    print(f"  Failed chunks: {len(recovery_summary['failed_chunks'])}")
    
    if recovery_summary['incomplete_meetings']:
        print("\nIncomplete meetings:")
        for meeting in recovery_summary['incomplete_meetings']:
            print(f"  - Meeting {meeting['meeting_id']}: {meeting['status']}")
    
    # Show pending work
    pending = recovery.get_pending_work()
    print(f"\nPending chunks ready for processing: {len(pending)}")
    for chunk in pending:
        print(f"  - {chunk.chunk_id} (retry {chunk.retry_count})")


def test_cleanup():
    """Clean up test state."""
    print("\n" + "="*60)
    print("CLEANUP")
    print("="*60)
    
    import shutil
    state_dir = Path("test_state")
    
    if state_dir.exists():
        shutil.rmtree(state_dir)
        print("\nRemoved test_state directory")
    
    print("\nTest complete!")


def main():
    """Run all tests."""
    print("\n")
    print("="*60)
    print("CHUNK STATE TRACKING AND RETRY TEST SUITE")
    print("="*60)
    print("\nThis script demonstrates:")
    print("  1. Chunk registration")
    print("  2. State transitions")
    print("  3. Retry handling with exponential backoff")
    print("  4. Meeting completion validation")
    print("  5. Persistence and crash recovery")
    
    try:
        # Run tests in sequence
        manager = test_chunk_registration()
        test_state_transitions(manager)
        test_retry_handling(manager)
        test_meeting_completion(manager)
        test_persistence_and_recovery()
        
        # Cleanup
        test_cleanup()
        
    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
