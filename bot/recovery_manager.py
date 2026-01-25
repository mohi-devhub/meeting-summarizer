"""
Crash recovery mechanism for the meeting summarizer bot.

This module handles:
- Bot restart recovery
- Resuming incomplete meetings
- Processing chunks that failed before crash
- Safe state restoration
"""

import logging
from pathlib import Path
from typing import List, Optional, Callable, Awaitable

from chunk_state_manager import ChunkStateManager, ChunkState, ChunkMetadata

logger = logging.getLogger(__name__)


class RecoveryManager:
    """
    Manages recovery from crashes and restarts.
    
    On bot startup:
    1. Load all persisted chunk states
    2. Identify incomplete meetings
    3. Resume processing from last known state
    """
    
    def __init__(self, chunk_manager: ChunkStateManager):
        self.chunk_manager = chunk_manager
        
    def recover_on_startup(self) -> dict:
        """
        Perform recovery operations on bot startup.
        
        Returns:
            Dictionary with recovery summary
        """
        logger.info("Starting crash recovery process...")
        
        # Load all persisted chunk states
        loaded_count = self.chunk_manager.load_state()
        
        if loaded_count == 0:
            logger.info("No persisted state found. Starting fresh.")
            return {
                "loaded_chunks": 0,
                "incomplete_meetings": [],
                "pending_chunks": [],
                "failed_chunks": []
            }
        
        # Analyze loaded state
        summary = self._analyze_state()
        
        logger.info(
            f"Recovery complete: {loaded_count} chunks loaded, "
            f"{len(summary['incomplete_meetings'])} incomplete meeting(s), "
            f"{len(summary['pending_chunks'])} chunk(s) pending processing"
        )
        
        return summary
    
    def _analyze_state(self) -> dict:
        """Analyze loaded state and identify work to be done."""
        all_chunks = list(self.chunk_manager._chunks.values())
        
        # Group chunks by meeting
        meetings = {}
        for chunk in all_chunks:
            if chunk.meeting_id not in meetings:
                meetings[chunk.meeting_id] = []
            meetings[chunk.meeting_id].append(chunk)
        
        incomplete_meetings = []
        pending_chunks = []
        failed_chunks = []
        
        for meeting_id, chunks in meetings.items():
            # Check if meeting is complete
            is_complete, status = self.chunk_manager.is_meeting_complete(meeting_id)
            
            if not is_complete:
                incomplete_meetings.append({
                    "meeting_id": meeting_id,
                    "status": status,
                    "chunk_count": len(chunks)
                })
                
                # Find chunks that need processing
                for chunk in chunks:
                    if chunk.state != ChunkState.TRANSCRIBED:
                        if chunk.retry_count >= self.chunk_manager.max_retries:
                            failed_chunks.append(chunk.chunk_id)
                        else:
                            pending_chunks.append(chunk.chunk_id)
        
        return {
            "loaded_chunks": len(all_chunks),
            "incomplete_meetings": incomplete_meetings,
            "pending_chunks": pending_chunks,
            "failed_chunks": failed_chunks
        }
    
    def get_pending_work(self, meeting_id: Optional[str] = None) -> List[ChunkMetadata]:
        """
        Get all chunks that need processing.
        
        Returns chunks in RECORDED state that haven't exceeded max retries.
        
        Args:
            meeting_id: Optional meeting ID to filter by
            
        Returns:
            List of chunks ready for processing
        """
        chunks = self.chunk_manager.get_chunks_by_state(
            ChunkState.RECORDED,
            meeting_id=meeting_id
        )
        
        # Filter out chunks that exceeded max retries
        pending = [
            chunk for chunk in chunks
            if chunk.retry_count < self.chunk_manager.max_retries
        ]
        
        return pending
    
    def get_incomplete_meetings(self) -> dict:
        """
        Get all meetings that are not fully complete.
        
        Returns:
            Dictionary mapping meeting_id to completion status
        """
        all_chunks = self.chunk_manager._chunks.values()
        meetings = {}
        
        for chunk in all_chunks:
            meeting_id = chunk.meeting_id
            if meeting_id not in meetings:
                is_complete, status = self.chunk_manager.is_meeting_complete(meeting_id)
                if not is_complete:
                    meetings[meeting_id] = {
                        "status": status,
                        "summary": self.chunk_manager.get_summary(meeting_id)
                    }
        
        return meetings
    
    def log_recovery_report(self) -> None:
        """Generate and log a detailed recovery report."""
        summary = self.chunk_manager.get_summary()
        incomplete = self.get_incomplete_meetings()
        
        logger.info("=" * 60)
        logger.info("RECOVERY REPORT")
        logger.info("=" * 60)
        logger.info(f"Total chunks: {summary['total']}")
        logger.info(f"  - Recorded: {summary['recorded']}")
        logger.info(f"  - Sent: {summary['sent']}")
        logger.info(f"  - Transcribed: {summary['transcribed']}")
        logger.info(f"  - Failed: {summary['failed']}")
        logger.info("")
        
        if incomplete:
            logger.info(f"Incomplete meetings: {len(incomplete)}")
            for meeting_id, info in incomplete.items():
                logger.info(f"  Meeting {meeting_id[:8]}:")
                logger.info(f"    Status: {info['status']}")
                logger.info(f"    Chunks: {info['summary']}")
        else:
            logger.info("No incomplete meetings found")
        
        logger.info("=" * 60)
    
    async def resume_processing(
        self,
        transcription_handler: Callable[[ChunkMetadata], Awaitable[bool]],
        meeting_id: Optional[str] = None
    ) -> dict:
        """
        Resume processing of pending chunks.
        
        This is a helper method that can be called by the main bot
        to automatically retry failed chunks.
        
        Args:
            transcription_handler: Async function that handles transcription
                                 Should return True on success, False on failure
            meeting_id: Optional meeting ID to limit processing to
            
        Returns:
            Dictionary with processing results
        """
        pending = self.get_pending_work(meeting_id)
        
        if not pending:
            logger.info("No pending chunks to process")
            return {
                "processed": 0,
                "succeeded": 0,
                "failed": 0
            }
        
        logger.info(f"Resuming processing for {len(pending)} pending chunk(s)")
        
        succeeded = 0
        failed = 0
        
        for chunk in pending:
            try:
                logger.info(
                    f"Processing chunk {chunk.chunk_id} "
                    f"(attempt {chunk.retry_count + 1}/{self.chunk_manager.max_retries})"
                )
                
                # Mark as sent
                self.chunk_manager.transition_chunk(chunk.chunk_id, ChunkState.SENT)
                
                # Call transcription handler
                success = await transcription_handler(chunk)
                
                if success:
                    # Mark as transcribed
                    self.chunk_manager.transition_chunk(
                        chunk.chunk_id,
                        ChunkState.TRANSCRIBED
                    )
                    succeeded += 1
                    logger.info(f"Successfully processed chunk {chunk.chunk_id}")
                else:
                    # Mark failed and check if should retry
                    should_retry = self.chunk_manager.mark_chunk_failed(
                        chunk.chunk_id,
                        "Transcription handler returned failure"
                    )
                    failed += 1
                    
                    if not should_retry:
                        logger.error(
                            f"Chunk {chunk.chunk_id} exceeded max retries. "
                            f"Manual intervention required."
                        )
                
            except Exception as e:
                logger.error(f"Error processing chunk {chunk.chunk_id}: {e}")
                self.chunk_manager.mark_chunk_failed(chunk.chunk_id, str(e))
                failed += 1
        
        logger.info(
            f"Processing complete: {succeeded} succeeded, {failed} failed"
        )
        
        return {
            "processed": len(pending),
            "succeeded": succeeded,
            "failed": failed
        }
    
    def verify_chunk_files_exist(self) -> dict:
        """
        Verify that all chunk files referenced in state actually exist on disk.
        
        Returns:
            Dictionary with verification results
        """
        all_chunks = list(self.chunk_manager._chunks.values())
        missing = []
        
        for chunk in all_chunks:
            file_path = Path(chunk.file_path)
            if not file_path.exists():
                missing.append({
                    "chunk_id": chunk.chunk_id,
                    "meeting_id": chunk.meeting_id,
                    "file_path": chunk.file_path,
                    "state": chunk.state.value
                })
                logger.warning(
                    f"Chunk {chunk.chunk_id} references missing file: {chunk.file_path}"
                )
        
        if missing:
            logger.warning(f"Found {len(missing)} chunk(s) with missing files")
        else:
            logger.info("All chunk files verified present")
        
        return {
            "total_chunks": len(all_chunks),
            "missing_files": missing
        }
