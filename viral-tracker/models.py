"""공통 데이터 모델."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class VideoItem:
    """플랫폼 공통 영상 데이터."""

    platform: str                # "youtube" | "tiktok" | "instagram"
    video_id: str
    title: str
    url: str
    channel_id: str
    channel_name: str
    channel_url: str
    view_count: int
    upload_time: float           # epoch seconds (UTC)
    thumbnail: Optional[str] = None
    like_count: Optional[int] = None
    duration: Optional[float] = None

    # 아래는 스코어링 단계에서 채워짐
    hours_since_upload: float = 0.0
    views_per_hour: float = 0.0        # 필터링 조건 1: 시간당 조회수 상승 속도
    channel_avg_views: Optional[float] = None
    outlier_ratio: Optional[float] = None  # 필터링 조건 2: 채널 평균 대비 배수
    is_outlier: bool = False
    viral_score: float = 0.0

    def key(self) -> str:
        return f"{self.platform}:{self.video_id}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ScanStatus:
    """플랫폼별 수집 상태 (UI에 그대로 노출)."""

    platform: str
    ok: bool = True
    message: str = ""
    video_count: int = 0
