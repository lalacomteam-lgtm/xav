/* 급상승 영상 트래커 — 프런트엔드
 * /api/videos 를 15초마다 폴링해 실시간으로 순위를 갱신한다.
 */
const POLL_MS = 15000;
const DL_POLL_MS = 3000;

const $ = (sel) => document.querySelector(sel);

let pendingDownload = null; // 모달에서 저장 대기 중인 영상
let lastData = null;

/* ---------------------------------------------------- 유틸 */
function fmt(n) {
  if (n === null || n === undefined) return "-";
  if (n >= 1e8) return (n / 1e8).toFixed(1) + "억";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + "만";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "천";
  return String(Math.round(n));
}

function hoursFmt(h) {
  if (h < 1) return Math.round(h * 60) + "분 전";
  return h.toFixed(1) + "시간 전";
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s ?? "";
  return d.innerHTML;
}

const PLATFORM_META = {
  youtube: { label: "YouTube", cls: "yt" },
  tiktok: { label: "TikTok", cls: "tt" },
  instagram: { label: "Instagram", cls: "ig" },
};

/* ---------------------------------------------------- 스캔 */
$("#scanForm").addEventListener("submit", async (e) => {
  e.preventDefault();
  const platforms = [...document.querySelectorAll(".platform-checks input:checked")].map((c) => c.value);
  if (!platforms.length) { alert("플랫폼을 하나 이상 선택하세요."); return; }
  const body = {
    keyword: $("#keyword").value.trim(),
    platforms,
    min_ratio: parseFloat($("#minRatio").value),
    strict_outlier: $("#strictOutlier").checked,
  };
  const r = await fetch("/api/scan", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    alert(err.detail || "스캔 요청 실패");
    return;
  }
  $("#scanning").classList.remove("hidden");
  $("#empty").classList.add("hidden");
  refreshVideos();
});

/* ---------------------------------------------------- 결과 렌더링 */
function render(data) {
  lastData = data;
  $("#scanning").classList.toggle("hidden", !data.scanning);

  if (data.last_scan) {
    const t = new Date(data.last_scan * 1000);
    $("#lastScan").textContent = "마지막 스캔 " + t.toLocaleTimeString("ko-KR");
  }

  // 플랫폼 상태 배지
  $("#statuses").innerHTML = (data.statuses || [])
    .map((s) => {
      const meta = PLATFORM_META[s.platform] || { label: s.platform, cls: "" };
      return `<span class="status ${s.ok ? "ok" : "fail"}" title="${esc(s.message)}">
        ${meta.label}: ${s.ok ? s.video_count + "개" : "⚠"} </span>`;
    })
    .join("");

  const vids = data.videos || [];
  $("#empty").classList.toggle("hidden", !!(vids.length || data.scanning || !data.keyword));
  if (!vids.length && data.keyword && !data.scanning) {
    $("#empty").classList.remove("hidden");
    $("#empty").textContent = "조건에 맞는 급상승 영상이 없습니다. (플랫폼 상태 배지에 마우스를 올려 사유 확인)";
  }

  $("#results").innerHTML = vids.map((v, i) => cardHtml(v, i + 1)).join("");
}

function cardHtml(v, rank) {
  const meta = PLATFORM_META[v.platform] || { label: v.platform, cls: "" };
  const ratio = v.outlier_ratio !== null && v.outlier_ratio !== undefined
    ? `<span class="metric ${v.is_outlier ? "hot" : ""}">${v.outlier_ratio}× 채널평균</span>`
    : `<span class="metric dim">채널평균 미확인</span>`;
  return `
  <article class="card">
    <div class="rank">#${rank}</div>
    <a class="thumb" href="${esc(v.url)}" target="_blank" rel="noopener">
      ${v.thumbnail ? `<img src="${esc(v.thumbnail)}" loading="lazy" alt="">` : `<div class="no-thumb">${meta.label}</div>`}
    </a>
    <div class="info">
      <div class="badges">
        <span class="platform ${meta.cls}">${meta.label}</span>
        <span class="score" title="Viral Score (velocity 60% + outlier 40%)">🔥 ${v.viral_score}</span>
      </div>
      <a class="title" href="${esc(v.url)}" target="_blank" rel="noopener">${esc(v.title) || "(제목 없음)"}</a>
      <a class="channel" href="${esc(v.channel_url)}" target="_blank" rel="noopener">${esc(v.channel_name)}</a>
      <div class="metrics">
        <span class="metric">조회수 ${fmt(v.view_count)}</span>
        <span class="metric hot">시간당 +${fmt(v.views_per_hour)}</span>
        ${ratio}
        <span class="metric dim">${hoursFmt(v.hours_since_upload)}</span>
      </div>
    </div>
    <div class="actions">
      <a class="btn link" href="${esc(v.url)}" target="_blank" rel="noopener">링크 열기</a>
      <button class="btn save" data-url="${esc(v.url)}" data-title="${esc(v.title)}" data-platform="${v.platform}">
        영상 저장
      </button>
    </div>
  </article>`;
}

/* ---------------------------------------------------- 저장 모달 */
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".btn.save");
  if (!btn) return;
  pendingDownload = {
    url: btn.dataset.url,
    title: btn.dataset.title,
    platform: btn.dataset.platform,
  };
  $("#modalTitle").textContent = pendingDownload.title || pendingDownload.url;
  $("#folderInput").value = "";
  $("#folderInput").placeholder =
    `비워두면: ${pendingDownload.platform}/${(lastData && lastData.keyword) || "키워드"}`;
  loadFolders();
  $("#folderModal").classList.remove("hidden");
});

$("#modalCancel").addEventListener("click", () => $("#folderModal").classList.add("hidden"));
$("#folderModal").addEventListener("click", (e) => {
  if (e.target === $("#folderModal")) $("#folderModal").classList.add("hidden");
});

$("#modalSave").addEventListener("click", async () => {
  if (!pendingDownload) return;
  const body = { ...pendingDownload, folder: $("#folderInput").value.trim() };
  $("#folderModal").classList.add("hidden");
  const r = await fetch("/api/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) alert("다운로드 요청 실패");
  refreshDownloads();
});

async function loadFolders() {
  try {
    const r = await fetch("/api/folders");
    const { folders } = await r.json();
    $("#folderList").innerHTML = folders.map((f) => `<option value="${esc(f)}">`).join("");
  } catch { /* 무시 */ }
}

/* ---------------------------------------------------- 다운로드 패널 */
function jobHtml(j) {
  const icons = { queued: "⏸", downloading: "⬇", done: "✅", error: "❌" };
  return `
  <div class="job ${j.status}">
    <span class="job-icon">${icons[j.status] || ""}</span>
    <div class="job-info">
      <div class="job-title">${esc(j.title || j.url)}</div>
      <div class="job-sub">📁 ${esc(j.folder)} ${j.status === "downloading" ? "· " + j.progress + "%" : ""}
        ${j.status === "error" ? `<span class="job-err" title="${esc(j.error)}">실패</span>` : ""}
      </div>
      ${j.status === "downloading" ? `<div class="bar"><div style="width:${j.progress}%"></div></div>` : ""}
    </div>
  </div>`;
}

async function refreshDownloads() {
  try {
    const r = await fetch("/api/downloads");
    const data = await r.json();
    $("#dlRoot").textContent = data.root;
    const panel = $("#downloadPanel");
    panel.classList.toggle("has-jobs", data.jobs.length > 0);
    $("#downloads").innerHTML = data.jobs.length
      ? data.jobs.map(jobHtml).join("")
      : `<p class="dim">저장한 영상이 여기에 표시됩니다.</p>`;
  } catch { /* 무시 */ }
}

/* ---------------------------------------------------- 폴링 루프 */
async function refreshVideos() {
  try {
    const r = await fetch("/api/videos");
    render(await r.json());
  } catch { /* 무시 */ }
}

let nextPoll = Date.now() + POLL_MS;
setInterval(() => {
  const s = Math.max(0, Math.round((nextPoll - Date.now()) / 1000));
  $("#countdown").textContent = lastData && lastData.keyword ? `(${s}초 후 갱신)` : "";
}, 1000);

setInterval(() => { nextPoll = Date.now() + POLL_MS; refreshVideos(); }, POLL_MS);
setInterval(refreshDownloads, DL_POLL_MS);

refreshVideos();
refreshDownloads();
