# 🔥 급상승 영상 트래커 (Viral Video Tracker)

인스타그램 · 유튜브 · 틱톡에서 **특정 키워드/해시태그로 검색된 최근 24시간 이내 영상** 중
급상승 중인 영상을 실시간으로 찾아주는 웹 대시보드입니다.

## 판별 로직

| 기준 | 계산 | 역할 |
|---|---|---|
| ① 상승 속도 (velocity) | `현재 조회수 ÷ 업로드 후 경과 시간` | 시간당 조회수가 높은 순 정렬 |
| ② 아웃라이어 | `현재 조회수 ÷ 채널 평균 조회수` | 채널 평균의 **3배 이상**인 영상만 통과 (2/3/5/10배 선택 가능) |
| ③ Viral Score | `velocity 정규화 × 0.6 + 아웃라이어 배수 × 0.4` → 0~100점 | 최종 정렬 기준 |

- velocity는 조회수 분포가 극단적이라 **log 스케일**로 정규화합니다.
- 채널 평균을 구할 수 없는 영상(신규 채널 등)은 기본적으로 velocity만으로 평가하며,
  "평균 미확인 영상 제외" 옵션을 켜면 완전히 걸러냅니다.
- 대시보드는 15초마다 화면을 갱신하고, 서버는 기본 5분마다 자동 재스캔합니다.

## 설치 & 실행

```bash
cd viral-tracker
pip install -r requirements.txt

# (선택) 유튜브 공식 API — 훨씬 빠르고 정확함
export YOUTUBE_API_KEY="AIza..."

# (선택) 틱톡/인스타그램용 로그인 쿠키 (Netscape cookies.txt 형식)
export COOKIES_FILE="/path/to/cookies.txt"

python app.py            # http://localhost:8000
```

## 사용법

1. 키워드/해시태그 입력 → 플랫폼 선택 → **스캔**
2. Viral Score 순으로 정렬된 카드에서:
   - **링크 열기** — 원본 영상으로 이동
   - **영상 저장** — 폴더를 지정해 다운로드 (비워두면 `플랫폼/키워드` 폴더로 자동 분류)
3. 우측 하단 패널에서 다운로드 진행률 확인 → `viral-tracker/downloads/` 아래 저장

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `YOUTUBE_API_KEY` | (없음) | 유튜브 Data API v3 키. 없으면 yt-dlp 검색으로 자동 대체 (느림) |
| `COOKIES_FILE` | (없음) | 틱톡/인스타그램 로그인 쿠키 파일 경로 |
| `REFRESH_INTERVAL` | `300` | 자동 재스캔 주기(초) |
| `MAX_RESULTS` | `25` | 플랫폼당 수집 후보 수 |
| `WINDOW_HOURS` | `24` | 최근 N시간 이내 영상만 대상 |
| `DOWNLOAD_DIR` | `./downloads` | 영상 저장 루트 폴더 |
| `PORT` | `8000` | 서버 포트 |

## 플랫폼별 제약 (중요)

- **YouTube** — 가장 안정적. API 키 없이도 동작하지만, 키가 있으면 검색 정확도·속도가 크게 좋아집니다.
  키 발급: [Google Cloud Console](https://console.cloud.google.com/) → YouTube Data API v3.
- **TikTok** — 공식 검색 API가 없어 yt-dlp로 해시태그 페이지를 수집합니다.
  봇 차단이 강해 `COOKIES_FILE`(로그인 쿠키) 설정을 권장하며, 그래도 간헐적으로 실패할 수 있습니다.
- **Instagram** — 로그인 쿠키(`COOKIES_FILE`)가 **필수**입니다. 쿠키가 없으면 해당 플랫폼은 건너뛰고
  상태 배지에 사유가 표시됩니다.
- 영상 다운로드는 **본인에게 권한이 있는 콘텐츠**에만 사용하세요. 각 플랫폼 약관과 저작권을 준수해야 합니다.

## 구조

```
viral-tracker/
├── app.py            # FastAPI 서버 + 자동 재스캔 루프
├── collectors.py     # 플랫폼별 수집기 (YouTube API / yt-dlp)
├── scoring.py        # velocity · 아웃라이어 · Viral Score 계산
├── downloader.py     # yt-dlp 다운로드 매니저 (폴더 분류 저장)
├── models.py         # 공통 데이터 모델
└── static/           # 대시보드 (HTML/CSS/JS)
```
