#!/usr/bin/env python3
"""
Example script demonstrating audio compression functionality.

Usage:
    python compress_audio.py <wav_file>
    python compress_audio.py <directory>  # Compresses all WAV files in directory
"""

import sys
import os
from pathlib import Path

# Add parent directory to path to import bot modules
sys.path.insert(0, str(Path(__file__).parent.parent / "bot"))

from audio_compressor import AudioCompressor, estimate_mp3_size, can_compress_safely
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def compress_single_file(wav_path: str):
    """Compress a single WAV file."""
    compressor = AudioCompressor()
    
    logger.info(f"Compressing: {wav_path}")
    
    success, mp3_path, error = compressor.compress_wav_to_mp3(
        wav_path=wav_path,
        cleanup_wav=False  # Keep WAV for safety in example
    )
    
    if success:
        logger.info(f"✅ Success! Compressed file: {mp3_path}")
        return True
    else:
        logger.error(f"❌ Compression failed: {error}")
        return False


def compress_directory(directory: str):
    """Compress all WAV files in a directory."""
    wav_files = list(Path(directory).glob("*.wav"))
    
    if not wav_files:
        logger.warning(f"No WAV files found in {directory}")
        return
    
    logger.info(f"Found {len(wav_files)} WAV files")
    
    compressor = AudioCompressor()
    successful, failed = compressor.batch_compress(
        wav_files=[str(f) for f in wav_files],
        cleanup_wav=False  # Keep WAV for safety in example
    )
    
    logger.info(f"\n{'='*60}")
    logger.info(f"Compression Summary:")
    logger.info(f"  ✅ Successful: {len(successful)}")
    logger.info(f"  ❌ Failed: {len(failed)}")
    logger.info(f"{'='*60}")
    
    if successful:
        logger.info("\nCompressed files:")
        for mp3_file in successful:
            size_mb = Path(mp3_file).stat().st_size / 1024 / 1024
            logger.info(f"  - {Path(mp3_file).name} ({size_mb:.2f} MB)")


def main():
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python compress_audio.py <wav_file>")
        print("  python compress_audio.py <directory>")
        sys.exit(1)
    
    target = sys.argv[1]
    
    if not os.path.exists(target):
        logger.error(f"Path not found: {target}")
        sys.exit(1)
    
    if os.path.isfile(target):
        if not target.lower().endswith('.wav'):
            logger.error("File must be a WAV file")
            sys.exit(1)
        compress_single_file(target)
    elif os.path.isdir(target):
        compress_directory(target)
    else:
        logger.error(f"Invalid path: {target}")
        sys.exit(1)


if __name__ == "__main__":
    main()
