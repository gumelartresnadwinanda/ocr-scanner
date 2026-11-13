const LIST_RENDER_STEP = 150;

const state = {
  scans: [],
  selectedScanId: null,
  currentScan: null,
  videoQuery: "",
  videoFilter: "all", // all | has_text | no_text
  moveFilter: localStorage.getItem("ocr-scanner-move-filter") || "unmoved", // unmoved | moved | all
  listRenderLimit: LIST_RENDER_STEP,
  entryQuery: "",
  entryFilter: "all", // all | handles | text
  showPlayer: false,
  destinationRoot: localStorage.getItem("ocr-scanner-dest-root") || "",
  scanMode: localStorage.getItem("ocr-scanner-scan-mode") === "handle_only" ? "handle_only" : "full_text",
  autoAdvance: localStorage.getItem("ocr-scanner-auto-advance") !== "false",
  movingHandle: null,
  moveFeedback: null,
  jobs: [],
  lastJobSnapshot: "",
  pollTimer: null,
};

function upsertScanSummary(detail) {
  if (!detail || !detail.scan_id) return;
  const idx = state.scans.findIndex((s) => s.scan_id === detail.scan_id);
  const prev = idx >= 0 ? state.scans[idx] : {};
  const previewText = Array.isArray(detail.distinct_texts)
    ? detail.distinct_texts.map((e) => e.text || "").join(" ")
    : (prev.text_preview || "");
  const updated = {
    ...prev,
    scan_id: detail.scan_id,
    video_name: detail.video_name ?? prev.video_name ?? "",
    video_path: detail.video_path ?? prev.video_path ?? "",
    duration_sec: detail.duration_sec ?? prev.duration_sec ?? 0,
    width: detail.width ?? prev.width ?? 0,
    height: detail.height ?? prev.height ?? 0,
    has_text: detail.has_text ?? prev.has_text ?? false,
    handles: detail.handles ?? prev.handles ?? [],
    filename_handle: detail.filename_handle ?? prev.filename_handle ?? null,
    distinct_count: detail.distinct_count ?? prev.distinct_count ?? 0,
    text_preview: previewText,
    scan_mode: detail.scan_mode ?? prev.scan_mode ?? "full_text",
    stopped_early: Boolean(detail.stopped_early ?? prev.stopped_early),
    ocr_frames: detail.ocr_frames ?? prev.ocr_frames ?? 0,
    sampled_frames: detail.sampled_frames ?? prev.sampled_frames ?? 0,
    moved_folder: detail.moved_folder ?? null,
    moved_to: detail.moved_to ?? null,
    mtime: prev.mtime ?? Date.now() / 1000,
  };
  if (idx >= 0) {
    state.scans[idx] = updated;
  } else {
    state.scans.unshift(updated);
  }
}

const els = {
  videoCountBadge: document.getElementById("video-count-badge"),
  videoSearch: document.getElementById("video-search"),
  moveFilterSummary: document.getElementById("move-filter-summary"),
  videoList: document.getElementById("video-list"),
  activeJobs: document.getElementById("active-jobs"),
  mainState: document.getElementById("main-state"),
  errorBanner: document.getElementById("error-banner"),
  errorTitle: document.getElementById("error-title"),
  errorMessage: document.getElementById("error-message"),
  errorDismiss: document.getElementById("error-dismiss"),
  themeToggle: document.getElementById("theme-toggle"),
  themeLabel: document.getElementById("theme-label"),
  themeSun: document.getElementById("theme-icon-sun"),
  themeMoon: document.getElementById("theme-icon-moon"),
  scanDialog: document.getElementById("scan-dialog"),
  scanForm: document.getElementById("scan-form"),
  scanPathInput: document.getElementById("scan-path-input"),
  browseScanFolderBtn: document.getElementById("browse-scan-folder-btn"),
  browseScanFileBtn: document.getElementById("browse-scan-file-btn"),
  scanModeHint: document.getElementById("scan-mode-hint"),
  skipExistingInput: document.getElementById("skip-existing-input"),
  sampleFpsInput: document.getElementById("sample-fps-input"),
  minConfInput: document.getElementById("min-conf-input"),
  recursiveInput: document.getElementById("recursive-input"),
  scanFormError: document.getElementById("scan-form-error"),
  openScanBtn: document.getElementById("open-scan-btn"),
  closeScanBtn: document.getElementById("close-scan-btn"),
  cancelScanBtn: document.getElementById("cancel-scan-btn"),
  submitScanBtn: document.getElementById("submit-scan-btn"),
  lightboxDialog: document.getElementById("lightbox-dialog"),
  lightboxImg: document.getElementById("lightbox-img"),
  lightboxCaption: document.getElementById("lightbox-caption"),
  closeLightboxBtn: document.getElementById("close-lightbox-btn"),
  fsPickerDialog: document.getElementById("fs-picker-dialog"),
  fsPickerTitle: document.getElementById("fs-picker-title"),
  closePickerBtn: document.getElementById("close-picker-btn"),
  pickerUpBtn: document.getElementById("picker-up-btn"),
  pickerPathInput: document.getElementById("picker-path-input"),
  pickerGoBtn: document.getElementById("picker-go-btn"),
  pickerQuickRoots: document.getElementById("picker-quick-roots"),
  pickerBreadcrumbs: document.getElementById("picker-breadcrumbs"),
  pickerSummary: document.getElementById("picker-summary"),
  pickerFilterInput: document.getElementById("picker-filter-input"),
  pickerError: document.getElementById("picker-error"),
  pickerList: document.getElementById("picker-list"),
  pickerSelectedLabel: document.getElementById("picker-selected-label"),
  cancelPickerBtn: document.getElementById("cancel-picker-btn"),
  confirmPickerBtn: document.getElementById("confirm-picker-btn"),
};

const pickerState = {
  mode: "folder",
  targetInput: null,
  roots: { drives: [], places: [], recent: [] },
  currentPath: "",
  parentPath: null,
  breadcrumbs: [],
  folders: [],
  files: [],
  videoCount: 0,
  selectedFilePath: null,
  filterQuery: "",
  renderLimit: 200,
};

function escapeHtml(str) {
  return String(str)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function folderNameFromHandle(handle) {
  return String(handle || "").trim().replace(/^@+/, "");
}

function applyScanMode(mode) {
  state.scanMode = mode === "handle_only" ? "handle_only" : "full_text";
  localStorage.setItem("ocr-scanner-scan-mode", state.scanMode);
  document.querySelectorAll("[data-scan-mode]").forEach((btn) => {
    const active = btn.getAttribute("data-scan-mode") === state.scanMode;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-checked", active ? "true" : "false");
  });
  if (els.scanModeHint) {
    els.scanModeHint.textContent =
      state.scanMode === "handle_only"
        ? "Stops scanning each video as soon as a complete @handle is found. Much faster for sorting by handle."
        : "Scans all sampled frames to extract every distinct text overlay and @handle.";
  }
}

function renderQuickRoots() {
  const chips = [
    `<button type="button" class="picker-root-chip" data-fs-jump="">This PC</button>`,
    ...(pickerState.roots.drives || []).map(
      (d) => `<button type="button" class="picker-root-chip" data-fs-jump="${escapeHtml(d.path)}">${escapeHtml(d.name)}</button>`
    ),
    ...(pickerState.roots.places || []).map(
      (p) => `<button type="button" class="picker-root-chip" data-fs-jump="${escapeHtml(p.path)}">${escapeHtml(p.name)}</button>`
    ),
    ...(pickerState.roots.recent || []).map(
      (r) => `<button type="button" class="picker-root-chip recent" data-fs-jump="${escapeHtml(r.path)}" title="${escapeHtml(r.path)}">Recent: ${escapeHtml(r.name)}</button>`
    ),
  ];
  els.pickerQuickRoots.innerHTML = chips.join("");
  els.pickerQuickRoots.querySelectorAll("[data-fs-jump]").forEach((btn) => {
    btn.addEventListener("click", () => {
      loadFsDirectory(btn.getAttribute("data-fs-jump") || "", false);
    });
  });
}

function updatePickerSelectionUi() {
  if (pickerState.mode === "file") {
    const selected = pickerState.selectedFilePath;
    els.pickerSelectedLabel.textContent = selected || "Click a video file above";
    els.confirmPickerBtn.textContent = "Select file";
    els.confirmPickerBtn.disabled = !selected;
  } else {
    const selected = pickerState.currentPath;
    els.pickerSelectedLabel.textContent = selected || "Open a drive or folder first";
    els.confirmPickerBtn.textContent = "Select this folder";
    els.confirmPickerBtn.disabled = !selected;
  }
}

function renderPickerList() {
  const q = pickerState.filterQuery.trim().toLowerCase();
  const matchedFolders = q
    ? pickerState.folders.filter((f) => f.name.toLowerCase().includes(q))
    : pickerState.folders;
  const matchedFiles =
    pickerState.mode === "file"
      ? q
        ? pickerState.files.filter((f) => f.name.toLowerCase().includes(q))
        : pickerState.files
      : [];

  const combined = [...matchedFolders, ...matchedFiles];
  if (combined.length === 0) {
    els.pickerList.innerHTML = `<div class="picker-empty">${
      q
        ? "No folders or video files match this filter."
        : pickerState.mode === "file"
        ? "No subfolders or video files in this folder."
        : "No subfolders inside this folder."
    }</div>`;
    updatePickerSelectionUi();
    return;
  }

  const visible = combined.slice(0, pickerState.renderLimit);
  const remaining = combined.length - visible.length;

  const folderIcon = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" /></svg>`;
  const videoIcon = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="2" y="4" width="20" height="16" rx="2" /><path d="M10 9l5 3-5 3V9z" /></svg>`;

  const rowsHtml = visible
    .map((item) => {
      if (item.kind === "file") {
        const isSelected = pickerState.selectedFilePath === item.path;
        return `
          <button type="button" class="picker-row ${isSelected ? "active" : ""}" role="option" aria-selected="${isSelected}" data-fs-file="${escapeHtml(item.path)}">
            <span class="picker-row-main">
              ${videoIcon}
              <span class="picker-row-name">${escapeHtml(item.name)}</span>
            </span>
            <span class="picker-row-tag">${escapeHtml(item.ext.toUpperCase().replace(".", ""))}</span>
          </button>
        `;
      }
      const tag = item.kind === "drive" ? "Drive" : "Folder";
      return `
        <button type="button" class="picker-row" role="option" aria-selected="false" data-fs-dir="${escapeHtml(item.path)}">
          <span class="picker-row-main">
            ${folderIcon}
            <span class="picker-row-name">${escapeHtml(item.name)}</span>
          </span>
          <span class="picker-row-tag">${tag}</span>
        </button>
      `;
    })
    .join("");

  const moreBtnHtml =
    remaining > 0
      ? `<button type="button" id="picker-show-more-btn" class="btn btn-secondary">Show ${Math.min(remaining, 200)} more (${remaining} remaining)</button>`
      : "";

  els.pickerList.innerHTML = rowsHtml + moreBtnHtml;

  els.pickerList.querySelectorAll("[data-fs-dir]").forEach((btn) => {
    btn.addEventListener("click", () => {
      loadFsDirectory(btn.getAttribute("data-fs-dir") || "", false);
    });
  });

  els.pickerList.querySelectorAll("[data-fs-file]").forEach((btn) => {
    btn.addEventListener("click", () => {
      pickerState.selectedFilePath = btn.getAttribute("data-fs-file");
      renderPickerList();
    });
    btn.addEventListener("dblclick", () => {
      pickerState.selectedFilePath = btn.getAttribute("data-fs-file");
      confirmFsPickerSelection();
    });
  });

  document.getElementById("picker-show-more-btn")?.addEventListener("click", () => {
    pickerState.renderLimit += 200;
    renderPickerList();
  });

  updatePickerSelectionUi();
}

function renderFsPicker() {
  els.pickerPathInput.value = pickerState.currentPath;
  els.pickerUpBtn.disabled = pickerState.parentPath === null;

  const crumbs = pickerState.breadcrumbs || [{ name: "This PC", path: "" }];
  els.pickerBreadcrumbs.innerHTML = crumbs
    .map((c, idx) => {
      const btn = `<button type="button" class="picker-crumb-btn" data-fs-crumb="${escapeHtml(c.path)}">${escapeHtml(c.name)}</button>`;
      return idx < crumbs.length - 1 ? `${btn}<span class="picker-crumb-sep">/</span>` : btn;
    })
    .join("");

  els.pickerBreadcrumbs.querySelectorAll("[data-fs-crumb]").forEach((btn) => {
    btn.addEventListener("click", () => {
      loadFsDirectory(btn.getAttribute("data-fs-crumb") || "", false);
    });
  });

  if (!pickerState.currentPath) {
    els.pickerSummary.textContent = `${pickerState.folders.length} drives available`;
  } else {
    els.pickerSummary.textContent = `${pickerState.folders.length} folders | ${pickerState.videoCount} video files`;
  }

  renderPickerList();
}

async function loadFsDirectory(path, fallback) {
  els.pickerError.classList.add("hidden");
  els.pickerError.textContent = "";
  els.pickerList.innerHTML = `<div class="picker-empty">Loading folder contents...</div>`;

  const params = new URLSearchParams({
    mode: pickerState.mode,
    fallback: fallback ? "true" : "false",
  });
  if (path) {
    params.set("path", path);
  }

  try {
    const res = await fetch(`/api/fs/list?${params.toString()}`);
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }
    pickerState.currentPath = data.path || "";
    pickerState.parentPath = data.parent === undefined ? null : data.parent;
    pickerState.breadcrumbs = data.breadcrumbs || [];
    pickerState.folders = data.folders || [];
    pickerState.files = data.files || [];
    pickerState.videoCount = Number(data.video_count || 0);
    pickerState.selectedFilePath = null;
    pickerState.filterQuery = "";
    pickerState.renderLimit = 200;
    els.pickerFilterInput.value = "";
    renderFsPicker();
  } catch (err) {
    els.pickerError.textContent = String(err.message || err);
    els.pickerError.classList.remove("hidden");
    renderPickerList();
  }
}

function confirmFsPickerSelection() {
  const chosen = pickerState.mode === "file" ? pickerState.selectedFilePath : pickerState.currentPath;
  if (!chosen || !pickerState.targetInput) return;
  pickerState.targetInput.value = chosen;
  pickerState.targetInput.dispatchEvent(new Event("input", { bubbles: true }));
  els.fsPickerDialog.close();
  pickerState.targetInput.focus();
}

async function openFsPicker(mode, targetInput, options = {}) {
  pickerState.mode = mode;
  pickerState.targetInput = targetInput;
  pickerState.selectedFilePath = null;
  pickerState.filterQuery = "";
  pickerState.renderLimit = 200;

  els.fsPickerTitle.textContent = options.title || (mode === "file" ? "Select a video file" : "Select a folder");
  els.pickerFilterInput.value = "";
  els.pickerError.classList.add("hidden");
  els.pickerList.innerHTML = `<div class="picker-empty">Loading locations...</div>`;
  updatePickerSelectionUi();

  if (!els.fsPickerDialog.open) {
    els.fsPickerDialog.showModal();
  }

  try {
    const rootsRes = await fetch("/api/fs/roots");
    if (rootsRes.ok) {
      pickerState.roots = await rootsRes.json();
      renderQuickRoots();
    }
  } catch {
    // Non-fatal; directory listing still works
  }

  const startPath =
    (targetInput && targetInput.value.trim()) ||
    options.fallbackDir ||
    state.currentScan?.source_dir ||
    "";
  await loadFsDirectory(startPath, true);
}

function formatTs(sec) {
  const num = Number(sec || 0);
  if (num >= 60) {
    const mins = Math.floor(num / 60);
    const rem = (num - mins * 60).toFixed(2).padStart(5, "0");
    return `${String(mins).padStart(2, "0")}:${rem}`;
  }
  return `${num.toFixed(2)}s`;
}

function showError(title, message) {
  els.errorTitle.textContent = title;
  els.errorMessage.textContent = message;
  els.errorBanner.classList.remove("hidden");
}

function hideError() {
  els.errorBanner.classList.add("hidden");
}

function applyTheme(theme) {
  const isDark = theme === "dark";
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem("ocr-scanner-theme", theme);
  els.themeToggle.setAttribute("aria-pressed", String(isDark));
  els.themeLabel.textContent = isDark ? "Light mode" : "Dark mode";
  els.themeSun.classList.toggle("icon-hidden", !isDark);
  els.themeMoon.classList.toggle("icon-hidden", isDark);
}

function initTheme() {
  const saved = localStorage.getItem("ocr-scanner-theme");
  applyTheme(saved === "dark" ? "dark" : "light");
}

function applyMoveFilter(filterVal) {
  const valid = filterVal === "moved" || filterVal === "all" ? filterVal : "unmoved";
  state.moveFilter = valid;
  localStorage.setItem("ocr-scanner-move-filter", valid);
  document.querySelectorAll("[data-move-filter]").forEach((btn) => {
    const active = btn.getAttribute("data-move-filter") === valid;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function getFilteredScans() {
  const q = state.videoQuery.trim().toLowerCase();
  return state.scans.filter((item) => {
    if (state.videoFilter === "has_text" && !item.has_text) return false;
    if (state.videoFilter === "no_text" && item.has_text) return false;
    if (state.moveFilter === "unmoved" && item.moved_folder) return false;
    if (state.moveFilter === "moved" && !item.moved_folder) return false;
    if (!q) return true;
    const inName = item.video_name.toLowerCase().includes(q);
    const inHandles = (item.handles || []).some((h) => h.toLowerCase().includes(q));
    const inFilenameHandle = (item.filename_handle || "").toLowerCase().includes(q);
    const inMovedFolder = (item.moved_folder || "").toLowerCase().includes(q);
    const inText = (item.text_preview || "").toLowerCase().includes(q);
    return inName || inHandles || inFilenameHandle || inMovedFolder || inText;
  });
}

function renderVideoList() {
  const filtered = getFilteredScans();
  const movedCount = state.scans.filter((s) => Boolean(s.moved_folder)).length;
  const unmovedCount = state.scans.length - movedCount;

  els.videoCountBadge.textContent =
    filtered.length === state.scans.length
      ? String(state.scans.length)
      : `${filtered.length} / ${state.scans.length}`;
  if (els.moveFilterSummary) {
    els.moveFilterSummary.textContent =
      state.scans.length > 0 ? `${unmovedCount} unmoved | ${movedCount} moved` : "";
  }

  if (state.scans.length === 0) {
    els.videoList.innerHTML = `<div class="job-card">No scans saved yet. Use "Scan video or folder" to start.</div>`;
    return;
  }

  if (filtered.length === 0) {
    const emptyMsg =
      state.moveFilter === "unmoved" && unmovedCount === 0
        ? "All scanned videos have been moved."
        : state.moveFilter === "moved" && movedCount === 0
        ? "No moved videos yet."
        : "No videos match the current filter.";
    els.videoList.innerHTML = `<div class="job-card">${emptyMsg}</div>`;
    return;
  }

  const prevScrollTop = els.videoList.scrollTop;
  const selectedIdx = state.selectedScanId
    ? filtered.findIndex((s) => s.scan_id === state.selectedScanId)
    : -1;
  const visibleLimit = Math.max(
    state.listRenderLimit,
    selectedIdx >= 0 ? selectedIdx + 20 : 0
  );
  const visibleScans = filtered.slice(0, visibleLimit);
  const remainingCount = Math.max(0, filtered.length - visibleScans.length);
  const moreBtnHtml = remainingCount > 0
    ? `<button type="button" id="video-list-more-btn" class="btn btn-secondary list-more-btn">Show more (${Math.min(LIST_RENDER_STEP, remainingCount)} of ${remainingCount} remaining)</button>`
    : "";

  els.videoList.innerHTML =
    visibleScans
      .map((scan) => {
      const isActive = scan.scan_id === state.selectedScanId;
      const badgeClass = scan.has_text ? "has-text" : "no-text";
      const badgeLabel = scan.has_text ? `${scan.distinct_count} distinct` : "No text";
      const modeTag =
        scan.scan_mode === "handle_only"
          ? `<span class="status-tag mode-partial">Handle-only</span>`
          : "";
      const movedTag = scan.moved_folder
        ? `<span class="status-tag moved">Moved: ${escapeHtml(scan.moved_folder)}</span>`
        : "";
      const handleSummary =
        scan.handles && scan.handles.length > 0
          ? scan.handles.join(", ")
          : scan.filename_handle
          ? `Name: ${scan.filename_handle}`
          : `${scan.duration_sec.toFixed(1)}s`;
      return `
        <button type="button" class="video-item ${isActive ? "active" : ""}" data-scan-id="${escapeHtml(scan.scan_id)}">
          <span class="video-item-name">${escapeHtml(scan.video_name)}</span>
          <span class="video-item-meta">
            <span>${escapeHtml(handleSummary)}</span>
            <span>${modeTag} ${movedTag} <span class="status-tag ${badgeClass}">${escapeHtml(badgeLabel)}</span></span>
          </span>
        </button>
      `;
      })
      .join("") + moreBtnHtml;
  els.videoList.scrollTop = prevScrollTop;
}

function renderJobs() {
  const visibleJobs = state.jobs.filter((j) => j.status === "queued" || j.status === "running" || j.errors.length > 0);
  if (visibleJobs.length === 0) {
    els.activeJobs.innerHTML = "";
    return;
  }

  els.activeJobs.innerHTML = visibleJobs
    .map((job) => {
      const pct = job.total_count > 0 ? Math.round((job.done_count / job.total_count) * 100) : 0;
      const isActive = job.status === "queued" || job.status === "running";
      const statusLine =
        job.status === "running"
          ? `Scanning ${job.done_count + 1 > job.total_count ? job.total_count : job.done_count + 1}/${job.total_count}: ${job.current_video || "..."}`
          : job.status === "queued"
          ? `Queued (${job.total_count} video${job.total_count === 1 ? "" : "s"})`
          : job.status === "cancelled"
          ? `Cancelled (${job.done_count}/${job.total_count})`
          : `Finished with ${job.errors.length} error(s)`;
      const modeText = job.scan_mode === "handle_only" ? "Stop at @handle" : "Full text";
      const countsLine = `Scanned: ${job.scanned_count || 0} | Skipped: ${job.skipped_count || 0} | ${modeText}`;
      const errHtml = job.errors.length
        ? `<div class="field-hint">${escapeHtml(job.errors[0])}</div>`
        : "";
      const cancelBtn = isActive
        ? `<button type="button" class="btn btn-secondary btn-compact" data-cancel-job="${escapeHtml(job.job_id)}">${job.cancel_requested ? "Stopping..." : "Cancel"}</button>`
        : "";
      return `
        <div class="job-card">
          <div class="job-head">
            <div>
              <div class="job-title">${escapeHtml(statusLine)}</div>
              <div class="field-hint">${escapeHtml(countsLine)}</div>
            </div>
            ${cancelBtn}
          </div>
          ${errHtml}
          <div class="job-progress-bar">
            <div class="job-progress-fill" style="width: ${pct}%"></div>
          </div>
        </div>
      `;
    })
    .join("");

  els.activeJobs.querySelectorAll("[data-cancel-job]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const jobId = btn.getAttribute("data-cancel-job");
      btn.disabled = true;
      await fetch(`/api/jobs/${encodeURIComponent(jobId)}/cancel`, { method: "POST" });
      await pollJobs();
    });
  });
}

function getFilteredEntries(scan) {
  const q = state.entryQuery.trim().toLowerCase();
  return (scan.distinct_texts || []).filter((entry) => {
    if (state.entryFilter === "handles" && !entry.is_handle && !(entry.handles || []).length) return false;
    if (state.entryFilter === "text" && entry.is_handle) return false;
    if (!q) return true;
    return entry.text.toLowerCase().includes(q);
  });
}

function findNextUnmovedScanId(currentScanId, orderedScans) {
  if (!orderedScans || orderedScans.length === 0) return null;
  const currentIdx = orderedScans.findIndex((s) => s.scan_id === currentScanId);
  if (currentIdx >= 0) {
    for (let i = currentIdx + 1; i < orderedScans.length; i += 1) {
      if (orderedScans[i].scan_id !== currentScanId && !orderedScans[i].moved_folder) {
        return orderedScans[i].scan_id;
      }
    }
    for (let i = 0; i < currentIdx; i += 1) {
      if (orderedScans[i].scan_id !== currentScanId && !orderedScans[i].moved_folder) {
        return orderedScans[i].scan_id;
      }
    }
    return null;
  }
  const firstUnmoved = orderedScans.find((s) => s.scan_id !== currentScanId && !s.moved_folder);
  return firstUnmoved ? firstUnmoved.scan_id : null;
}

function getAdjacentScanIds() {
  const list = getFilteredScans();
  const idx = list.findIndex((s) => s.scan_id === state.selectedScanId);
  return {
    index: idx,
    total: list.length,
    prevId: idx > 0 ? list[idx - 1].scan_id : null,
    nextId: idx >= 0 && idx < list.length - 1 ? list[idx + 1].scan_id : null,
  };
}

function renderMainContent() {
  if (!state.selectedScanId) {
    if (state.scans.length === 0) {
      els.mainState.innerHTML = `
        <section class="state-box">
          <h2>No scanned videos yet</h2>
          <p>Provide a directory path to scan videos one by one, then inspect distinct text and move files into @handle folders.</p>
          <button type="button" class="btn btn-primary" id="empty-scan-cta">Scan video or folder</button>
        </section>
      `;
      document.getElementById("empty-scan-cta")?.addEventListener("click", openScanDialog);
    } else if (getFilteredScans().length === 0 && state.moveFilter === "unmoved") {
      els.mainState.innerHTML = `
        <section class="state-box">
          <h2>All scanned videos are moved</h2>
          <p>Every video in this library is already organized into a folder. Switch to Moved or All to inspect them.</p>
          <div class="summary-actions" style="justify-content: center;">
            <button type="button" class="btn btn-primary" data-quick-move-filter="moved">Show moved videos</button>
            <button type="button" class="btn btn-secondary" data-quick-move-filter="all">Show all</button>
          </div>
        </section>
      `;
      els.mainState.querySelectorAll("[data-quick-move-filter]").forEach((btn) => {
        btn.addEventListener("click", () => {
          applyMoveFilter(btn.getAttribute("data-quick-move-filter"));
          handleSidebarFilterUpdate();
        });
      });
    } else {
      els.mainState.innerHTML = `
        <section class="state-box">
          <h2>Select a video from the list</h2>
          <p>Choose a scanned video on the left to inspect its extracted text and frame examples.</p>
        </section>
      `;
    }
    return;
  }

  if (!state.currentScan) {
    els.mainState.innerHTML = `
      <section class="state-box">
        <h2>Loading scan results</h2>
        <p>Fetching distinct text entries and example frames.</p>
      </section>
    `;
    return;
  }

  const scan = state.currentScan;
  const entries = scan.distinct_texts || [];
  const filteredEntries = getFilteredEntries(scan);
  const handleEntriesCount = entries.filter((e) => e.is_handle || (e.handles && e.handles.length > 0)).length;
  const textOnlyCount = entries.filter((e) => !e.is_handle).length;
  const nav = getAdjacentScanIds();

  const handlesHtml =
    scan.handles && scan.handles.length > 0
      ? scan.handles
          .map((h) => {
            const active = state.entryQuery.toLowerCase() === h.toLowerCase();
            return `<button type="button" class="chip-btn ${active ? "active" : ""}" data-handle-chip="${escapeHtml(h)}">${escapeHtml(h)}</button>`;
          })
          .join("")
      : `<span class="field-hint">No @handles detected</span>`;

  const videoToggleBtn = scan.video_url
    ? `<button type="button" id="toggle-player-btn" class="btn btn-secondary" aria-expanded="${state.showPlayer}">${state.showPlayer ? "Hide video" : "Play video"}</button>`
    : "";

  const pagerHtml =
    nav.total > 0 && nav.index >= 0
      ? `
        <div class="nav-pager" role="group" aria-label="Step through videos">
          <button type="button" id="prev-video-btn" class="btn btn-secondary" ${nav.prevId ? "" : "disabled"}>Prev</button>
          <span class="nav-pos">${nav.index + 1} / ${nav.total}</span>
          <button type="button" id="next-video-btn" class="btn btn-secondary" ${nav.nextId ? "" : "disabled"}>Next</button>
        </div>
      `
      : "";

  const moveButtonsHtml =
    scan.handles && scan.handles.length > 0
      ? scan.handles
          .map((h) => {
            const folderName = folderNameFromHandle(h);
            if (!folderName) return "";
            const isMoving = state.movingHandle === h;
            const alreadyMovedHere = Boolean(
              scan.moved_folder && scan.moved_folder.toLowerCase() === folderName.toLowerCase()
            );
            const label = isMoving
              ? `Moving to ${folderName}...`
              : alreadyMovedHere
              ? `In ${folderName}`
              : `Move to ${folderName}`;
            return `<button type="button" class="btn btn-move" data-move-handle="${escapeHtml(h)}" ${state.movingHandle || !scan.video_exists ? "disabled" : ""}>${escapeHtml(label)}</button>`;
          })
          .join("")
      : "";

  const ocrFolderSet = new Set(
    (scan.handles || []).map((h) => folderNameFromHandle(h).toLowerCase()).filter(Boolean)
  );
  const fnSuggestion =
    scan.filename_handle && !ocrFolderSet.has(scan.filename_handle.toLowerCase())
      ? scan.filename_handle
      : null;
  const fnMoveButtonHtml = fnSuggestion
    ? (() => {
        const isMovingFn = state.movingHandle === fnSuggestion;
        const alreadyMovedFn = Boolean(
          scan.moved_folder && scan.moved_folder.toLowerCase() === fnSuggestion.toLowerCase()
        );
        const label = isMovingFn
          ? `Moving to ${fnSuggestion}...`
          : alreadyMovedFn
          ? `In ${fnSuggestion} (from filename)`
          : `Move to ${fnSuggestion} (from filename)`;
        return `<button type="button" class="btn btn-move-suggestion" data-move-handle="${escapeHtml(fnSuggestion)}" ${state.movingHandle || !scan.video_exists ? "disabled" : ""}>${escapeHtml(label)}</button>`;
      })()
    : "";
  const allMoveButtonsHtml = `${moveButtonsHtml}${fnMoveButtonHtml}`;

  let moveStatusBanner = "";
  if (state.moveFeedback) {
    const fb = state.moveFeedback;
    if (fb.scanId === scan.scan_id) {
      const prefix = fb.alreadyThere
        ? "File is already in target folder:"
        : fb.exhausted
        ? "Moved file to folder (no more unmoved videos in this view):"
        : "Moved file to folder:";
      moveStatusBanner = `
        <div class="move-status" role="status">
          <div>
            <strong>${prefix}</strong>
            <div class="move-status-path">${escapeHtml(fb.movedTo)}</div>
          </div>
          ${!fb.exhausted && (fb.nextScanId || nav.nextId) ? `<button type="button" id="move-next-btn" class="btn btn-secondary btn-compact">Next video</button>` : ""}
        </div>
      `;
    } else {
      moveStatusBanner = `
        <div class="move-status" role="status">
          <div>
            <strong>Moved ${escapeHtml(fb.videoName)} to ${escapeHtml(fb.folderName)}:</strong>
            <div class="move-status-path">${escapeHtml(fb.movedTo)}</div>
          </div>
          <button type="button" id="view-last-moved-btn" class="btn btn-secondary btn-compact" data-jump-scan="${escapeHtml(fb.scanId)}">View moved</button>
        </div>
      `;
    }
  } else if (scan.moved_to) {
    moveStatusBanner = `
      <div class="move-status">
        <div>
          <strong>Current location (${escapeHtml(scan.moved_folder || "")}):</strong>
          <div class="move-status-path">${escapeHtml(scan.moved_to)}</div>
        </div>
      </div>
    `;
  }

  const moveBarHtml = `
    <div class="move-bar">
      <div class="move-actions">
        <span class="handles-label">Organize file:</span>
        ${allMoveButtonsHtml || `<span class="field-hint">${scan.video_exists ? "No @handle or filename suggestion; use custom folder below." : "Source video file missing on disk."}</span>`}
      </div>
      <div class="move-toolbar-row">
        <label class="inline-check">
          <input id="auto-advance-checkbox" type="checkbox" ${state.autoAdvance ? "checked" : ""} />
          <span>Auto-advance to next unmoved video after move</span>
        </label>
      </div>
      ${moveStatusBanner}
      <details class="move-settings">
        <summary>Destination folder options</summary>
        <div class="move-settings-body">
          <div class="form-group">
            <label for="dest-root-input">Base destination directory (optional)</label>
            <div class="path-input-row">
              <input id="dest-root-input" type="text" value="${escapeHtml(state.destinationRoot)}" placeholder="Leave empty to create handle folder inside video's folder" />
              <button type="button" id="browse-dest-root-btn" class="btn btn-secondary">Browse folder</button>
            </div>
          </div>
          <div class="form-group">
            <label for="custom-folder-input">Or move to custom folder name</label>
            <div class="path-input-row">
              <input id="custom-folder-input" type="text" placeholder="rose, naruto, or custom folder" />
              <button type="button" id="move-custom-btn" class="btn btn-secondary" ${state.movingHandle || !scan.video_exists ? "disabled" : ""}>Move to folder</button>
            </div>
          </div>
        </div>
      </details>
    </div>
  `;

  const playerHtml =
    state.showPlayer && scan.video_url
      ? `
        <div class="player-panel">
          <video id="video-player" controls preload="metadata" src="${escapeHtml(scan.video_url)}"></video>
        </div>
      `
      : "";

  let entriesHtml = "";
  if (!scan.has_text || entries.length === 0) {
    entriesHtml = `
      <section class="state-box">
        <h3>No text detected on screen</h3>
        <p>Sampled ${scan.sampled_frames} frames (${scan.ocr_frames} OCR'd) with no text meeting confidence threshold.</p>
      </section>
    `;
  } else if (filteredEntries.length === 0) {
    entriesHtml = `
      <section class="state-box">
        <h3>No matching text entries</h3>
        <p>Try clearing the text search or switching the filter tab.</p>
        <button type="button" id="reset-entry-filters-btn" class="btn btn-secondary">Clear filters</button>
      </section>
    `;
  } else {
    entriesHtml = `
      <div class="entries-grid">
        ${filteredEntries
          .map((entry) => {
            const badgeClass = entry.is_handle ? "handle" : "text";
            const badgeLabel = entry.is_handle ? "@HANDLE" : "TEXT";
            const seenSpan = `${formatTs(entry.first_seen_sec)} - ${formatTs(entry.last_seen_sec)} (${entry.occurrences}x)`;
            const boxedUrl = escapeHtml(entry.boxed_frame_url);
            const cleanUrl = escapeHtml(entry.clean_frame_url);
            const canSeek = Boolean(scan.video_url);
            return `
              <article class="entry-card ${entry.is_handle ? "is-handle" : ""}" data-entry-id="${entry.id}">
                <div class="entry-head">
                  <div class="entry-badges">
                    <span class="entry-num">#${entry.id}</span>
                    <span class="type-badge ${badgeClass}">${badgeLabel}</span>
                  </div>
                  <span class="entry-meta">Conf ${(entry.confidence * 100).toFixed(1)}% | ${escapeHtml(seenSpan)}</span>
                </div>

                <div class="ocr-box">
                  <pre class="ocr-pre">${escapeHtml(entry.text)}</pre>
                  <button type="button" class="btn btn-secondary copy-btn" data-copy-text="${escapeHtml(entry.text)}">Copy</button>
                </div>

                <div class="entry-controls">
                  <div class="frame-toggle" role="group" aria-label="Frame overlay mode for entry ${entry.id}">
                    <button type="button" class="seg-btn active" data-frame-mode="boxed" data-src="${boxedUrl}">Boxed</button>
                    <button type="button" class="seg-btn" data-frame-mode="clean" data-src="${cleanUrl}">Clean</button>
                  </div>

                  ${
                    canSeek
                      ? `<button type="button" class="ts-btn" data-seek-sec="${entry.best_timestamp_sec}">Jump to ${formatTs(entry.best_timestamp_sec)}</button>`
                      : `<span class="entry-meta">Frame @ ${formatTs(entry.best_timestamp_sec)}</span>`
                  }
                </div>

                <button type="button" class="frame-preview-btn" data-lightbox-src="${boxedUrl}" data-lightbox-title="#${entry.id} (${formatTs(entry.best_timestamp_sec)}): ${escapeHtml(entry.text.replaceAll('\n', ' '))}">
                  <img src="${boxedUrl}" alt="Frame example at ${formatTs(entry.best_timestamp_sec)} for ${escapeHtml(entry.text)}" loading="lazy" />
                </button>
              </article>
            `;
          })
          .join("")}
      </div>
    `;
  }

  els.mainState.innerHTML = `
    <section class="summary-card">
      <div class="summary-top">
        <div>
          <h2 class="video-heading">${escapeHtml(scan.video_name)}</h2>
          <p class="meta-line">${scan.scan_mode === "handle_only" ? (scan.stopped_early ? "Handle-only (stopped early)" : "Handle-only") : "Full text"} | ${scan.width}x${scan.height} | ${scan.duration_sec.toFixed(2)}s | ${scan.fps.toFixed(1)} fps | ${scan.ocr_frames}/${scan.sampled_frames} frames OCR'd (${scan.skipped_frames} skipped) in ${scan.elapsed_sec.toFixed(2)}s</p>
        </div>
        <div class="summary-actions">
          ${scan.scan_mode === "handle_only" && scan.video_exists ? '<button type="button" id="rescan-full-btn" class="btn btn-secondary">Scan full text</button>' : ""}
          ${pagerHtml}
          ${videoToggleBtn}
        </div>
      </div>

      <div class="handles-row">
        <span class="handles-label">@Handles found:</span>
        ${handlesHtml}
      </div>

      ${moveBarHtml}
      ${playerHtml}
    </section>

    ${
      entries.length > 0
        ? `
      <div class="toolbar">
        <label class="sr-only" for="entry-search">Filter extracted text</label>
        <div class="search-field">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <circle cx="11" cy="11" r="8" />
            <path d="M21 21l-4.35-4.35" />
          </svg>
          <input id="entry-search" type="search" value="${escapeHtml(state.entryQuery)}" placeholder="Filter text or @handle inside this video" />
        </div>

        <div class="filter-tabs" role="group" aria-label="Filter text entries">
          <button type="button" class="seg-btn ${state.entryFilter === "all" ? "active" : ""}" data-entry-filter="all">All (${entries.length})</button>
          <button type="button" class="seg-btn ${state.entryFilter === "handles" ? "active" : ""}" data-entry-filter="handles">@Handles (${handleEntriesCount})</button>
          <button type="button" class="seg-btn ${state.entryFilter === "text" ? "active" : ""}" data-entry-filter="text">Other text (${textOnlyCount})</button>
        </div>
      </div>
    `
        : ""
    }

    ${entriesHtml}
  `;

  bindMainContentEvents(nav);
}

async function moveVideoToHandle(scanId, handleName) {
  const trimmed = String(handleName || "").trim();
  if (!trimmed) {
    showError("Missing folder name", "Enter a target @handle or folder name before moving.");
    return;
  }

  const preMoveFiltered = getFilteredScans();
  const sourceVideoName = state.currentScan?.video_name || scanId;

  // Release any active browser video stream handle so Windows does not lock the file
  const activeVideo = document.getElementById("video-player");
  if (activeVideo) {
    activeVideo.pause();
    activeVideo.removeAttribute("src");
    activeVideo.load();
  }
  state.showPlayer = false;
  state.movingHandle = trimmed;
  hideError();
  renderMainContent();

  try {
    const res = await fetch(`/api/scans/${encodeURIComponent(scanId)}/move`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        handle: trimmed,
        destination_root: state.destinationRoot.trim() || null,
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      throw new Error(data.detail || `HTTP ${res.status}`);
    }

    state.currentScan = data.scan;
    const manualNextId = findNextUnmovedScanId(scanId, preMoveFiltered);
    const shouldAutoAdvance = state.autoAdvance && !data.already_there;
    const nextTargetId = shouldAutoAdvance ? manualNextId : null;

    state.moveFeedback = {
      scanId,
      videoName: data.scan?.video_name || sourceVideoName,
      folderName: data.folder_name || folderNameFromHandle(trimmed),
      movedTo: data.moved_to,
      alreadyThere: Boolean(data.already_there),
      exhausted: Boolean(shouldAutoAdvance && !nextTargetId),
      nextScanId: manualNextId,
    };
    if (data.scan) {
      upsertScanSummary(data.scan);
    } else {
      await fetchScans(false);
    }
    if (nextTargetId && state.scans.some((s) => s.scan_id === nextTargetId)) {
      state.movingHandle = null;
      await selectScan(nextTargetId, { preserveFeedback: true });
      return;
    }
    if (data.scan) {
      renderVideoList();
    }
  } catch (err) {
    showError("Could not move file", String(err.message || err));
  } finally {
    state.movingHandle = null;
    renderVideoList();
    renderMainContent();
  }
}

function bindMainContentEvents(nav) {
  document.getElementById("prev-video-btn")?.addEventListener("click", () => {
    if (nav.prevId) selectScan(nav.prevId);
  });

  document.getElementById("next-video-btn")?.addEventListener("click", () => {
    if (nav.nextId) selectScan(nav.nextId);
  });

  document.getElementById("move-next-btn")?.addEventListener("click", () => {
    const targetNext = nav.nextId || state.moveFeedback?.nextScanId;
    if (targetNext) selectScan(targetNext);
  });

  document.getElementById("view-last-moved-btn")?.addEventListener("click", () => {
    const jumpId = document.getElementById("view-last-moved-btn")?.getAttribute("data-jump-scan");
    if (jumpId) selectScan(jumpId);
  });

  document.getElementById("auto-advance-checkbox")?.addEventListener("change", (event) => {
    state.autoAdvance = Boolean(event.target.checked);
    localStorage.setItem("ocr-scanner-auto-advance", state.autoAdvance ? "true" : "false");
  });

  document.getElementById("dest-root-input")?.addEventListener("input", (event) => {
    state.destinationRoot = event.target.value;
    localStorage.setItem("ocr-scanner-dest-root", state.destinationRoot);
  });

  document.getElementById("browse-dest-root-btn")?.addEventListener("click", () => {
    const destInput = document.getElementById("dest-root-input");
    openFsPicker("folder", destInput, { title: "Choose base destination folder" });
  });

  document.getElementById("custom-folder-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      const folderVal = event.target.value || "";
      if (state.currentScan) {
        moveVideoToHandle(state.currentScan.scan_id, folderVal);
      }
    }
  });

  document.getElementById("move-custom-btn")?.addEventListener("click", () => {
    const folderVal = document.getElementById("custom-folder-input")?.value || "";
    if (state.currentScan) {
      moveVideoToHandle(state.currentScan.scan_id, folderVal);
    }
  });

  els.mainState.querySelectorAll("[data-move-handle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (state.currentScan) {
        moveVideoToHandle(state.currentScan.scan_id, btn.getAttribute("data-move-handle"));
      }
    });
  });

  document.getElementById("rescan-full-btn")?.addEventListener("click", async () => {
    const scan = state.currentScan;
    if (!scan) return;
    const btn = document.getElementById("rescan-full-btn");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Starting...";
    }
    try {
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: scan.video_path,
          sample_fps: Number(els.sampleFpsInput?.value || 2.0),
          min_confidence: Number(els.minConfInput?.value || 0.55),
          recursive: false,
          skip_existing: false,
          scan_mode: "full_text",
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      await pollJobs();
      if (!state.pollTimer) {
        state.pollTimer = setInterval(pollJobs, 1000);
      }
    } catch (err) {
      showError("Could not start full text scan", String(err.message || err));
      if (btn) {
        btn.disabled = false;
        btn.textContent = "Scan full text";
      }
    }
  });

  document.getElementById("toggle-player-btn")?.addEventListener("click", () => {
    state.showPlayer = !state.showPlayer;
    renderMainContent();
  });

  document.getElementById("entry-search")?.addEventListener("input", (event) => {
    state.entryQuery = event.target.value;
    const caret = event.target.selectionStart;
    renderMainContent();
    const input = document.getElementById("entry-search");
    if (input) {
      input.focus();
      input.setSelectionRange(caret, caret);
    }
  });

  document.getElementById("reset-entry-filters-btn")?.addEventListener("click", () => {
    state.entryQuery = "";
    state.entryFilter = "all";
    renderMainContent();
  });

  els.mainState.querySelectorAll("[data-entry-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.entryFilter = btn.getAttribute("data-entry-filter");
      renderMainContent();
    });
  });

  els.mainState.querySelectorAll("[data-handle-chip]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const handle = btn.getAttribute("data-handle-chip");
      state.entryQuery = state.entryQuery.toLowerCase() === handle.toLowerCase() ? "" : handle;
      renderMainContent();
    });
  });

  els.mainState.querySelectorAll(".copy-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const text = btn.getAttribute("data-copy-text") || "";
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = "Copied";
        setTimeout(() => {
          btn.textContent = "Copy";
        }, 1500);
      } catch {
        showError("Copy failed", "Browser clipboard access was blocked.");
      }
    });
  });

  els.mainState.querySelectorAll(".frame-toggle .seg-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const card = btn.closest(".entry-card");
      const src = btn.getAttribute("data-src");
      card.querySelectorAll(".frame-toggle .seg-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      const previewBtn = card.querySelector(".frame-preview-btn");
      const img = previewBtn.querySelector("img");
      img.src = src;
      previewBtn.setAttribute("data-lightbox-src", src);
    });
  });

  els.mainState.querySelectorAll(".ts-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const sec = Number(btn.getAttribute("data-seek-sec") || 0);
      if (!state.showPlayer) {
        state.showPlayer = true;
        renderMainContent();
      }
      const video = document.getElementById("video-player");
      if (video) {
        const seekAndPlay = () => {
          video.currentTime = sec;
          video.play().catch(() => {});
        };
        if (video.readyState >= 1) {
          seekAndPlay();
        } else {
          video.addEventListener("loadedmetadata", seekAndPlay, { once: true });
        }
        video.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    });
  });

  els.mainState.querySelectorAll(".frame-preview-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      els.lightboxImg.src = btn.getAttribute("data-lightbox-src");
      els.lightboxCaption.textContent = btn.getAttribute("data-lightbox-title") || "Frame preview";
      els.lightboxDialog.showModal();
    });
  });
}

async function handleSidebarFilterUpdate() {
  const filtered = getFilteredScans();
  renderVideoList();
  const stillVisible = filtered.some((s) => s.scan_id === state.selectedScanId);
  if (!stillVisible) {
    if (filtered.length > 0) {
      await selectScan(filtered[0].scan_id);
    } else {
      state.selectedScanId = null;
      state.currentScan = null;
      state.moveFeedback = null;
      renderMainContent();
    }
  } else {
    renderMainContent();
  }
}

async function fetchScans(selectIfEmpty = true) {
  try {
    const res = await fetch("/api/scans");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    state.scans = data.scans || [];
    const filtered = getFilteredScans();
    if (selectIfEmpty && !state.selectedScanId && filtered.length > 0) {
      await selectScan(filtered[0].scan_id);
    } else {
      renderVideoList();
      renderMainContent();
    }
  } catch (err) {
    showError("Failed to fetch scans", String(err));
  }
}

async function selectScan(scanId, options = {}) {
  state.selectedScanId = scanId;
  state.currentScan = null;
  state.entryQuery = "";
  state.entryFilter = "all";
  state.showPlayer = false;
  if (!options.preserveFeedback) {
    state.moveFeedback = null;
  }
  renderVideoList();
  renderMainContent();
  try {
    const res = await fetch(`/api/scans/${encodeURIComponent(scanId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.currentScan = await res.json();
    renderMainContent();
  } catch (err) {
    showError("Could not load scan detail", String(err));
  }
}

async function pollJobs() {
  try {
    const res = await fetch("/api/jobs");
    if (!res.ok) return;
    const data = await res.json();
    const prevRunning = state.jobs.some((j) => j.status === "queued" || j.status === "running");
    state.jobs = data.jobs || [];
    renderJobs();

    const snapshot = state.jobs.map((j) => `${j.job_id}:${j.status}:${j.done_count}`).join("|");
    const progressChanged = snapshot !== state.lastJobSnapshot;
    state.lastJobSnapshot = snapshot;

    const stillRunning = state.jobs.some((j) => j.status === "queued" || j.status === "running");
    if (stillRunning) {
      if (progressChanged) {
        await fetchScans(!state.selectedScanId);
        if (
          state.selectedScanId &&
          state.currentScan?.scan_mode === "handle_only" &&
          state.jobs.some(
            (j) =>
              j.status === "done" &&
              j.scan_mode === "full_text" &&
              (j.scan_ids || []).includes(state.selectedScanId)
          )
        ) {
          await selectScan(state.selectedScanId);
        }
      }
    } else {
      if (prevRunning || progressChanged) {
        await fetchScans(false);
        if (!state.selectedScanId) {
          const latestDone = state.jobs.find((j) => j.scan_ids && j.scan_ids.length > 0);
          if (latestDone) {
            await selectScan(latestDone.scan_ids[0]);
          }
        } else if (state.jobs.some((j) => (j.scan_ids || []).includes(state.selectedScanId))) {
          await selectScan(state.selectedScanId);
        }
      }
      if (state.pollTimer) {
        clearInterval(state.pollTimer);
        state.pollTimer = null;
      }
    }
  } catch {
    // Keep polling quietly on transient hiccup
  }
}

function openScanDialog() {
  els.scanFormError.classList.add("hidden");
  els.scanFormError.textContent = "";
  applyScanMode(state.scanMode);
  els.scanDialog.showModal();
  els.scanPathInput.focus();
}

function initEvents() {
  els.errorDismiss.addEventListener("click", hideError);
  applyScanMode(state.scanMode);

  document.querySelectorAll("[data-scan-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      applyScanMode(btn.getAttribute("data-scan-mode"));
    });
  });

  els.themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") || "light";
    applyTheme(current === "dark" ? "light" : "dark");
  });

  els.videoSearch.addEventListener("input", (event) => {
    state.videoQuery = event.target.value;
    state.listRenderLimit = LIST_RENDER_STEP;
    renderVideoList();
  });

  applyMoveFilter(state.moveFilter);
  document.querySelectorAll("[data-move-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.listRenderLimit = LIST_RENDER_STEP;
      applyMoveFilter(btn.getAttribute("data-move-filter"));
      handleSidebarFilterUpdate();
    });
  });

  document.querySelectorAll("[data-video-filter]").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("[data-video-filter]").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.videoFilter = btn.getAttribute("data-video-filter");
      state.listRenderLimit = LIST_RENDER_STEP;
      handleSidebarFilterUpdate();
    });
  });

  els.videoList.addEventListener("click", (event) => {
    const moreBtn = event.target.closest("#video-list-more-btn");
    if (moreBtn) {
      state.listRenderLimit += LIST_RENDER_STEP;
      renderVideoList();
      return;
    }
    const item = event.target.closest("[data-scan-id]");
    if (!item) return;
    selectScan(item.getAttribute("data-scan-id"));
  });

  els.videoList.addEventListener(
    "scroll",
    () => {
      const moreBtn = document.getElementById("video-list-more-btn");
      if (!moreBtn) return;
      if (els.videoList.scrollTop + els.videoList.clientHeight >= els.videoList.scrollHeight - 160) {
        state.listRenderLimit += LIST_RENDER_STEP;
        renderVideoList();
      }
    },
    { passive: true }
  );

  els.openScanBtn.addEventListener("click", openScanDialog);
  els.closeScanBtn.addEventListener("click", () => els.scanDialog.close());
  els.cancelScanBtn.addEventListener("click", () => els.scanDialog.close());
  els.scanPathInput?.addEventListener("input", () => {
    els.scanFormError.classList.add("hidden");
  });
  els.browseScanFolderBtn?.addEventListener("click", () => {
    openFsPicker("folder", els.scanPathInput, {
      title: "Select folder to scan",
      fallbackDir: state.destinationRoot.trim() || null,
    });
  });
  els.browseScanFileBtn?.addEventListener("click", () => {
    openFsPicker("file", els.scanPathInput, {
      title: "Select video file to scan",
      fallbackDir: state.destinationRoot.trim() || null,
    });
  });

  els.closePickerBtn?.addEventListener("click", () => els.fsPickerDialog.close());
  els.cancelPickerBtn?.addEventListener("click", () => els.fsPickerDialog.close());
  els.fsPickerDialog?.addEventListener("click", (event) => {
    if (event.target === els.fsPickerDialog) {
      els.fsPickerDialog.close();
    }
  });
  els.pickerUpBtn?.addEventListener("click", () => {
    if (pickerState.parentPath !== null) {
      loadFsDirectory(pickerState.parentPath, false);
    }
  });
  els.pickerGoBtn?.addEventListener("click", () => {
    loadFsDirectory(els.pickerPathInput.value, false);
  });
  els.pickerPathInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      loadFsDirectory(els.pickerPathInput.value, false);
    }
  });
  els.pickerFilterInput?.addEventListener("input", (event) => {
    pickerState.filterQuery = event.target.value;
    pickerState.renderLimit = 200;
    renderPickerList();
  });
  els.confirmPickerBtn?.addEventListener("click", confirmFsPickerSelection);
  els.closeLightboxBtn.addEventListener("click", () => els.lightboxDialog.close());

  els.lightboxDialog.addEventListener("click", (event) => {
    if (event.target === els.lightboxDialog) {
      els.lightboxDialog.close();
    }
  });

  els.scanForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const pathVal = els.scanPathInput.value.trim();
    if (!pathVal) {
      els.scanFormError.textContent = "Enter a local directory or video file path first.";
      els.scanFormError.classList.remove("hidden");
      els.scanPathInput.focus();
      return;
    }

    els.scanFormError.classList.add("hidden");
    els.submitScanBtn.disabled = true;
    els.submitScanBtn.textContent = "Starting...";

    try {
      const res = await fetch("/api/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          path: pathVal,
          sample_fps: Number(els.sampleFpsInput.value || 2.0),
          min_confidence: Number(els.minConfInput.value || 0.55),
          recursive: Boolean(els.recursiveInput.checked),
          skip_existing: Boolean(els.skipExistingInput.checked),
          scan_mode: state.scanMode,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        els.scanFormError.textContent = data.detail || "Could not start scan.";
        els.scanFormError.classList.remove("hidden");
        return;
      }

      els.scanDialog.close();
      els.scanPathInput.value = "";
      await pollJobs();
      if (!state.pollTimer) {
        state.pollTimer = setInterval(pollJobs, 1000);
      }
    } catch (err) {
      els.scanFormError.textContent = `Request failed: ${err}`;
      els.scanFormError.classList.remove("hidden");
    } finally {
      els.submitScanBtn.disabled = false;
      els.submitScanBtn.textContent = "Start scan";
    }
  });
}

initTheme();
initEvents();
fetchScans(true);
