from datetime import datetime
from enum import Enum
from typing import Optional, Dict
import uuid


class MeetingStatus(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    ENDED = "ended"


class MeetingSession:
    def __init__(
        self,
        guild_id: int,
        voice_channel_id: int,
        text_channel_id: int,
        initiator_id: int
    ):
        self.meeting_id = str(uuid.uuid4())
        self.guild_id = guild_id
        self.voice_channel_id = voice_channel_id
        self.text_channel_id = text_channel_id
        self.initiator_id = initiator_id
        self.start_timestamp = datetime.utcnow()
        self.end_timestamp: Optional[datetime] = None
        self.status = MeetingStatus.RECORDING
        self.recording_path: Optional[str] = None
        
    def end(self) -> None:
        self.status = MeetingStatus.ENDED
        self.end_timestamp = datetime.utcnow()
        
    def duration_seconds(self) -> Optional[float]:
        if self.end_timestamp:
            return (self.end_timestamp - self.start_timestamp).total_seconds()
        return (datetime.utcnow() - self.start_timestamp).total_seconds()
    
    def __repr__(self) -> str:
        return (
            f"MeetingSession(id={self.meeting_id[:8]}, "
            f"guild={self.guild_id}, status={self.status.value})"
        )


class MeetingStateManager:
    def __init__(self):
        self._active_meetings: Dict[int, MeetingSession] = {}
        
    def start_meeting(
        self,
        guild_id: int,
        voice_channel_id: int,
        text_channel_id: int,
        initiator_id: int
    ) -> tuple[bool, Optional[MeetingSession], Optional[str]]:
        if guild_id in self._active_meetings:
            existing = self._active_meetings[guild_id]
            return (
                False,
                None,
                f"A meeting is already in progress (ID: {existing.meeting_id[:8]})"
            )
        
        session = MeetingSession(
            guild_id=guild_id,
            voice_channel_id=voice_channel_id,
            text_channel_id=text_channel_id,
            initiator_id=initiator_id
        )
        
        self._active_meetings[guild_id] = session
        return (True, session, None)
    
    def end_meeting(self, guild_id: int) -> tuple[bool, Optional[MeetingSession], Optional[str]]:
        if guild_id not in self._active_meetings:
            return (False, None, "No active meeting to end")
        
        session = self._active_meetings[guild_id]
        session.end()
        del self._active_meetings[guild_id]
        
        return (True, session, None)
    
    def get_active_meeting(self, guild_id: int) -> Optional[MeetingSession]:
        return self._active_meetings.get(guild_id)
    
    def is_meeting_active(self, guild_id: int) -> bool:
        return guild_id in self._active_meetings
    
    def get_all_active_meetings(self) -> Dict[int, MeetingSession]:
        return self._active_meetings.copy()
