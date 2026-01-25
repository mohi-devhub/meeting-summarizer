"""
Chunk-level state tracking and retry mechanism.

This module provides:
- Per-chunk state tracking (RECORDED, SENT, TRANSCRIBED)
- Independent chunk retry handling
- Crash recovery through JSON persistence
- Meeting completion guarantee (all chunks must be TRANSCRIBED)
"""

import json
import logging
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ChunkState(Enum):
    """States for audio chunk processing."""
    RECORDED = "recorded"          # Chunk fully written to disk
    SENT = "sent"                  # Chunk submitted for transcription
    TRANSCRIBED = "transcribed"    # Transcription completed successfully
    

class ChunkMetadata:
    """Metadata for a single audio chunk."""
    
    def __init__(
        self,
        chunk_id: str,
        meeting_id: str,
        file_path: str,
        state: ChunkState = ChunkState.RECORDED
    ):
        self.chunk_id = chunk_id
        self.meeting_id = meeting_id
        self.file_path = file_path
        self.state = state
        self.created_at = datetime.utcnow().isoformat()
        self.updated_at = datetime.utcnow().isoformat()
        self.retry_count = 0
        self.last_error: Optional[str] = None
        self.transcription_result: Optional[str] = None
        
    def transition_to(self, new_state: ChunkState) -> bool:
        """
        Validate and perform state transition.
        
        Valid transitions:
        - RECORDED -> SENT
        - SENT -> TRANSCRIBED
        - SENT -> RECORDED (retry)
        
        Returns:
            bool: True if transition is valid and completed, False otherwise
        """
        valid_transitions = {
            ChunkState.RECORDED: [ChunkState.SENT],
            ChunkState.SENT: [ChunkState.TRANSCRIBED, ChunkState.RECORDED],
            ChunkState.TRANSCRIBED: []  # Terminal state
        }
        
        if new_state not in valid_transitions[self.state]:
            logger.error(
                f"Invalid state transition for chunk {self.chunk_id}: "
                f"{self.state.value} -> {new_state.value}"
            )
            return False
        
        old_state = self.state
        self.state = new_state
        self.updated_at = datetime.utcnow().isoformat()
        
        logger.info(
            f"Chunk {self.chunk_id} transitioned: {old_state.value} -> {new_state.value}"
        )
        return True
    
    def mark_failed(self, error: str) -> None:
        """Mark chunk as failed and increment retry count."""
        self.retry_count += 1
        self.last_error = error
        self.updated_at = datetime.utcnow().isoformat()
        logger.warning(
            f"Chunk {self.chunk_id} failed (attempt {self.retry_count}): {error}"
        )
    
    def to_dict(self) -> dict:
        """Serialize to dictionary for JSON persistence."""
        return {
            "chunk_id": self.chunk_id,
            "meeting_id": self.meeting_id,
            "file_path": self.file_path,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "retry_count": self.retry_count,
            "last_error": self.last_error,
            "transcription_result": self.transcription_result
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ChunkMetadata":
        """Deserialize from dictionary."""
        chunk = cls(
            chunk_id=data["chunk_id"],
            meeting_id=data["meeting_id"],
            file_path=data["file_path"],
            state=ChunkState(data["state"])
        )
        chunk.created_at = data["created_at"]
        chunk.updated_at = data["updated_at"]
        chunk.retry_count = data["retry_count"]
        chunk.last_error = data.get("last_error")
        chunk.transcription_result = data.get("transcription_result")
        return chunk
    
    def __repr__(self) -> str:
        return (
            f"ChunkMetadata(id={self.chunk_id}, state={self.state.value}, "
            f"retries={self.retry_count})"
        )


class ChunkStateManager:
    """
    Manages chunk state tracking, persistence, and retry logic.
    
    Ensures:
    - No chunk is lost or silently ignored
    - Failed chunks are retried independently
    - State survives process crashes
    - Meeting is only complete when all chunks are TRANSCRIBED
    """
    
    def __init__(self, state_dir: str = "state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        
        # In-memory chunk tracking
        self._chunks: Dict[str, ChunkMetadata] = {}
        
        # Retry configuration
        self.max_retries = 5
        self.retry_base_delay = 2  # seconds
        self.use_exponential_backoff = True
        
        logger.info(f"ChunkStateManager initialized with state_dir: {self.state_dir}")
    
    def register_chunk(
        self,
        chunk_id: str,
        meeting_id: str,
        file_path: str
    ) -> ChunkMetadata:
        """
        Register a new chunk in RECORDED state.
        
        Args:
            chunk_id: Unique identifier for the chunk
            meeting_id: Meeting this chunk belongs to
            file_path: Path to the recorded audio file
            
        Returns:
            ChunkMetadata: The created chunk metadata
        """
        if chunk_id in self._chunks:
            logger.warning(f"Chunk {chunk_id} already registered")
            return self._chunks[chunk_id]
        
        chunk = ChunkMetadata(
            chunk_id=chunk_id,
            meeting_id=meeting_id,
            file_path=file_path,
            state=ChunkState.RECORDED
        )
        
        self._chunks[chunk_id] = chunk
        self._persist_chunk(chunk)
        
        logger.info(f"Registered chunk {chunk_id} for meeting {meeting_id}")
        return chunk
    
    def transition_chunk(self, chunk_id: str, new_state: ChunkState) -> bool:
        """
        Transition a chunk to a new state.
        
        Args:
            chunk_id: ID of the chunk to transition
            new_state: Target state
            
        Returns:
            bool: True if transition successful, False otherwise
        """
        if chunk_id not in self._chunks:
            logger.error(f"Cannot transition unknown chunk: {chunk_id}")
            return False
        
        chunk = self._chunks[chunk_id]
        success = chunk.transition_to(new_state)
        
        if success:
            self._persist_chunk(chunk)
        
        return success
    
    def mark_chunk_failed(self, chunk_id: str, error: str) -> bool:
        """
        Mark a chunk as failed and determine if retry is needed.
        
        Args:
            chunk_id: ID of the failed chunk
            error: Error message describing the failure
            
        Returns:
            bool: True if chunk should be retried, False if max retries exceeded
        """
        if chunk_id not in self._chunks:
            logger.error(f"Cannot mark unknown chunk as failed: {chunk_id}")
            return False
        
        chunk = self._chunks[chunk_id]
        chunk.mark_failed(error)
        
        if chunk.retry_count >= self.max_retries:
            logger.error(
                f"Chunk {chunk_id} exceeded max retries ({self.max_retries}). "
                f"Manual intervention required."
            )
            self._persist_chunk(chunk)
            return False
        
        # Reset to RECORDED state for retry
        chunk.transition_to(ChunkState.RECORDED)
        self._persist_chunk(chunk)
        
        logger.info(f"Chunk {chunk_id} will be retried (attempt {chunk.retry_count + 1})")
        return True
    
    def set_transcription_result(self, chunk_id: str, result: str) -> None:
        """
        Store the transcription result for a chunk.
        
        Args:
            chunk_id: ID of the chunk
            result: Transcription text
        """
        if chunk_id not in self._chunks:
            logger.error(f"Cannot set result for unknown chunk: {chunk_id}")
            return
        
        chunk = self._chunks[chunk_id]
        chunk.transcription_result = result
        self._persist_chunk(chunk)
        
        logger.info(f"Transcription result stored for chunk {chunk_id}")
    
    def get_chunk(self, chunk_id: str) -> Optional[ChunkMetadata]:
        """Get chunk metadata by ID."""
        return self._chunks.get(chunk_id)
    
    def get_chunks_by_meeting(self, meeting_id: str) -> List[ChunkMetadata]:
        """Get all chunks for a specific meeting."""
        return [
            chunk for chunk in self._chunks.values()
            if chunk.meeting_id == meeting_id
        ]
    
    def get_chunks_by_state(
        self,
        state: ChunkState,
        meeting_id: Optional[str] = None
    ) -> List[ChunkMetadata]:
        """
        Get all chunks in a specific state.
        
        Args:
            state: State to filter by
            meeting_id: Optional meeting ID to further filter
            
        Returns:
            List of chunks matching the criteria
        """
        chunks = [chunk for chunk in self._chunks.values() if chunk.state == state]
        
        if meeting_id:
            chunks = [chunk for chunk in chunks if chunk.meeting_id == meeting_id]
        
        return chunks
    
    def get_failed_chunks(
        self,
        meeting_id: Optional[str] = None
    ) -> List[ChunkMetadata]:
        """
        Get all chunks that have failed and exceeded max retries.
        
        These chunks require manual intervention.
        """
        chunks = [
            chunk for chunk in self._chunks.values()
            if chunk.retry_count >= self.max_retries and chunk.state != ChunkState.TRANSCRIBED
        ]
        
        if meeting_id:
            chunks = [chunk for chunk in chunks if chunk.meeting_id == meeting_id]
        
        return chunks
    
    def is_meeting_complete(self, meeting_id: str) -> Tuple[bool, str]:
        """
        Check if all chunks for a meeting are TRANSCRIBED.
        
        Args:
            meeting_id: Meeting ID to check
            
        Returns:
            Tuple of (is_complete, status_message)
        """
        chunks = self.get_chunks_by_meeting(meeting_id)
        
        if not chunks:
            return False, "No chunks found for this meeting"
        
        total = len(chunks)
        transcribed = len([c for c in chunks if c.state == ChunkState.TRANSCRIBED])
        failed = len(self.get_failed_chunks(meeting_id))
        
        if transcribed == total:
            return True, f"All {total} chunks transcribed successfully"
        
        if failed > 0:
            return False, f"{failed} chunk(s) failed permanently (max retries exceeded)"
        
        return False, f"{transcribed}/{total} chunks transcribed"
    
    def get_retry_delay(self, retry_count: int) -> float:
        """
        Calculate retry delay with optional exponential backoff.
        
        Args:
            retry_count: Number of retries already attempted
            
        Returns:
            Delay in seconds
        """
        if self.use_exponential_backoff:
            return self.retry_base_delay * (2 ** retry_count)
        return self.retry_base_delay
    
    def _persist_chunk(self, chunk: ChunkMetadata) -> None:
        """Persist chunk state to disk."""
        try:
            state_file = self._get_chunk_state_file(chunk.chunk_id)
            state_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(state_file, 'w') as f:
                json.dump(chunk.to_dict(), f, indent=2)
                
        except Exception as e:
            logger.error(f"Failed to persist chunk {chunk.chunk_id}: {e}")
    
    def _get_chunk_state_file(self, chunk_id: str) -> Path:
        """Get the path to a chunk's state file."""
        return self.state_dir / f"{chunk_id}.json"
    
    def load_state(self) -> int:
        """
        Load all persisted chunk state from disk.
        
        Called on startup to recover from crashes.
        
        Returns:
            Number of chunks loaded
        """
        loaded_count = 0
        
        try:
            for state_file in self.state_dir.glob("*.json"):
                try:
                    with open(state_file, 'r') as f:
                        data = json.load(f)
                        chunk = ChunkMetadata.from_dict(data)
                        self._chunks[chunk.chunk_id] = chunk
                        loaded_count += 1
                        
                except Exception as e:
                    logger.error(f"Failed to load state file {state_file}: {e}")
            
            if loaded_count > 0:
                logger.info(f"Loaded {loaded_count} chunk(s) from disk")
            
        except Exception as e:
            logger.error(f"Failed to load chunk state: {e}")
        
        return loaded_count
    
    def get_summary(self, meeting_id: Optional[str] = None) -> dict:
        """
        Get a summary of chunk states.
        
        Args:
            meeting_id: Optional meeting ID to filter by
            
        Returns:
            Dictionary with state counts and details
        """
        chunks = self._chunks.values()
        if meeting_id:
            chunks = [c for c in chunks if c.meeting_id == meeting_id]
        
        chunks_list = list(chunks)
        
        return {
            "total": len(chunks_list),
            "recorded": len([c for c in chunks_list if c.state == ChunkState.RECORDED]),
            "sent": len([c for c in chunks_list if c.state == ChunkState.SENT]),
            "transcribed": len([c for c in chunks_list if c.state == ChunkState.TRANSCRIBED]),
            "failed": len([c for c in chunks_list if c.retry_count >= self.max_retries and c.state != ChunkState.TRANSCRIBED])
        }
    
    def clear_meeting(self, meeting_id: str) -> int:
        """
        Clear all chunk state for a completed meeting.
        
        Only clears chunks that are TRANSCRIBED or permanently failed.
        
        Args:
            meeting_id: Meeting ID to clear
            
        Returns:
            Number of chunks cleared
        """
        cleared_count = 0
        chunks_to_remove = []
        
        for chunk_id, chunk in self._chunks.items():
            if chunk.meeting_id != meeting_id:
                continue
                
            # Only clear terminal states
            if chunk.state == ChunkState.TRANSCRIBED or chunk.retry_count >= self.max_retries:
                chunks_to_remove.append(chunk_id)
        
        for chunk_id in chunks_to_remove:
            try:
                # Remove from memory
                del self._chunks[chunk_id]
                
                # Remove from disk
                state_file = self._get_chunk_state_file(chunk_id)
                if state_file.exists():
                    state_file.unlink()
                
                cleared_count += 1
                
            except Exception as e:
                logger.error(f"Failed to clear chunk {chunk_id}: {e}")
        
        if cleared_count > 0:
            logger.info(f"Cleared {cleared_count} chunk(s) for meeting {meeting_id}")
        
        return cleared_count
