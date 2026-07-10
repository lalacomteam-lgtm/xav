"""플랫폼별 영상 수집기.

- YouTube   : YOUTUBE_API_KEY 가 있으면 공식 Data API v3, 없으면 yt-dlp 검색으로 대체
- TikTok    : yt-dlp 해시태그 페이지 추출 (쿠키 필요할 수 있음)
- Instagram : yt-dlp 해시태그 페이지 추출 (로그인 쿠키 필수)

모든 수집기는 (VideoItem 리스트, ScanStatus) 를 반환하며,
실패해도 예외를 밖으로 던지지 않고 ScanStatus.message 에 사유를 담는다.
"""
from __future__ import annotations

import os
import time
import logging
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import Optional

import httpx

from models import VideoItem, ScanStatus

log = logging.getLogger("collectors")

# ---------------------------------------------------------------- 설정
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
# 틱톡/인스타그램용 넷스케이프 형식 cookies.txt (브라우저 확장으로 내보내기)
COOKIES_FILE = os.environ.get("COOKIES_FILE", "").strip()

MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "25"))          # 플랫폼당 후보 수
WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "24"))        # 최근 N시간 이내만
CHANNEL_AVG_SAMPLE = 10                                          # 채널 평균 계산에 쓸 최근 영상 수
CHANNEL_AVG_TTL = 6 * 3600                                       # 채널 평균 캐시 유지 시간(초)

# 채널 평균 조회수 캐시: {"platform:channel_id": (timestamp, avg)}
_channel_avg_cache: dict[str, tuple[float, Optional[float]]] = {}


def _cache_get(key: str) -> Optional[float] | None:
    hit = _channel_avg_cache.get(key)
    if hit and time.time() - hit[0] < CHANNEL_AVG_TTL:
        return hit[1]
    return None


def _cache_put(key: str, avg: Optional[float]) -> None:
    _channel_avg_cache[key] = (time.time(), avg)


# ---------------------------------------------------------------- yt-dlp 공통
def _ydl_opts(flat: bool = False, playlist_end: Optional[int] = None) -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": True,
        "socket_timeout": 20,
    }
    if flat:
        opts["extract_flat"] = "in_playlist"
    if playlist_end:
        opts["playlist_items"] = f"1:{playlist_end}"
    if COOKIES_FILE and os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts


def _extract(url: str, flat: bool = False, playlist_end: Optional[int] = None):
    import yt_dlp

    with yt_dlp.YoutubeDL(_ydl_opts(flat=flat, playlist_end=playlist_end)) as ydl:
        return ydl.extract_info(url, download=False)


def _within_window(ts: Optional[float]) -> bool:
    if not ts:
        return False
    return (time.time() - ts) <= WINDOW_HOURS * 3600


def _ydl_channel_avg(platform: str, channel_id: str, videos_url: str) -> Optional[float]:
    """yt-dlp flat 추출로 채널 최근 영상들의 평균 조회수를 구한다."""
    key = f"{platform}:{channel_id}"
    cached = _cache_get(key)
    if cached is not None:
        return cached
    if key in _channel_avg_cache:  # TTL 내 '평균 없음(None)' 결과도 재시도하지 않음
        ts, val = _channel_avg_cache[key]
        if time.time() - ts < CHANNEL_AVG_TTL:
            return val
    try:
        info = _extract(videos_url, flat=True, playlist_end=CHANNEL_AVG_SAMPLE + 2)
        entries = (info or {}).get("entries") or []
        views = [e.get("view_count") for e in entries if e and e.get("view_count")]
        avg = mean(views[:CHANNEL_AVG_SAMPLE]) if len(views) >= 3 else None
    except Exception as e:  # noqa: BLE001 - 수집 실패는 평균 없음으로 처리
        log.warning("channel avg failed for %s: %s", videos_url, e)
        avg = None
    _cache_put(key, avg)
    return avg


# ---------------------------------------------------------------- YouTube
def collect_youtube(keyword: str) -> tuple[list[VideoItem], ScanStatus]:
    if YOUTUBE_API_KEY:
        try:
            return _youtube_via_api(keyword)
        except Exception as e:  # noqa: BLE001
            log.exception("YouTube API failed, falling back to yt-dlp")
            items, status = _youtube_via_ytdlp(keyword)
            status.message = f"API 오류로 yt-dlp 대체 사용: {e}. " + status.message
            return items, status
    return _youtube_via_ytdlp(keyword)


def _youtube_via_api(keyword: str) -> tuple[list[VideoItem], ScanStatus]:
    base = "https://www.googleapis.com/youtube/v3"
    published_after = (
        datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    with httpx.Client(timeout=20) as client:
        r = client.get(f"{base}/search", params={
            "key": YOUTUBE_API_KEY, "part": "id", "q": keyword, "type": "video",
            "order": "viewCount", "publishedAfter": published_after,
            "maxResults": min(MAX_RESULTS, 50),
        })
        r.raise_for_status()
        ids = [it["id"]["videoId"] for it in r.json().get("items", [])]
        if not ids:
            return [], ScanStatus("youtube", True, "최근 24시간 검색 결과 없음", 0)

        r = client.get(f"{base}/videos", params={
            "key": YOUTUBE_API_KEY, "part": "snippet,statistics",
            "id": ",".join(ids), "maxResults": 50,
        })
        r.raise_for_status()
        videos_raw = r.json().get("items", [])

        items: list[VideoItem] = []
        for v in videos_raw:
            sn, st = v["snippet"], v.get("statistics", {})
            ts = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00")).timestamp()
            thumbs = sn.get("thumbnails", {})
            thumb = (thumbs.get("medium") or thumbs.get("default") or {}).get("url")
            items.append(VideoItem(
                platform="youtube",
                video_id=v["id"],
                title=sn.get("title", ""),
                url=f"https://www.youtube.com/watch?v={v['id']}",
                channel_id=sn.get("channelId", ""),
                channel_name=sn.get("channelTitle", ""),
                channel_url=f"https://www.youtube.com/channel/{sn.get('channelId', '')}",
                view_count=int(st.get("viewCount", 0)),
                like_count=int(st["likeCount"]) if st.get("likeCount") else None,
                upload_time=ts,
                thumbnail=thumb,
            ))

        _fill_youtube_channel_avgs_api(client, base, items)

    return items, ScanStatus("youtube", True, "YouTube Data API 사용", len(items))


def _fill_youtube_channel_avgs_api(client: httpx.Client, base: str, items: list[VideoItem]) -> None:
    """채널별 업로드 재생목록에서 최근 영상 조회수 평균을 계산 (캐시 적용)."""
    channel_ids = {v.channel_id for v in items if v.channel_id}
    need = [c for c in channel_ids if _cache_get(f"youtube:{c}") is None]

    uploads: dict[str, str] = {}
    for i in range(0, len(need), 50):
        r = client.get(f"{base}/channels", params={
            "key": YOUTUBE_API_KEY, "part": "contentDetails",
            "id": ",".join(need[i:i + 50]), "maxResults": 50,
        })
        r.raise_for_status()
        for ch in r.json().get("items", []):
            pl = ch["contentDetails"]["relatedPlaylists"].get("uploads")
            if pl:
                uploads[ch["id"]] = pl

    for cid, playlist in uploads.items():
        try:
            r = client.get(f"{base}/playlistItems", params={
                "key": YOUTUBE_API_KEY, "part": "contentDetails",
                "playlistId": playlist, "maxResults": CHANNEL_AVG_SAMPLE,
            })
            r.raise_for_status()
            vids = [it["contentDetails"]["videoId"] for it in r.json().get("items", [])]
            if not vids:
                _cache_put(f"youtube:{cid}", None)
                continue
            r = client.get(f"{base}/videos", params={
                "key": YOUTUBE_API_KEY, "part": "statistics",
                "id": ",".join(vids), "maxResults": 50,
            })
            r.raise_for_status()
            views = [int(v["statistics"].get("viewCount", 0)) for v in r.json().get("items", [])]
            avg = mean(views) if len(views) >= 3 else None
            _cache_put(f"youtube:{cid}", avg)
        except Exception as e:  # noqa: BLE001
            log.warning("channel avg (API) failed for %s: %s", cid, e)
            _cache_put(f"youtube:{cid}", None)

    for v in items:
        v.channel_avg_views = _cache_get(f"youtube:{v.channel_id}")


# 유튜브 검색 필터 protobuf: 정렬=조회수, 업로드=오늘, 타입=영상
_YT_SEARCH_SP = "CAMSBAgCEAE%3D"


def _youtube_via_ytdlp(keyword: str) -> tuple[list[VideoItem], ScanStatus]:
    """API 키가 없을 때: '오늘 업로드 + 조회수순' 필터 검색 후 영상별 메타데이터 추출."""
    from urllib.parse import quote

    search_url = (
        f"https://www.youtube.com/results?search_query={quote(keyword)}&sp={_YT_SEARCH_SP}"
    )
    try:
        info = _extract(search_url, flat=True, playlist_end=MAX_RESULTS)
    except Exception:  # noqa: BLE001 - 필터 검색이 막히면 일반 검색으로 대체
        try:
            info = _extract(f"ytsearch{MAX_RESULTS}:{keyword}", flat=True)
        except Exception as e:  # noqa: BLE001
            return [], ScanStatus("youtube", False, f"yt-dlp 검색 실패: {e}", 0)

    entries = [e for e in ((info or {}).get("entries") or []) if e]
    items: list[VideoItem] = []
    for e in entries:
        url = e.get("url") or f"https://www.youtube.com/watch?v={e.get('id')}"
        try:
            v = _extract(url)
        except Exception:  # noqa: BLE001
            continue
        if not v or not _within_window(v.get("timestamp")):
            continue
        cid = v.get("channel_id") or ""
        item = VideoItem(
            platform="youtube",
            video_id=v.get("id", ""),
            title=v.get("title", ""),
            url=v.get("webpage_url", url),
            channel_id=cid,
            channel_name=v.get("channel") or v.get("uploader") or "",
            channel_url=v.get("channel_url") or "",
            view_count=int(v.get("view_count") or 0),
            like_count=v.get("like_count"),
            upload_time=float(v.get("timestamp")),
            thumbnail=v.get("thumbnail"),
            duration=v.get("duration"),
        )
        if cid:
            item.channel_avg_views = _ydl_channel_avg(
                "youtube", cid,
                (v.get("channel_url") or f"https://www.youtube.com/channel/{cid}") + "/videos",
            )
        items.append(item)

    msg = "yt-dlp 검색 사용 (YOUTUBE_API_KEY 설정 시 더 빠르고 정확)"
    return items, ScanStatus("youtube", True, msg, len(items))


# ---------------------------------------------------------------- TikTok
def collect_tiktok(keyword: str) -> tuple[list[VideoItem], ScanStatus]:
    tag = keyword.lstrip("#").replace(" ", "")
    try:
        info = _extract(f"https://www.tiktok.com/tag/{tag}", flat=True, playlist_end=MAX_RESULTS)
    except Exception as e:  # noqa: BLE001
        return [], ScanStatus(
            "tiktok", False,
            f"틱톡 해시태그 수집 실패: {e} — COOKIES_FILE(로그인 쿠키) 설정이 필요할 수 있습니다.", 0,
        )

    entries = [e for e in ((info or {}).get("entries") or []) if e]
    items: list[VideoItem] = []
    for e in entries[:MAX_RESULTS]:
        url = e.get("url") or e.get("webpage_url")
        if not url:
            continue
        try:
            v = _extract(url)
        except Exception:  # noqa: BLE001
            continue
        if not v or not _within_window(v.get("timestamp")):
            continue
        uploader = v.get("uploader") or v.get("channel") or ""
        uid = v.get("uploader_id") or uploader
        item = VideoItem(
            platform="tiktok",
            video_id=str(v.get("id", "")),
            title=v.get("title") or v.get("description") or "",
            url=v.get("webpage_url", url),
            channel_id=uid,
            channel_name=uploader,
            channel_url=f"https://www.tiktok.com/@{uid}" if uid else "",
            view_count=int(v.get("view_count") or 0),
            like_count=v.get("like_count"),
            upload_time=float(v.get("timestamp")),
            thumbnail=v.get("thumbnail"),
            duration=v.get("duration"),
        )
        if uid:
            item.channel_avg_views = _ydl_channel_avg(
                "tiktok", uid, f"https://www.tiktok.com/@{uid}",
            )
        items.append(item)

    if not items:
        return items, ScanStatus(
            "tiktok", False,
            "결과 없음 — 틱톡은 봇 차단이 강해 COOKIES_FILE 설정을 권장합니다.", 0,
        )
    return items, ScanStatus("tiktok", True, "yt-dlp 해시태그 수집", len(items))


# ---------------------------------------------------------------- Instagram
def collect_instagram(keyword: str) -> tuple[list[VideoItem], ScanStatus]:
    if not (COOKIES_FILE and os.path.exists(COOKIES_FILE)):
        return [], ScanStatus(
            "instagram", False,
            "인스타그램은 로그인 쿠키가 필수입니다. COOKIES_FILE 환경변수에 cookies.txt 경로를 지정하세요.", 0,
        )
    tag = keyword.lstrip("#").replace(" ", "")
    try:
        info = _extract(
            f"https://www.instagram.com/explore/tags/{tag}/",
            flat=True, playlist_end=MAX_RESULTS,
        )
    except Exception as e:  # noqa: BLE001
        return [], ScanStatus("instagram", False, f"인스타그램 해시태그 수집 실패: {e}", 0)

    entries = [e for e in ((info or {}).get("entries") or []) if e]
    items: list[VideoItem] = []
    for e in entries[:MAX_RESULTS]:
        url = e.get("url") or e.get("webpage_url")
        if not url:
            continue
        try:
            v = _extract(url)
        except Exception:  # noqa: BLE001
            continue
        if not v or not _within_window(v.get("timestamp")):
            continue
        uploader = v.get("uploader") or v.get("channel") or ""
        uid = v.get("uploader_id") or uploader
        item = VideoItem(
            platform="instagram",
            video_id=str(v.get("id", "")),
            title=v.get("title") or v.get("description") or "",
            url=v.get("webpage_url", url),
            channel_id=uid,
            channel_name=uploader,
            channel_url=f"https://www.instagram.com/{uid}/" if uid else "",
            view_count=int(v.get("view_count") or 0),
            like_count=v.get("like_count"),
            upload_time=float(v.get("timestamp")),
            thumbnail=v.get("thumbnail"),
            duration=v.get("duration"),
        )
        if uid:
            item.channel_avg_views = _ydl_channel_avg(
                "instagram", uid, f"https://www.instagram.com/{uid}/reels/",
            )
        items.append(item)

    if not items:
        return items, ScanStatus("instagram", False, "최근 24시간 이내 결과 없음 (쿠키/차단 여부 확인)", 0)
    return items, ScanStatus("instagram", True, "yt-dlp 해시태그 수집", len(items))


COLLECTORS = {
    "youtube": collect_youtube,
    "tiktok": collect_tiktok,
    "instagram": collect_instagram,
}
