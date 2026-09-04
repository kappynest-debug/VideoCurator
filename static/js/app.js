// Video Curator Web App - JavaScript

let selectedVideos = [];

// ===== TAB NAVIGATION =====
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        const tabName = btn.dataset.tab;
        
        document.querySelectorAll('.tab-content').forEach(tab => {
            tab.classList.remove('active');
        });
        
        document.querySelectorAll('.tab-btn').forEach(b => {
            b.classList.remove('active');
        });
        
        document.getElementById(tabName).classList.add('active');
        btn.classList.add('active');
        
        if (tabName === 'upload') {
            refreshVideoList();
        } else if (tabName === 'outputs') {
            refreshOutputList();
        }
    });
});

// ===== FILE UPLOAD =====
const uploadArea = document.getElementById('uploadArea');
const fileInput = document.getElementById('fileInput');

uploadArea.addEventListener('click', () => fileInput.click());
uploadArea.addEventListener('dragover', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = 'var(--primary-color)';
    uploadArea.style.backgroundColor = 'rgba(99, 102, 241, 0.05)';
});

uploadArea.addEventListener('dragleave', () => {
    uploadArea.style.borderColor = '';
    uploadArea.style.backgroundColor = '';
});

uploadArea.addEventListener('drop', (e) => {
    e.preventDefault();
    uploadArea.style.borderColor = '';
    uploadArea.style.backgroundColor = '';
    handleFiles(e.dataTransfer.files);
});

fileInput.addEventListener('change', (e) => {
    handleFiles(e.target.files);
});

function handleFiles(files) {
    Array.from(files).forEach(file => {
        uploadFile(file);
    });
}

function uploadFile(file) {
    const formData = new FormData();
    formData.append('file', file);
    
    const progressDiv = document.getElementById('uploadProgress');
    progressDiv.style.display = 'block';
    document.getElementById('uploadFileName').textContent = file.name;
    
    fetch('/api/upload', {
        method: 'POST',
        body: formData
    })
    .then(response => response.json())
    .then(data => {
        if (data.error) {
            alert('Error: ' + data.error);
        } else {
            refreshVideoList();
        }
        progressDiv.style.display = 'none';
    })
    .catch(error => {
        alert('Upload failed: ' + error);
        progressDiv.style.display = 'none';
    });
}

// ===== VIDEO LIST =====
function refreshVideoList() {
    fetch('/api/videos')
        .then(response => response.json())
        .then(data => {
            document.getElementById('videoCount').textContent = data.total;
            const videosList = document.getElementById('videosList');
            
            if (data.videos.length === 0) {
                videosList.innerHTML = '<p class="empty">No videos uploaded yet</p>';
            } else {
                videosList.innerHTML = data.videos.map(video => `
                    <div class="video-item">
                        <div class="video-info">
                            <div class="video-name">📹 ${escapeHtml(video.filename)}</div>
                            <div class="video-description">
                                ${video.description || 'Not analyzed yet'}
                            </div>
                        </div>
                        <div class="video-analyzed">✓ Analyzed</div>
                    </div>
                `).join('');
            }
        });
}

// ===== ANALYZE =====
document.getElementById('analyzeAllBtn')?.addEventListener('click', () => {
    analyzeVideos('all');
});

document.getElementById('analyzeNewBtn')?.addEventListener('click', () => {
    analyzeVideos('new');
});

function analyzeVideos(mode) {
    const progressDiv = document.getElementById('analyzeProgress');
    progressDiv.style.display = 'block';
    
    fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: mode })
    })
    .then(response => response.json())
    .then(data => {
        if (data.count === 0) {
            alert('No videos to analyze');
            progressDiv.style.display = 'none';
        } else {
            pollAnalysisStatus();
        }
    })
    .catch(error => {
        alert('Analysis failed: ' + error);
        progressDiv.style.display = 'none';
    });
}

function pollAnalysisStatus() {
    const progressDiv = document.getElementById('analyzeProgress');
    
    const interval = setInterval(() => {
        fetch('/api/videos')
            .then(response => response.json())
            .then(data => {
                refreshVideoList();
                
                if (data.videos.length > 0) {
                    clearInterval(interval);
                    progressDiv.style.display = 'none';
                    alert('✅ All videos analyzed!');
                }
            });
    }, 5000);
    
    setTimeout(() => clearInterval(interval), 30 * 60 * 1000);
}

// ===== SEARCH =====
document.getElementById('searchBtn')?.addEventListener('click', () => {
    const query = document.getElementById('queryInput').value;
    
    if (!query.trim()) {
        alert('Please enter a search query');
        return;
    }
    
    fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: query })
    })
    .then(response => response.json())
    .then(data => {
        displaySearchResults(data.videos);
    })
    .catch(error => {
        alert('Search failed: ' + error);
    });
});

function displaySearchResults(videos) {
    const resultsDiv = document.getElementById('searchResults');
    const resultsList = document.getElementById('resultsList');
    
    resultsDiv.style.display = 'block';
    document.getElementById('matchCount').textContent = videos.length;
    
    selectedVideos = videos;
    
    resultsList.innerHTML = videos.map((video, idx) => `
        <div class="result-item">
            <div style="flex: 1;">
                <input
