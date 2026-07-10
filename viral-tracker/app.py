"""급상승 영상 트래커 웹 서버.

실행:  uvicorn app:app --host 0.0.0.0 --port 8000
       (또는 python app.py)

흐름:
  키워드 스캔 → 플랫폼별 수집 → 24시간 필터 → velocity/outlier 계산
  → 아웃라이어(3배↑) 필터 → Viral Score 정렬 → 대시보드 표시
  대시보드는 주기적으로 자동 재스캔(기본 5분)하고, 프런트는 15초마다 폴링한다.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import scoring
from collectors import COLLECTORS
from models import ScanStatus
from downloader import manager as download_manager, DOWNLOAD_ROOT

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
log = logging.getLogger("app")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "300"))  # 자동 재스캔 주기(초)

app = FastAPI(title="Viral Video Tracker")


# ---------------------------------------------------------------- 상태
class State:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.keyword: str = ""
        self.platforms: list[str] = ["youtube", "tiktok", "instagram"]
        self.min_ratio: float = 3.0
        self.strict_outlier: bool = False   # True면 채널 평균 없는 영상도 제외
        self.videos: list[dict] = []
        self.statuses: list[dict] = []
        self.last_scan: float = 0.0
        self.scanning: bool = False


state = State()


def _run_scan() -> None:
    with state.lock:
        if state.scanning or not state.keyword:
            return
        state.scanning = True
        keyword = state.keyword
        platforms = list(state.platforms)
        min_ratio = state.min_ratio
        strict = state.strict_outlier

    log.info("scan start: %r on %s", keyword, platforms)
    videos, statuses, result = [], [], []
    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                p: pool.submit(COLLECTORS[p], keyword)
                for p in platforms if p in COLLECTORS
            }
            for p, fut in futures.items():
                try:
                    items, status = fut.result()
                except Exception as e:  # noqa: BLE001
                    items, status = [], ScanStatus(p, False, f"수집기 오류: {e}", 0)
                videos.extend(items)
                statuses.append(status.__dict__)

        now = time.time()
        for v in videos:
            scoring.enrich_metrics(v, now)
        videos = scoring.apply_outlier_filter(
            videos, min_ratio=min_ratio, keep_unknown=not strict
        )
        videos = scoring.compute_viral_scores(videos)
        result = [v.to_dict() for v in videos]
    finally:
        with state.lock:
            state.videos = result
            state.statuses = statuses
            state.last_scan = time.time()
            state.scanning = False
    log.info("scan done: %d videos", len(result))


def _auto_refresh_loop() -> None:
    """실시간성 확보: 마지막 스캔 후 REFRESH_INTERVAL 지나면 자동 재스캔."""
    while True:
        time.sleep(10)
        with state.lock:
            due = (
                state.keyword
                and not state.scanning
                and time.time() - state.last_scan >= REFRESH_INTERVAL
            )
        if due:
            _run_scan()


threading.Thread(target=_auto_refresh_loop, daemon=True).start()


# ---------------------------------------------------------------- API
class ScanRequest(BaseModel):
    keyword: str
    platforms: list[str] = ["youtube", "tiktok", "instagram"]
    min_ratio: float = 3.0
    strict_outlier: bool = False


class DownloadRequest(BaseModel):
    url: str
    folder: str = ""
    title: str = ""
    platform: str = ""


@app.post("/api/scan")
def api_scan(req: ScanRequest):
    keyword = req.keyword.strip()
    if not keyword:
        raise HTTPException(400, "키워드를 입력하세요.")
    with state.lock:
        already = state.scanning
        state.keyword = keyword
        state.platforms = req.platforms
        state.min_ratio = req.min_ratio
        state.strict_outlier = req.strict_outlier
    if not already:
        threading.Thread(target=_run_scan, daemon=True).start()
    return {"ok": True, "scanning": True}


@app.get("/api/videos")
def api_videos():
    with state.lock:
        return {
            "keyword": state.keyword,
            "platforms": state.platforms,
            "min_ratio": state.min_ratio,
            "strict_outlier": state.strict_outlier,
            "scanning": state.scanning,
            "last_scan": state.last_scan,
            "refresh_interval": REFRESH_INTERVAL,
            "videos": state.videos,
            "statuses": state.statuses,
        }


@app.post("/api/download")
def api_download(req: DownloadRequest):
    if not req.url:
        raise HTTPException(400, "url이 필요합니다.")
    folder = req.folder.strip()
    if not folder:
        with state.lock:
            kw = state.keyword or "etc"
        folder = f"{req.platform or 'video'}/{kw}"
    job = download_manager.start(req.url, folder, req.title)
    return {"ok": True, "job": job.to_dict()}


@app.get("/api/downloads")
def api_downloads():
    return {"root": DOWNLOAD_ROOT, "jobs": download_manager.list_jobs()}


@app.get("/api/folders")
def api_folders():
    """기존 다운로드 폴더 목록 (UI 자동완성용)."""
    folders: list[str] = []
    if os.path.isdir(DOWNLOAD_ROOT):
        for top in sorted(os.listdir(DOWNLOAD_ROOT)):
            top_path = os.path.join(DOWNLOAD_ROOT, top)
            if not os.path.isdir(top_path):
                continue
            subs = [
                f"{top}/{s}" for s in sorted(os.listdir(top_path))
                if os.path.isdir(os.path.join(top_path, s))
            ]
            folders.extend(subs or [top])
    return {"folders": folders}


# ---------------------------------------------------------------- 정적 파일
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))


if __name__ == "__main__":
    import uvicorn
    import webbrowser

    port = int(os.environ.get("PORT", "8000"))
    # 앱처럼 쓰도록 서버가 뜨면 브라우저를 자동으로 연다
    threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{port}")).start()
    uvicorn.run(app, host="0.0.0.0", port=port)
