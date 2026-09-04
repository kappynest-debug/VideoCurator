#!/usr/bin/env python3
from flask import Flask, render_template, request, jsonify
import os
import json
import subprocess
import tempfile
import base64
from pathlib import Path
from datetime import datetime
import threading
import anthropic
import pickle

try:
    import googleapiclient.discovery
    from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
    DRIVE_AVAILABLE = True
except:
    DRIVE_AVAILABLE = False

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

Path('/tmp/work').mkdir(exist_ok=True)
WORK_FOLDER = '/tmp/work'

SCOPES = ['https://www.googleapis.com/auth/drive']
DRIVE_FOLDER_ID = os.getenv('GOOGLE_DRIVE_FOLDER_ID', '')
TOKEN_FILE = os.path.join(WORK_FOLDER, 'token.pickle')
CREDENTIALS_FILE = os.path.join(WORK_FOLDER, 'credentials.json')

VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv'}

drive_service = None

def init_google_drive():
    global drive_service
    if not DRIVE_AVAILABLE or not DRIVE_FOLDER_ID:
        return None
    try:
        creds = None
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE, 'rb') as f:
                creds = pickle.load(f)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
            else:
                from google_auth_oauthlib.flow import InstalledAppFlow
                flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
            with open(TOKEN_FILE, 'wb') as f:
                pickle.dump(creds, f)
        drive_service = googleapiclient.discovery.build('drive', 'v3', credentials=creds)
        return drive_service
    except Exception as e:
        print(f"Warning: Could not connect to Google Drive: {e}")
        return None

def upload_to_drive(local_path, folder_id=None):
    if not drive_service or not folder_id:
        return None
    try:
        file_metadata = {'name': os.path.basename(local_path), 'parents': [folder_id]}
        media = MediaFileUpload(local_path, resumable=True)
        file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return file.get('id')
    except Exception as e:
        print(f"Error uploading to Drive: {e}")
        return None

def download_from_drive(file_id, local_path):
    if not drive_service:
        return False
    try:
        request = drive_service.files().get_media(fileId=file_id)
        with open(local_path, 'wb') as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                status, done = downloader.next_chunk()
        return True
    except Exception as e:
        print(f"Error downloading from Drive: {e}")
        return False

def list_drive_files(folder_id, extension=None):
    if not drive_service:
        return []
    try:
        query = f"'{folder_id}' in parents and trashed=false"
        results = drive_service.files().list(q=query, spaces='drive', fields='files(id, name, createdTime, size)', pageSize=100).execute()
        files = results.get('files', [])
        if extension:
            files = [f for f in files if f['name'].endswith(extension)]
        return files
    except Exception as e:
        print(f"Error listing Drive files: {e}")
        return []

def get_video_duration(video_path: str) -> float:
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=noprint_wrappers=1:nokey=1:novalue=1', video_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return float(result.stdout.strip())
    except:
        return 0.0

def extract_keyframes(video_path: str, num_frames: int = 5) -> list:
    try:
        duration = get_video_duration(video_path)
        if duration == 0:
            return []
        frames = []
        temp_dir = tempfile.gettempdir()
        intervals = [0] + [int(duration * (i / num_frames)) for i in range(1, num_frames)]
        for i, timestamp in enumerate(intervals):
            frame_path = os.path.join(temp_dir, f'frame_{timestamp}_{i}.jpg')
            cmd = ['ffmpeg', '-y', '-ss', str(timestamp), '-i', video_path, '-vf', 'scale=1280:-1', '-vframes', '1', '-q:v', '3', frame_path]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and os.path.exists(frame_path):
                with open(frame_path, 'rb') as f:
                    frame_data = base64.standard_b64encode(f.read()).decode('utf-8')
                    frames.append({'timestamp': timestamp, 'data': frame_data, 'media_type': 'image/jpeg'})
                try:
                    os.remove(frame_path)
                except:
                    pass
        return frames
    except:
        return []

def extract_audio(video_path: str) -> str:
    try:
        temp_dir = tempfile.gettempdir()
        audio_path = os.path.join(temp_dir, f'audio_{int(datetime.now().timestamp())}.mp3')
        cmd = ['ffmpeg', '-y', '-i', video_path, '-q:a', '9', '-map', 'a', audio_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0 and os.path.exists(audio_path):
            file_size = os.path.getsize(audio_path)
            if file_size > 10000 and file_size < 25000000:
                return audio_path
        return None
    except:
        return None

def transcribe_audio(audio_path: str) -> str:
    try:
        client = anthropic.Anthropic()
        with open(audio_path, 'rb') as f:
            audio_data = f.read()
        audio_base64 = base64.standard_b64encode(audio_data).decode('utf-8')
        response = client.messages.create(model="claude-opus-4-6", max_tokens=500, messages=[{"role": "user", "content": [{"type": "text", "text": "Transcribe this fitness video audio. Focus on: workout type, exercises, instructions, intensity, target areas. Be concise (2-3 sentences max)."}, {"type": "audio", "source": {"type": "base64", "media_type": "audio/mp3", "data": audio_base64}}]}])
        return response.content[0].text if response.content else ""
    except:
        return ""

def analyze_frames_vision(frames: list, video_filename: str) -> str:
    if not frames:
        return "Unable to analyze video frames."
    try:
        client = anthropic.Anthropic()
        content = [{"type": "text", "text": f"""Analyze these keyframes from a fitness video: {video_filename}

Describe what you observe in 2-3 sentences focusing on:
- Type of workout/exercise
- Target muscle groups
- Equipment visible
- Estimated intensity level
- Number of people

Be specific and searchable."""}]
        for frame in frames:
            content.append({"type": "image", "source": {"type": "base64", "media_type": frame['media_type'], "data": frame['data']}})
        response = client.messages.create(model="claude-opus-4-6", max_tokens=300, messages=[{"role": "user", "content": content}])
        return response.content[0].text
    except:
        return "Unable to analyze frames."

def combine_analysis(vision_desc: str, audio_desc: str) -> str:
    try:
        client = anthropic.Anthropic()
        prompt = f"""Combine these analyses into ONE coherent description (2-3 sentences):

VISUAL: {vision_desc}
AUDIO: {audio_desc if audio_desc else "(No audio)"}

Create a single searchable description."""
        response = client.messages.create(model="claude-opus-4-6", max_tokens=200, messages=[{"role": "user", "content": prompt}])
        return response.content[0].text
    except:
        return vision_desc

def semantic_search_videos(query: str, videos: list) -> list:
    if not videos:
        return []
    try:
        client = anthropic.Anthropic()
        video_catalog = "\n".join([f"- {v['filename']}: {v['description']} (Duration: {v['duration']:.0f}s)" for v in videos])
        prompt = f"""You are a fitness video curator. Identify the most relevant videos.

USER REQUEST: {query}

VIDEO LIBRARY:
{video_catalog}

Select 3-6 most relevant videos in logical order.

Response format (JSON only):
{{
  "videos": [
    {{"filename": "name.mp4", "reason": "why this matches"}},
    ...
  ]
}}
"""
        response = client.messages.create(model="claude-opus-4-6", max_tokens=500, messages=[{"role": "user", "content": prompt}])
        import re
        response_text = response.content[0].text
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        if json_match:
            result = json.loads(json_match.group())
            matched = []
            for video_ref in result.get('videos', []):
                for video in videos:
                    if video['filename'] == video_ref['filename']:
                        matched.append({'filename': video['filename'], 'duration': video['duration'], 'reason': video_ref.get('reason', ''), 'file_id': video.get('file_id')})
                        break
            return matched
    except:
        pass
    return []

def create_text_slate(text: str, duration: float = 2.5) -> str:
    try:
        slate_path = os.path.join(tempfile.gettempdir(), f'slate_{int(datetime.now().timestamp())}.mp4')
        cmd = ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=1920x1080:d=' + str(duration), '-vf', f"drawtext=text='{text}':fontsize=60:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2", '-pix_fmt', 'yuv420p',
