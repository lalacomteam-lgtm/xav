"""급상승 점수(Viral Score) 계산 로직.

기준:
  1. velocity  = 현재 조회수 ÷ 업로드 이후 경과 시간(시간당 조회수)
  2. outlier   = 현재 조회수 ÷ 채널 평균 조회수 (기본 3배 이상만 통과)

두 값을 정규화해 가중 합산한 0~100 점이 Viral Score.
"""
from __future__ import annotations

import math
import time
from typing import Iterable, Optional

from models import VideoItem

# 업로드 직후(수 분 이내) 영상의 velocity가 무한대로 튀는 것을 막는 하한선
MIN_HOURS = 0.25

VELOCITY_WEIGHT = 0.6
OUTLIER_WEIGHT = 0.4

# outlier_ratio는 이 값에서 만점 처리 (평균의 10배면 이미 확실한 아웃라이어)
OUTLIER_RATIO_CAP = 10.0


def enrich_metrics(video: VideoItem, now: Optional[float] = None) -> None:
    """velocity / outlier_ratio 등 개별 지표를 채운다."""
    now = now or time.time()
    hours = max((now - video.upload_time) / 3600.0, MIN_HOURS)
    video.hours_since_upload = round(hours, 2)
    video.views_per_hour = round(video.view_count / hours, 1)

    if video.channel_avg_views and video.channel_avg_views > 0:
        video.outlier_ratio = round(video.view_count / video.channel_avg_views, 2)
    else:
        video.outlier_ratio = None


def apply_outlier_filter(
    videos: list[VideoItem],
    min_ratio: float = 3.0,
    keep_unknown: bool = True,
) -> list[VideoItem]:
    """채널 평균 대비 min_ratio 배 이상인 아웃라이어 영상만 남긴다.

    keep_unknown=True 이면 채널 평균을 구하지 못한 영상(신규 채널 등)은
    탈락시키지 않고 is_outlier=False 상태로 유지한다.
    """
    kept: list[VideoItem] = []
    for v in videos:
        if v.outlier_ratio is None:
            v.is_outlier = False
            if keep_unknown:
                kept.append(v)
        elif v.outlier_ratio >= min_ratio:
            v.is_outlier = True
            kept.append(v)
    return kept


def _log_minmax(values: Iterable[float]) -> list[float]:
    """조회수 계열은 분포가 극단적이라 log 스케일로 0~1 정규화한다."""
    logs = [math.log1p(max(v, 0.0)) for v in values]
    if not logs:
        return []
    lo, hi = min(logs), max(logs)
    if hi - lo < 1e-9:
        return [1.0 for _ in logs]
    return [(x - lo) / (hi - lo) for x in logs]


def compute_viral_scores(videos: list[VideoItem]) -> list[VideoItem]:
    """결과 집합 내에서 상대 점수를 계산하고 점수 내림차순으로 정렬해 반환."""
    if not videos:
        return []

    velocity_norm = _log_minmax(v.views_per_hour for v in videos)

    for v, vel_n in zip(videos, velocity_norm):
        if v.outlier_ratio is not None:
            ratio_n = min(v.outlier_ratio / OUTLIER_RATIO_CAP, 1.0)
            score = VELOCITY_WEIGHT * vel_n + OUTLIER_WEIGHT * ratio_n
        else:
            # 채널 평균이 없으면 velocity만으로 평가 (가중치 재배분)
            score = vel_n
        v.viral_score = round(score * 100, 1)

    return sorted(videos, key=lambda v: v.viral_score, reverse=True)
