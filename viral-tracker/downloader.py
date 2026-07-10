"""yt-dlp 기반 영상 다운로드 매니저.

- 백그라운드 스레드에서 다운로드 (UI는 진행률을 폴링)
- downloads/<폴더명>/ 아래에 저장 — 폴더명은 UI에서 직접 지정 가능하며,
  지정하지 않으면 "<플랫폼>/<키워드>" 로 자동 분류된다.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional

DOWNLOAD_ROOT = os.environ.get(
    "DOWNLOAD_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
)
COOKIES_FILE = os.environ.get("COOKIES_FILE", "").strip()

_SAFE_SEG = re.compile(r"[^\w\-. 가-힣ㄱ-ㅎㅏ-ㅣ]")


def sanitize_folder(name: str) -> str:
    """경로 탈출을 막고 파일시스템에 안전한 폴더 경로로 정리 (하위 폴더 1단계 허용)."""
    parts = []
    for seg in name.replace("\\", "/").split("/"):
        seg = _SAFE_SEG.sub("_", seg).strip(" .")
        if seg and seg not in (".", ".."):
            parts.append(seg)
    return "/".join(parts[:2]) or "etc"


@dataclass
class DownloadJob:
    job_id: str
    url: str
    folder: str
    title: str = ""
    status: str = "queued"        # queued | downloading | done | error
    progress: float = 0.0         # 0~100
    filepath: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class DownloadManager:
    def __init__(self) -> None:
        self._jobs: dict[str, DownloadJob] = {}
        self._lock = threading.Lock()

    def start(self, url: str, folder: str, title: str = "") -> DownloadJob:
        folder = sanitize_folder(folder)
        job = DownloadJob(job_id=uuid.uuid4().hex[:12], url=url, folder=folder, title=title)
        with self._lock:
            self._jobs[job.job_id] = job
        threading.Thread(target=self._run, args=(job,), daemon=True).start()
        return job

    def list_jobs(self) -> list[dict]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
            return [j.to_dict() for j in jobs[:100]]

    def _run(self, job: DownloadJob) -> None:
        import yt_dlp

        target_dir = os.path.join(DOWNLOAD_ROOT, job.folder)
        os.makedirs(target_dir, exist_ok=True)
        job.status = "downloading"

        def hook(d: dict) -> None:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                if total:
                    job.progress = round(d.get("downloaded_bytes", 0) / total * 100, 1)
            elif d.get("status") == "finished":
                job.progress = 100.0
                job.filepath = d.get("filename")

        opts = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": os.path.join(target_dir, "%(title).80s [%(id)s].%(ext)s"),
            "format": "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b",
            "merge_output_format": "mp4",
            "progress_hooks": [hook],
            "noplaylist": True,
        }
        if COOKIES_FILE and os.path.exists(COOKIES_FILE):
            opts["cookiefile"] = COOKIES_FILE

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(job.url, download=True)
                if info and not job.title:
                    job.title = info.get("title", "")
                if info and not job.filepath:
                    reqs = info.get("requested_downloads") or []
                    if reqs:
                        job.filepath = reqs[0].get("filepath")
            job.status = "done"
            job.progress = 100.0
        except Exception as e:  # noqa: BLE001 - 실패 사유를 UI로 그대로 전달
            job.status = "error"
            job.error = str(e)[:500]


manager = DownloadManager()
