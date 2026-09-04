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

def extract_keyframes(video_path: str, num_frames: int = 10) -> list:
    """Extract more keyframes for better analysis"""
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
    """Better audio transcription"""
    try:
        client = anthropic.Anthropic()
        with open(audio_path, 'rb') as f:
            audio_data = f.read()
        audio_base64 = base64.standard_b64encode(audio_data).decode('utf-8')
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": """Transcribe this fitness video audio and provide detailed analysis:

1. WORKOUT TYPE: What type of workout is this? (e.g., HIIT, yoga, strength, cardio, stretching, pilates)
2. TARGET AREAS: What body parts or muscle groups are targeted?
3. DIFFICULTY: Beginner, Intermediate, or Advanced?
4. DURATION: Estimated length of the workout
5. EQUIPMENT: What equipment is needed? (dumbbells, resistance bands, mat, none, etc.)
6. INTENSITY: How intense is this workout?
7. KEY EXERCISES: List the main exercises/movements
8. INSTRUCTIONS: Any special instructions or modifications mentioned?

Be thorough and specific. This will help users find workouts that match their needs."""
                    },
                    {
                        "type": "audio",
                        "source": {
                            "type": "base64",
                            "media_type": "audio/mp3",
                            "data": audio_base64
                        }
                    }
                ]
            }]
        )
        return response.content[0].text if response.content else ""
    except:
        return ""

def analyze_frames_vision(frames: list, video_filename: str) -> str:
    """Enhanced vision analysis with more detail"""
    if not frames:
        return "Unable to analyze video frames."
    
    try:
        client = anthropic.Anthropic()
        
        content = [{
            "type": "text",
            "text": f"""Analyze these keyframes from a fitness video: {video_filename}

Provide a detailed analysis covering:

1. WORKOUT TYPE: Identify the specific type of workout (HIIT, yoga, strength, cardio, stretching, dance, etc.)
2. INTENSITY LEVEL: Low, moderate, or high intensity?
3. TARGET MUSCLE GROUPS: Which muscles/areas are being worked?
4. EQUIPMENT VISIBLE: List any equipment, weights, machines visible
5. SETTING: Indoor studio, home, gym, outdoor?
6. BODY POSITION/FORM: What positions or exercises are shown?
7. NUMBER OF PEOPLE: How many people are visible?
8. OVERALL VIBE: Is it fast-paced, relaxing, energetic, meditative?

Be specific and detailed. This helps users find exactly what they're looking for."""
        }]
        
        for frame in frames:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": frame['media_type'],
                    "data": frame['data']
                }
            })
        
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": content}]
        )
        
        return response.content[0].text
    except:
        return "Unable to analyze frames."

def combine_analysis(vision_desc: str, audio_desc: str, filename: str) -> str:
    """Smarter combined analysis"""
    try:
        client = anthropic.Anthropic()
        
        prompt = f"""Create a comprehensive workout video description by combining these analyses:

VIDEO FILE: {filename}

VISUAL ANALYSIS:
{vision_desc}

AUDIO ANALYSIS:
{audio_desc if audio_desc else "(No audio available)"}

TASK: Create ONE rich, detailed description that:
1. Combines the best insights from both visual and audio analysis
2. Is 4-5 sentences that capture the workout type, target areas, difficulty, equipment, and intensity
3. Uses specific language that's searchable (e.g., "HIIT cardio workout", "beginner-friendly yoga")
4. Highlights what makes this workout unique or special
5. Is written for someone searching for a specific type of workout

The description will be used for AI-powered video search, so be clear and specific."""
        
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        
        return response.content[0].text
    except:
        return vision_desc

def semantic_search_videos(query: str, videos: list) -> list:
    if not videos:
        return []
    
    try:
        client = anthropic.Anthropic()
        
        video_catalog = "\n".join([
            f"- {v['filename']}: {v['description']} (Duration: {v['duration']:.0f}s)"
            for v in videos
        ])
        
        prompt = f"""You are an expert fitness video curator. A user is looking for specific workouts.

USER REQUEST: {query}

VIDEO LIBRARY:
{video_catalog}

TASK: Find the 3-6 most relevant videos that match what the user is looking for.

Consider:
- Workout type match
- Intensity level match
- Target muscles/areas match
- Equipment availability match
- Time availability match
- User skill level match

Return as JSON only:
{{
  "videos": [
    {{"filename": "name.mp4", "reason": "why this matches"}},
    ...
  ]
}}
"""
        
        response = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )
        
        import re
        response_text = response.content[0].text
        json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
        
        if json_match:
            result = json.loads(json_match.group())
            matched = []
            
            for video_ref in result.get('videos', []):
                for video in videos:
                    if video['filename'] == video_ref['filename']:
                        matched.append({
                            'filename': video['filename'],
                            'duration': video['duration'],
                            'reason': video_ref.get('reason', ''),
                            'file_id': video.get('file_id')
                        })
                        break
            
            return matched
    except:
        pass
    
    return []

def create_text_slate(text: str, duration: float = 2.5) -> str:
    try:
        slate_path = os.path.join(tempfile.gettempdir(), f'slate_{int(datetime.now().timestamp())}.mp4')
        cmd = ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=1920x1080:d=' + str(duration), '-vf', f"drawtext=text='{text}':fontsize=60:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2", '-pix_fmt', 'yuv420p', slate_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0 and os.path.exists(slate_path):
            return slate_path
    except:
        pass
    return None

def stitch_videos(video_paths: list, output_path: str) -> bool:
    if not video_paths:
        return False
    
    try:
        concat_file = os.path.join(tempfile.gettempdir(), f'concat_{int(datetime.now().timestamp())}.txt')
        slate_videos = []
        
        with open(concat_file, 'w') as f:
            for i, video_path in enumerate(video_paths):
                f.write(f"file '{os.path.abspath(video_path)}'\n")
                if i < len(video_paths) - 1:
                    slate_text = os.path.basename(video_path).replace('.mp4', '').replace('_', ' ').title()
                    slate = create_text_slate(f"Next: {slate_text}", 2.5)
                    if slate:
                        f.write(f"file '{os.path.abspath(slate)}'\n")
                        slate_videos.append(slate)
        
        cmd = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_file, '-c', 'copy', '-movflags', '+faststart', output_path]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        
        if result.returncode == 0:
            os.remove(concat_file)
            for slate in slate_videos:
                try:
                    os.remove(slate)
                except:
                    pass
            return True
    except:
        pass
    
    return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/videos', methods=['GET'])
def get_videos():
    if not drive_service or not DRIVE_FOLDER_ID:
        return jsonify({'videos': [], 'total': 0, 'error': 'Google Drive not connected'})
    
    try:
        cache_file = os.path.join(WORK_FOLDER, 'video_cache.json')
        cache = {}
        
        if os.path.exists(cache_file):
            with open(cache_file, 'r') as f:
                cache = json.load(f)
        
        videos = list_drive_files(DRIVE_FOLDER_ID)
        video_list = []
        
        for v in videos:
            if v['name'].endswith(tuple(VIDEO_EXTENSIONS)):
                entry = {'filename': v['name'], 'file_id': v['id'], 'size': v.get('size', 0), 'created': v.get('createdTime', '')}
                
                if v['id'] in cache:
                    entry['description'] = cache[v['id']].get('description', 'Not analyzed')
                else:
                    entry['description'] = 'Not analyzed'
                
                video_list.append(entry)
        
        return jsonify({'videos': video_list, 'total': len(video_list)})
    except Exception as e:
        return jsonify({'videos': [], 'total': 0, 'error': str(e)})

@app.route('/api/upload', methods=['POST'])
def upload_video():
    if not drive_service or not DRIVE_FOLDER_ID:
        return jsonify({'error': 'Google Drive not connected'}), 400
    
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    
    file = request.files['file']
    if not file or not any(file.filename.endswith(ext) for ext in VIDEO_EXTENSIONS):
        return jsonify({'error': 'Invalid video format'}), 400
    
    try:
        temp_path = os.path.join(WORK_FOLDER, file.filename)
        file.save(temp_path)
        
        file_id = upload_to_drive(temp_path, DRIVE_FOLDER_ID)
        
        os.remove(temp_path)
        
        if file_id:
            return jsonify({'filename': file.filename, 'file_id': file_id})
        else:
            return jsonify({'error': 'Upload failed'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.get_json()
    mode = data.get('mode', 'all')
    
    if not drive_service or not DRIVE_FOLDER_ID:
        return jsonify({'error': 'Google Drive not connected'}), 400
    
    cache_file = os.path.join(WORK_FOLDER, 'video_cache.json')
    cache = {}
    if os.path.exists(cache_file):
        with open(cache_file, 'r') as f:
            cache = json.load(f)
    
    videos = list_drive_files(DRIVE_FOLDER_ID)
