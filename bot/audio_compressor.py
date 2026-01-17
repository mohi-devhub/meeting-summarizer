"""
Audio Compression Module for Discord Meeting Bot

Handles compression of recorded WAV audio chunks to MP3 format using ffmpeg.
Optimized for speech transcription with Groq API constraints (25 MB limit).

Design Decisions:
- Codec: libmp3lame (widely supported, good compression)
- Bitrate: 32 kbps (speech-optimized, aggressive compression)
- Sample Rate: 16 kHz (sufficient for speech, Groq/STT standard)
- Channels: Mono (reduces size by ~50%, speech doesn't need stereo)

File Size Calculation:
- 10 min @ 32 kbps = ~2.3 MB
- 30 min @ 32 kbps = ~7.2 MB
- 90 min @ 32 kbps = ~21.6 MB (under 25 MB limit with margin)

This allows long meeting chunks while staying safely under API limits.
"""

import subprocess
import os
import logging
from pathlib import Path
from typing import Optional, Tuple
import shutil

logger = logging.getLogger(__name__)

# Constants
MAX_FILE_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB (Groq API limit)
SAFE_FILE_SIZE_BYTES = 24 * 1024 * 1024  # 24 MB (safety margin)


class AudioCompressionError(Exception):
    """Raised when audio compression fails"""
    pass


class AudioCompressor:
    """
    Handles WAV to MP3 compression using ffmpeg.
    
    Optimized for speech transcription with strict file size constraints.
    """
    
    def __init__(
        self,
        bitrate: str = "32k",
        sample_rate: int = 16000,
        channels: int = 1
    ):
        """
        Initialize audio compressor with encoding settings.
        
        Args:
            bitrate: Target bitrate (default: 32k for speech)
            sample_rate: Target sample rate in Hz (default: 16000)
            channels: Number of audio channels (default: 1 for mono)
        """
        self.bitrate = bitrate
        self.sample_rate = sample_rate
        self.channels = channels
        
        # Verify ffmpeg is available
        if not self._check_ffmpeg_available():
            raise RuntimeError("ffmpeg not found. Please install ffmpeg.")
        
        logger.info(
            f"AudioCompressor initialized: {bitrate} bitrate, "
            f"{sample_rate}Hz, {channels} channel(s)"
        )
    
    def _check_ffmpeg_available(self) -> bool:
        """Check if ffmpeg is installed and available."""
        return shutil.which("ffmpeg") is not None
    
    def compress_wav_to_mp3(
        self,
        wav_path: str,
        mp3_path: Optional[str] = None,
        cleanup_wav: bool = False
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Compress a WAV file to MP3 format.
        
        Args:
            wav_path: Path to input WAV file
            mp3_path: Path to output MP3 file (default: same as WAV with .mp3 extension)
            cleanup_wav: If True, delete WAV file after successful compression
        
        Returns:
            Tuple of (success, mp3_path, error_message)
            - success: True if compression succeeded
            - mp3_path: Path to compressed MP3 file (None on failure)
            - error_message: Error description (None on success)
        """
        wav_file = Path(wav_path)
        
        # Validate input file exists
        if not wav_file.exists():
            error = f"WAV file not found: {wav_path}"
            logger.error(error)
            return (False, None, error)
        
        if not wav_file.is_file():
            error = f"Path is not a file: {wav_path}"
            logger.error(error)
            return (False, None, error)
        
        # Determine output path
        if mp3_path is None:
            mp3_path = str(wav_file.with_suffix('.mp3'))
        
        mp3_file = Path(mp3_path)
        
        logger.info(f"Compressing {wav_file.name} → {mp3_file.name}")
        
        try:
            # Build ffmpeg command
            # -i: input file
            # -codec:a libmp3lame: use MP3 encoder
            # -b:a: audio bitrate
            # -ar: audio sample rate
            # -ac: audio channels
            # -y: overwrite output file if exists
            # -loglevel error: only show errors
            cmd = [
                "ffmpeg",
                "-i", str(wav_file),
                "-codec:a", "libmp3lame",
                "-b:a", self.bitrate,
                "-ar", str(self.sample_rate),
                "-ac", str(self.channels),
                "-y",  # Overwrite if exists
                "-loglevel", "error",
                str(mp3_file)
            ]
            
            # Execute ffmpeg
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300  # 5 minute timeout for very long files
            )
            
            if result.returncode != 0:
                error = f"ffmpeg failed: {result.stderr}"
                logger.error(error)
                return (False, None, error)
            
            # Validate compressed file
            validation_ok, validation_error = self._validate_compressed_file(mp3_file)
            if not validation_ok:
                logger.error(f"Validation failed: {validation_error}")
                # Clean up invalid MP3
                if mp3_file.exists():
                    mp3_file.unlink()
                return (False, None, validation_error)
            
            # Log compression results
            original_size = wav_file.stat().st_size
            compressed_size = mp3_file.stat().st_size
            compression_ratio = (1 - compressed_size / original_size) * 100
            
            logger.info(
                f"Compression successful: {wav_file.name} "
                f"({original_size / 1024 / 1024:.2f} MB) → "
                f"{mp3_file.name} ({compressed_size / 1024 / 1024:.2f} MB), "
                f"ratio: {compression_ratio:.1f}%"
            )
            
            # Cleanup original WAV if requested
            if cleanup_wav:
                try:
                    wav_file.unlink()
                    logger.info(f"Deleted original WAV: {wav_file.name}")
                except Exception as e:
                    logger.warning(f"Failed to delete WAV file {wav_file.name}: {e}")
            
            return (True, str(mp3_file), None)
            
        except subprocess.TimeoutExpired:
            error = f"ffmpeg timeout after 300s for file: {wav_path}"
            logger.error(error)
            return (False, None, error)
            
        except Exception as e:
            error = f"Unexpected error during compression: {str(e)}"
            logger.error(error, exc_info=True)
            return (False, None, error)
    
    def _validate_compressed_file(self, mp3_file: Path) -> Tuple[bool, Optional[str]]:
        """
        Validate compressed MP3 file.
        
        Checks:
        1. File exists
        2. File is not empty
        3. File size is under limit
        4. File is a valid audio file (basic ffprobe check)
        
        Args:
            mp3_file: Path to MP3 file
        
        Returns:
            Tuple of (is_valid, error_message)
        """
        # Check file exists
        if not mp3_file.exists():
            return (False, "Compressed file does not exist")
        
        # Check file is not empty
        file_size = mp3_file.stat().st_size
        if file_size == 0:
            return (False, "Compressed file is empty")
        
        # Check file size is under limit
        if file_size > MAX_FILE_SIZE_BYTES:
            return (
                False,
                f"File too large: {file_size / 1024 / 1024:.2f} MB "
                f"(limit: {MAX_FILE_SIZE_BYTES / 1024 / 1024} MB)"
            )
        
        # Warn if approaching limit
        if file_size > SAFE_FILE_SIZE_BYTES:
            logger.warning(
                f"File size approaching limit: {file_size / 1024 / 1024:.2f} MB "
                f"(safe threshold: {SAFE_FILE_SIZE_BYTES / 1024 / 1024} MB)"
            )
        
        # Validate it's a valid audio file using ffprobe
        if not self._check_audio_integrity(mp3_file):
            return (False, "File failed audio integrity check")
        
        return (True, None)
    
    def _check_audio_integrity(self, audio_file: Path) -> bool:
        """
        Check if file is a valid, playable audio file using ffprobe.
        
        Args:
            audio_file: Path to audio file
        
        Returns:
            True if file is valid audio, False otherwise
        """
        try:
            cmd = [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(audio_file)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                logger.error(f"ffprobe failed for {audio_file.name}: {result.stderr}")
                return False
            
            # Check if we got a valid duration
            try:
                duration = float(result.stdout.strip())
                if duration <= 0:
                    logger.error(f"Invalid duration for {audio_file.name}: {duration}")
                    return False
                
                logger.debug(f"Audio duration: {duration:.2f}s for {audio_file.name}")
                return True
                
            except (ValueError, AttributeError):
                logger.error(f"Could not parse duration from ffprobe output for {audio_file.name}")
                return False
                
        except subprocess.TimeoutExpired:
            logger.error(f"ffprobe timeout for {audio_file.name}")
            return False
            
        except Exception as e:
            logger.error(f"Error checking audio integrity: {e}")
            return False
    
    def batch_compress(
        self,
        wav_files: list[str],
        output_dir: Optional[str] = None,
        cleanup_wav: bool = False
    ) -> Tuple[list[str], list[str]]:
        """
        Compress multiple WAV files to MP3.
        
        Args:
            wav_files: List of WAV file paths
            output_dir: Directory for compressed files (default: same as input)
            cleanup_wav: If True, delete WAV files after successful compression
        
        Returns:
            Tuple of (successful_mp3_paths, failed_wav_paths)
        """
        successful = []
        failed = []
        
        logger.info(f"Starting batch compression of {len(wav_files)} files")
        
        for wav_path in wav_files:
            # Determine output path
            if output_dir:
                wav_file = Path(wav_path)
                mp3_path = Path(output_dir) / wav_file.with_suffix('.mp3').name
            else:
                mp3_path = None
            
            # Compress
            success, mp3_file, error = self.compress_wav_to_mp3(
                wav_path=wav_path,
                mp3_path=str(mp3_path) if mp3_path else None,
                cleanup_wav=cleanup_wav
            )
            
            if success:
                successful.append(mp3_file)
            else:
                failed.append(wav_path)
                logger.error(f"Failed to compress {wav_path}: {error}")
        
        logger.info(
            f"Batch compression complete: {len(successful)} succeeded, "
            f"{len(failed)} failed"
        )
        
        return (successful, failed)


def estimate_mp3_size(duration_seconds: float, bitrate_kbps: int = 32) -> float:
    """
    Estimate MP3 file size for a given duration and bitrate.
    
    Args:
        duration_seconds: Audio duration in seconds
        bitrate_kbps: Bitrate in kilobits per second
    
    Returns:
        Estimated file size in bytes
    """
    # Formula: (bitrate * duration) / 8 = size in bytes
    # Add 5% overhead for MP3 headers/metadata
    size_bytes = (bitrate_kbps * 1000 * duration_seconds) / 8
    size_bytes *= 1.05  # Add 5% overhead
    return size_bytes


def can_compress_safely(duration_seconds: float, bitrate_kbps: int = 32) -> bool:
    """
    Check if audio of given duration can be compressed safely under file size limit.
    
    Args:
        duration_seconds: Audio duration in seconds
        bitrate_kbps: Target bitrate in kilobits per second
    
    Returns:
        True if estimated size is under safe limit, False otherwise
    """
    estimated_size = estimate_mp3_size(duration_seconds, bitrate_kbps)
    return estimated_size < SAFE_FILE_SIZE_BYTES
