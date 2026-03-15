#!/usr/bin/env python3
"""
Audio Upload Module for Music AI

Handles MP3 file uploads and basic audio processing.
"""

import os
import json
from pathlib import Path
from typing import Optional, Dict, Any
import aiofiles
from fastapi import UploadFile, HTTPException


class AudioUploadHandler:
    """Handles audio file uploads and processing."""
    
    def __init__(self, upload_dir: str = "uploads"):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(exist_ok=True)
        self.allowed_extensions = {".mp3", ".wav", ".m4a", ".flac"}
        
    async def save_upload(self, file: UploadFile, filename: Optional[str] = None) -> Dict[str, Any]:
        """
        Save uploaded audio file and return metadata.
        
        Args:
            file: The uploaded file
            filename: Optional custom filename
            
        Returns:
            Dict containing file metadata
        """
        # Validate file extension
        file_ext = Path(file.filename).suffix.lower()
        if file_ext not in self.allowed_extensions:
            raise HTTPException(
                status_code=400, 
                detail=f"File type {file_ext} not allowed. Allowed types: {self.allowed_extensions}"
            )
        
        # Generate filename
        if filename:
            safe_filename = filename
        else:
            safe_filename = f"audio_{hash(file.filename)}_{file_ext}"
        
        file_path = self.upload_dir / safe_filename
        
        # Save file
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        # Return metadata
        metadata = {
            "filename": safe_filename,
            "original_filename": file.filename,
            "file_path": str(file_path),
            "file_size": len(content),
            "file_type": file_ext,
            "content_type": file.content_type
        }
        
        # Save metadata
        metadata_path = file_path.with_suffix('.json')
        async with aiofiles.open(metadata_path, 'w') as f:
            await f.write(json.dumps(metadata, indent=2))
        
        return metadata
    
    def get_upload_info(self, filename: str) -> Optional[Dict[str, Any]]:
        """Get upload metadata for a file."""
        file_path = self.upload_dir / filename
        metadata_path = file_path.with_suffix('.json')
        
        if metadata_path.exists():
            with open(metadata_path, 'r') as f:
                return json.load(f)
        return None
    
    def list_uploads(self) -> list:
        """List all uploaded audio files."""
        uploads = []
        for file_path in self.upload_dir.glob("*.mp3"):
            metadata = self.get_upload_info(file_path.name)
            if metadata:
                uploads.append(metadata)
        return uploads
    
    def delete_upload(self, filename: str) -> bool:
        """Delete an uploaded file and its metadata."""
        file_path = self.upload_dir / filename
        metadata_path = file_path.with_suffix('.json')
        
        deleted = False
        if file_path.exists():
            file_path.unlink()
            deleted = True
        
        if metadata_path.exists():
            metadata_path.unlink()
            deleted = True
        
        return deleted


# Global upload handler instance
upload_handler = AudioUploadHandler()
