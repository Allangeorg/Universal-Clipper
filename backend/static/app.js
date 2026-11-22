const $ = (selector) => document.querySelector(selector);
let currentJobId = null;
let pollTimer = null;
let jobsCache = [];
let clipsCache = [];
let currentEditingClip = null;
let currentSourceDuration = 0;
let uploadedLogoPath = null;
let currentJobOptions = { aspect_ratio: '9:16', editing_intelligence: 'balanced', duration_mode: 'exact', exact_seconds: 60, clip_min_seconds: 60, clip_max_seconds: 60 };

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#039;'}[char]));
}
function timecode(value) {
  const seconds = Math.max(0, Math.floor(Number(value) || 0));
  return `${String(Math.floor(seconds / 60)).padStart(2,'0')}:${String(seconds % 60).padStart(2,'0')}`;
}
function showToast(message) {
  const toast = $('#toast'); toast.textContent = message; toast.classList.remove('hidden');
  clearTimeout(showToast.timer); showToast.timer = setTimeout(() => toast.classList.add('hidden'), 3200);
}
async function api(url, options = {}) {
  const response = await fetch(url, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || `Request failed (${response.status})`);
  return payload;
}

function readCreationOptions() {
  const mode = $('#duration-mode').value || 'exact';
  const exact = Math.round(Number($('#clip-exact-seconds').value || 60));
  let min = Math.round(Number($('#clip-min-seconds').value || 30));
  let max = Math.round(Number($('#clip-max-seconds').value || 60));
  if (mode === 'exact' && (exact < 5 || exact > 600)) throw new Error('Exact duration must be between 5 and 600 seconds.');
  if (mode === 'range') {
    if (min < 5) min = 5;
    if (max > 600) max = 600;
    // Auto-swap if minimum exceeds maximum
    if (min > max) {
      const tmp = min; min = max; max = tmp;
      if ($('#clip-min-seconds')) $('#clip-min-seconds').value = min;
      if ($('#clip-max-seconds')) $('#clip-max-seconds').value = max;
    }
  }
  const brief = ($('#campaign-brief')?.value || '').trim();
  const logoPlacement = $('#logo-position')?.value || 'top-right';
  const logoScale = Number($('#logo-scale')?.value || 0.20);
  const logoOpacity = Number($('#logo-opacity')?.value || 0.90);

  return {
    duration_mode: mode, exact_seconds: exact, min_seconds: min, max_seconds: max,
    editing_intelligence: $('#editing-intelligence').value, aspect_ratio: $('#create-aspect').value || '9:16',
    campaign_brief: brief,
    logo_path: uploadedLogoPath || '',
    logo_position: logoPlacement,
    logo_scale: logoScale,
    logo_opacity: logoOpacity
  };
}

function updateDurationControls() {
  const exact = $('#duration-mode').value === 'exact';
  $('#duration-exact-fields').classList.toggle('hidden', !exact);
  $('#duration-range-fields').classList.toggle('hidden', exact);
}

function validateAndSyncDuration(fromTop = true) {
  const mode = $(fromTop ? '#duration-mode' : '#active-duration-mode')?.value || 'exact';
  let exact = Math.round(Number($(fromTop ? '#clip-exact-seconds' : '#active-exact-seconds')?.value || 60));
  let min = Math.round(Number($(fromTop ? '#clip-min-seconds' : '#active-min-seconds')?.value || 30));
  let max = Math.round(Number($(fromTop ? '#clip-max-seconds' : '#active-max-seconds')?.value || 60));

  if (mode === 'range' && min > 0 && max > 0 && min > max) {
    const tmp = min; min = max; max = tmp;
    showToast(`Adjusted range to ${min}–${max}s (minimum cannot exceed maximum)`);
  }

  if ($('#duration-mode')) $('#duration-mode').value = mode;
  if ($('#clip-exact-seconds')) $('#clip-exact-seconds').value = exact;
  if ($('#clip-min-seconds')) $('#clip-min-seconds').value = min;
  if ($('#clip-max-seconds')) $('#clip-max-seconds').value = max;
  updateDurationControls();

  if ($('#active-duration-mode')) $('#active-duration-mode').value = mode;
  if ($('#active-exact-seconds')) $('#active-exact-seconds').value = exact;
  if ($('#active-min-seconds')) $('#active-min-seconds').value = min;
  if ($('#active-max-seconds')) $('#active-max-seconds').value = max;
  $('#active-exact-group')?.classList.toggle('hidden', mode !== 'exact');
  $('#active-range-group')?.classList.toggle('hidden', mode === 'exact');

  if (currentJobId && $('#apply-to-active-btn')) {
    $('#apply-to-active-btn').classList.remove('hidden');
    $('#apply-to-active-btn').textContent = `⚡ Apply ${mode === 'exact' ? `${exact}s` : `${min}–${max}s`} to Selected Project & Re-discover`;
  }
}

async function uploadFile(file) {
  if (!file) return;
  let creationOptions;
  try { creationOptions = readCreationOptions(); } catch (error) { showToast(error.message); return; }
  const form = new FormData(); form.append('file', file);
  Object.entries(creationOptions).forEach(([key, value]) => form.append(key, String(value)));
  showToast('Uploading video…');
  try {
    const result = await api('/api/jobs/upload', { method: 'POST', body: form });
    currentJobId = result.job_id;
    showToast('Upload complete. Analysis queued.');
    await refreshJobs(); selectJob(currentJobId);
  } catch (error) { showToast(error.message); }
  finally { $('#video-file').value = ''; }
}

async function submitUrl() {
  const url = $('#video-url').value.trim();
  if (!url) return showToast('Paste a video URL first.');
  let creationOptions;
  try { creationOptions = readCreationOptions(); } catch (error) { showToast(error.message); return; }
  $('#url-submit').disabled = true;
  try {
    const result = await api('/api/jobs/url', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({url, ...creationOptions}) });
    currentJobId = result.job_id; $('#video-url').value = '';
    showToast('URL queued for processing.'); await refreshJobs(); selectJob(currentJobId);
  } catch (error) { showToast(error.message); }
  finally { $('#url-submit').disabled = false; }
}

let storageDataCache = null;

async function loadStorageUsage() {
  try {
    const data = await api('/api/storage');
    storageDataCache = data;
    const totalFormatted = data.total_formatted || '0 B';
    if ($('#topbar-storage-size')) $('#topbar-storage-size').textContent = totalFormatted;
    if ($('#sidebar-storage-badge')) $('#sidebar-storage-badge').textContent = totalFormatted;
    if ($('#storage-total-display')) $('#storage-total-display').textContent = totalFormatted;
    if ($('#storage-sources-display')) $('#storage-sources-display').textContent = data.sources_formatted || '0 B';
    if ($('#storage-work-display')) $('#storage-work-display').textContent = data.work_formatted || '0 B';
    if ($('#storage-exports-display')) $('#storage-exports-display').textContent = data.exports_formatted || '0 B';

    const total = data.total_bytes || 1;
    const sourcesPct = Math.min(100, Math.round(((data.sources_bytes || 0) / total) * 100));
    const workPct = Math.min(100, Math.round(((data.work_bytes || 0) / total) * 100));
    const exportsPct = Math.min(100, Math.round(((data.exports_bytes || 0) / total) * 100));

    if ($('#meter-sources')) $('#meter-sources').style.width = `${sourcesPct}%`;
    if ($('#meter-work')) $('#meter-work').style.width = `${workPct}%`;
    if ($('#meter-exports')) $('#meter-exports').style.width = `${exportsPct}%`;

    renderStorageProjects(data.jobs || []);
  } catch (err) {
    console.warn('Failed to load storage usage:', err);
  }
}

function renderStorageProjects(jobs) {
  const container = $('#storage-jobs-list');
  if (!container) return;
  if (!jobs.length) {
    container.innerHTML = '<div class="muted" style="padding:12px;text-align:center;">No project videos stored.</div>';
    return;
  }
  container.innerHTML = jobs.map(j => {
    const hasSource = j.has_source && j.size_bytes > 0;
    const sizePill = hasSource ? `<span class="storage-size-pill">💾 ${escapeHtml(j.size_formatted)}</span>` : `<span class="muted" style="font-size:11px;">Source Cleared</span>`;
    const actionBtn = hasSource ? `<button type="button" class="quiet-button clear-job-source-btn" data-job="${escapeHtml(j.job_id)}" style="font-size:11px; padding:3px 8px; color:var(--amber);">🗑 Free Video</button>` : '';
    return `
      <div class="storage-job-row">
        <div class="storage-job-name" title="${escapeHtml(j.source_name)}">
          <strong>${escapeHtml(j.source_name)}</strong>
        </div>
        <div class="storage-job-actions">
          ${sizePill}
          ${actionBtn}
          <button type="button" class="quiet-button danger-button delete-project-btn" data-job="${escapeHtml(j.job_id)}" style="font-size:11px; padding:3px 8px;">✕ Delete</button>
        </div>
      </div>
    `;
  }).join('');

  container.querySelectorAll('.clear-job-source-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const jid = btn.dataset.job;
      if (!confirm('Free laptop disk space by deleting the raw source video for this project? (Your clips, edit plans, and exports will remain intact.)')) return;
      try {
        btn.disabled = true;
        btn.textContent = 'Freeing…';
        const res = await api(`/api/jobs/${jid}/clear-source`, { method: 'POST' });
        showToast(`✓ Freed ${res.freed_formatted || 'disk space'} from project source!`);
        await loadStorageUsage();
        await refreshJobs();
      } catch (err) {
        showToast(`Error: ${err.message}`);
        btn.disabled = false;
        btn.textContent = '🗑 Free Video';
      }
    });
  });

  container.querySelectorAll('.delete-project-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const jid = btn.dataset.job;
      if (!confirm('Permanently delete this project and all its video files from your laptop?')) return;
      try {
        btn.disabled = true;
        btn.textContent = 'Deleting…';
        await api(`/api/jobs/${jid}`, { method: 'DELETE' });
        showToast('✓ Project deleted from laptop.');
        if (currentJobId === jid) {
          currentJobId = null;
          $('#detail-panel').classList.add('hidden');
        }
        await loadStorageUsage();
        await refreshJobs();
      } catch (err) {
        showToast(`Error: ${err.message}`);
        btn.disabled = false;
        btn.textContent = '✕ Delete';
      }
    });
  });
}

function openStorageModal() {
  $('#storage-modal').classList.remove('hidden');
  loadStorageUsage();
}

function closeStorageModal() {
  $('#storage-modal').classList.add('hidden');
}

function renderJobs(jobs) {
  jobsCache = jobs;
  if (!jobs.length) {
    $('#jobs-list').innerHTML = `
      <div class="empty-state">
        <div class="empty-symbol">
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
        </div>
        <strong>No projects queued</strong>
        <p>Import a recording or URL above to begin discovering highlights.</p>
      </div>`;
    return;
  }
  $('#jobs-list').innerHTML = jobs.map(job => {
    const active = job.id === currentJobId ? ' active' : '';
    const status = job.status === 'completed' ? 'COMPLETE' : job.status === 'failed' ? 'FAILED' : job.status === 'queued' ? 'QUEUED' : 'PROCESSING';
    const progress = Math.max(0, Math.min(100, Number(job.progress || 0) * 100));
    const storageItem = (storageDataCache?.jobs || []).find(sj => sj.job_id === job.id);
    const hasSource = storageItem && storageItem.has_source && storageItem.size_bytes > 0;
    const sourceSizeBadge = hasSource
      ? `<span class="storage-size-pill" style="margin-left:6px;" title="Source video stored on laptop">${escapeHtml(storageItem.size_formatted)}</span>`
      : '';

    return `
      <div class="job-card${active}" data-job="${escapeHtml(job.id)}">
        <div class="job-main">
          <div class="file-icon" style="background: rgba(245, 158, 11, 0.1); border: 1px solid rgba(245, 158, 11, 0.25); border-radius: 8px; width: 36px; height: 36px; display: grid; place-items: center; color: var(--accent);">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="23 7 16 12 23 17 23 7"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>
          </div>
          <div class="job-copy">
            <div style="display:flex; align-items:center; gap:6px; flex-wrap:wrap;">
              <strong>${escapeHtml(job.source_name)}</strong>
              ${sourceSizeBadge}
            </div>
            <small>${escapeHtml(job.message || job.status)} · ${new Date(job.created_at).toLocaleDateString()}</small>
          </div>
        </div>
        <div class="job-actions-row">
          <button type="button" class="quiet-button select-job-btn" data-job="${escapeHtml(job.id)}">Open Project</button>
          ${hasSource ? `<button type="button" class="quiet-button quick-free-source-btn" data-job="${escapeHtml(job.id)}" title="Free up laptop disk space by deleting raw source video" style="color:var(--amber);">Free Video</button>` : ''}
          <button type="button" class="quiet-button danger-button quick-delete-job-btn" data-job="${escapeHtml(job.id)}" title="Delete project and files">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>
        <div class="job-status" style="margin-left: 8px;">
          <span>${status}</span>
          ${job.status !== 'completed' ? `<small>${Math.round(progress)}%</small><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div>` : ''}
        </div>
      </div>
    `;
  }).join('');

  document.querySelectorAll('.job-card').forEach(card => card.addEventListener('click', (e) => {
    if (e.target.closest('.job-actions-row button')) return;
    selectJob(card.dataset.job);
  }));

  document.querySelectorAll('.select-job-btn').forEach(btn => btn.addEventListener('click', (e) => {
    e.stopPropagation();
    selectJob(btn.dataset.job);
  }));

  document.querySelectorAll('.quick-free-source-btn').forEach(btn => btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const jid = btn.dataset.job;
    if (!confirm('Free laptop disk space by deleting the raw source video for this project? (Candidate clips and exports are preserved.)')) return;
    try {
      btn.disabled = true;
      btn.textContent = 'Freeing…';
      const res = await api(`/api/jobs/${jid}/clear-source`, { method: 'POST' });
      showToast(`Freed ${res.freed_formatted || 'disk space'} from project source.`);
      await loadStorageUsage();
      await refreshJobs();
    } catch (err) {
      showToast(`Error: ${err.message}`);
      btn.disabled = false;
      btn.textContent = 'Free Video';
    }
  }));

  document.querySelectorAll('.quick-delete-job-btn').forEach(btn => btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const jid = btn.dataset.job;
    if (!confirm('Permanently delete this project and all its files from your laptop?')) return;
    try {
      btn.disabled = true;
      btn.textContent = '…';
      await api(`/api/jobs/${jid}`, { method: 'DELETE' });
      showToast('Project deleted from laptop.');
      if (currentJobId === jid) {
        currentJobId = null;
        $('#detail-panel').classList.add('hidden');
      }
      await loadStorageUsage();
      await refreshJobs();
    } catch (err) {
      showToast(`Error: ${err.message}`);
      btn.disabled = false;
      btn.textContent = '✕';
    }
  }));
}

async function refreshJobs() {
  try {
    const data = await api('/api/jobs'); renderJobs(data.jobs);
    const active = data.jobs.some(job => ['queued','processing'].includes(job.status));
    if (active && !pollTimer) pollTimer = setInterval(refreshJobs, 2200);
    if (!active && pollTimer) { clearInterval(pollTimer); pollTimer = null; }
    if (currentJobId) {
      const current = data.jobs.find(job => job.id === currentJobId);
      if (current) await refreshSelectedJob(current.status);
    }
  } catch (error) { console.error(error); }
}

async function selectJob(jobId) {
  currentJobId = jobId;
  renderJobs(jobsCache);
  $('#detail-panel').classList.remove('hidden');
  await refreshSelectedJob();
  $('#detail-panel').scrollIntoView({behavior:'smooth',block:'start'});
}

async function refreshSelectedJob(statusOverride) {
  if (!currentJobId) return;
  try {
    const job = await api(`/api/jobs/${currentJobId}`);
    currentJobOptions = job.options || { aspect_ratio: '9:16', editing_intelligence: 'balanced', duration_mode: 'exact', exact_seconds: 60, clip_min_seconds: 60, clip_max_seconds: 60 };
    if ($('#aspect-ratio')) $('#aspect-ratio').value = currentJobOptions.aspect_ratio || '9:16';
    const activeMode = currentJobOptions.duration_mode || 'exact';
    const exactSec = currentJobOptions.exact_seconds || 60;
    const minSec = currentJobOptions.clip_min_seconds || currentJobOptions.min_seconds || 30;
    const maxSec = currentJobOptions.clip_max_seconds || currentJobOptions.max_seconds || 60;

    if ($('#active-duration-mode')) {
      $('#active-duration-mode').value = activeMode;
      $('#active-exact-group')?.classList.toggle('hidden', activeMode !== 'exact');
      $('#active-range-group')?.classList.toggle('hidden', activeMode === 'exact');
      if ($('#active-exact-seconds')) $('#active-exact-seconds').value = exactSec;
      if ($('#active-min-seconds')) $('#active-min-seconds').value = minSec;
      if ($('#active-max-seconds')) $('#active-max-seconds').value = maxSec;
    }
    if (currentJobOptions.campaign_brief !== undefined) {
      if ($('#campaign-brief')) $('#campaign-brief').value = currentJobOptions.campaign_brief || '';
      if ($('#active-campaign-brief')) $('#active-campaign-brief').value = currentJobOptions.campaign_brief || '';
      if ($('#active-brief-status')) {
        $('#active-brief-status').textContent = currentJobOptions.campaign_brief ? 'Custom brief active' : 'Default AI discovery';
      }
    }
    if (currentJobOptions.logo_path) {
      uploadedLogoPath = currentJobOptions.logo_path;
      const filename = currentJobOptions.logo_path.split(/[/\\]/).pop();
      if ($('#logo-filename')) $('#logo-filename').textContent = filename;
      if ($('#logo-preview-img')) $('#logo-preview-img').src = `/api/branding/file/${encodeURIComponent(filename)}`;
      $('#logo-preview-box')?.classList.remove('hidden');
      $('#logo-remove-btn')?.classList.remove('hidden');
    }
    if (currentJobOptions.logo_position && $('#logo-position')) $('#logo-position').value = currentJobOptions.logo_position;
    if (currentJobOptions.logo_scale && $('#logo-scale')) $('#logo-scale').value = String(currentJobOptions.logo_scale);
    if (currentJobOptions.logo_opacity && $('#logo-opacity')) $('#logo-opacity').value = String(currentJobOptions.logo_opacity);
    // Also sync the top creation controls to reflect this project
    if ($('#duration-mode')) $('#duration-mode').value = activeMode;
    updateDurationControls();
    if ($('#clip-exact-seconds')) $('#clip-exact-seconds').value = exactSec;
    if ($('#clip-min-seconds')) $('#clip-min-seconds').value = minSec;
    if ($('#clip-max-seconds')) $('#clip-max-seconds').value = maxSec;
    if ($('#apply-to-active-btn')) {
      $('#apply-to-active-btn').classList.remove('hidden');
      $('#apply-to-active-btn').textContent = `⚡ Apply ${activeMode === 'exact' ? `${exactSec}s` : `${minSec}–${maxSec}s`} to Selected Project & Re-discover`;
    }
    $('#active-source-name').textContent = job.source_name;
    const profile = job.result?.content_profile;
    const clipSettings = job.result?.clip_settings || job.options || {};
    const requestedLength = clipSettings.duration_mode === 'exact'
      ? `exact ${clipSettings.exact_seconds || clipSettings.min_seconds || clipSettings.clip_min_seconds || 60}s`
      : `${clipSettings.min_seconds || clipSettings.clip_min_seconds || 30}–${clipSettings.max_seconds || clipSettings.clip_max_seconds || 60}s`;
    const specLabel = ` · ${requestedLength} · ${clipSettings.aspect_ratio || '9:16'} · ${(clipSettings.editing_intelligence || 'balanced')} mode`;
    $('#source-meta').textContent = job.duration ? `${Math.round(job.duration / 60)} min source · ${job.status} · ${job.stage}${profile ? ` · profile: ${profile.kind} (${Math.round(profile.confidence * 100)}%)` : ''}${specLabel}` : `${job.status} · ${job.message}${specLabel}`;
    if (job.status === 'failed') {
      $('#warnings').classList.remove('hidden');
      $('#warnings').innerHTML = `<strong>Processing failed:</strong> ${escapeHtml(job.error || 'Unknown error')} <button class="quiet-button" id="retry-job">Retry job</button>`;
      $('#retry-job').addEventListener('click', async () => { await api(`/api/jobs/${currentJobId}/retry`, {method:'POST'}); showToast('Job queued for retry.'); refreshJobs(); });
    } else if (job.result?.warnings?.length) {
      $('#warnings').classList.remove('hidden'); $('#warnings').innerHTML = job.result.warnings.map(w => `• ${escapeHtml(w)}`).join('<br>');
    } else { $('#warnings').classList.add('hidden'); }
    if (job.status === 'completed') {
      clipsCache = job.clips || []; renderClips(clipsCache);
      $('#detail-panel').classList.remove('hidden');
    } else if (!job.clips?.length) {
      $('#clips-list').innerHTML = `<div class="empty-state"><strong>${escapeHtml(job.message || 'Analysis running')}</strong><p>Analysis stages are saved as they complete.</p></div>`;
      $('#clip-count-label').textContent = 'Processing';
    }
  } catch (error) { console.error(error); }
}

function renderClips(clips) {
  $('#clip-count-label').textContent = `${clips.length} candidates`;
  const selectedCount = clips.filter(clip => clip.selected).length;
  $('#selected-count').textContent = `${selectedCount} clips selected`;
  if (!clips.length) {
    $('#clips-list').innerHTML = '<div class="empty-state"><strong>No candidate clips detected</strong><p>Try a source with clearer audio or visual activity, or enable cloud transcription in AI settings.</p></div>';
    return;
  }
  $('#clips-list').innerHTML = clips.map(clip => {
    const components = Object.entries(clip.components || {}).filter(([,v]) => Number(v) > 0).sort((a,b)=>b[1]-a[1]).slice(0,6);
    const narrativeBadge = clip.narrative_role ? `
      <span class="narrative-badge">${escapeHtml(clip.narrative_role)}</span>
    ` : '';
    const synopsisHtml = clip.context_synopsis ? `
      <div class="clip-synopsis"><em>${escapeHtml(clip.context_synopsis)}</em></div>
    ` : '';
    const calloutBadge = clip.callout_overlay ? `
      <div class="hook-banner-pill">
        <span class="hook-banner-label">HOOK OVERLAY</span>
        <span class="hook-banner-text">${escapeHtml(clip.callout_overlay)}</span>
      </div>
    ` : '';
    const captionHtml = clip.social_caption ? `
      <div class="social-caption-card">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom: 6px;">
          <span class="caption-card-title">Ready-to-Post Caption</span>
          <button type="button" class="quiet-button copy-caption-btn" data-caption="${escapeHtml(clip.social_caption)}">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>
            <span>Copy</span>
          </button>
        </div>
        <div style="font-size: 12px; color: #f3f4f8; white-space: pre-wrap; line-height: 1.5;">${escapeHtml(clip.social_caption)}</div>
      </div>
    ` : '';
    const signalsDrawer = components.length ? `
      <details class="signals-drawer">
        <summary class="signals-summary">Technical Signals (${components.length}) ▾</summary>
        <div class="component-pills">
          ${components.map(([key,value])=>`<span>${escapeHtml(key.replaceAll('_',' '))} ${Number(value).toFixed(1)}</span>`).join('')}
        </div>
      </details>
    ` : '';
    const scoreVal = Number(clip.score || 0);
    const viralTier = scoreVal >= 8 ? 'VIRAL HOOK' : scoreVal >= 6 ? 'STRONG SCENE' : 'HIGHLIGHT';

    return `<article class="clip-card"><input class="clip-check" type="checkbox" data-clip="${escapeHtml(clip.id)}" ${clip.selected ? 'checked' : ''} aria-label="Select clip"/><div class="clip-content"><div class="clip-title" style="display:flex; align-items:center; flex-wrap:wrap; gap:8px;"><span>${escapeHtml(clip.title)}</span>${narrativeBadge}</div>${synopsisHtml}${calloutBadge}<div class="clip-meta"><span>${timecode(clip.start)} – ${timecode(clip.end)}</span><span>${Math.round(clip.duration)} sec</span>${clip.output_path ? '<span>EXPORTED</span>' : ''}</div><div class="clip-reason">${escapeHtml(clip.reason)}</div>${clip.transcript ? `<div class="clip-transcript">“${escapeHtml(clip.transcript)}”</div>` : ''}${captionHtml}${signalsDrawer}<button class="quiet-button open-editor-button" data-edit-clip="${escapeHtml(clip.id)}" style="margin-top: 10px;">Open in Editor</button></div><div class="score-box"><strong>${scoreVal.toFixed(1)}</strong><small> / 10</small><div class="score-box-sub">${viralTier}</div></div></article>`;

  }).join('');
  document.querySelectorAll('.copy-caption-btn').forEach(btn => btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    const text = btn.dataset.caption;
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      showToast('✓ Caption copied to clipboard!');
      const prev = btn.textContent;
      btn.textContent = '✓ Copied!';
      setTimeout(() => { btn.textContent = prev; }, 2000);
    } catch (_) { showToast('Clipboard unavailable.'); }
  }));
  document.querySelectorAll('.clip-check').forEach(input => input.addEventListener('change', async () => {
    const id = input.dataset.clip;
    try { await api(`/api/jobs/${currentJobId}/clips/${id}`, {method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected:input.checked})});
      const clip = clipsCache.find(item => item.id === id); if (clip) clip.selected = input.checked;
      $('#selected-count').textContent = `${clipsCache.filter(item=>item.selected).length} clips selected`;
    } catch (error) { showToast(error.message); input.checked = !input.checked; }
  }));
  document.querySelectorAll('[data-edit-clip]').forEach(button => button.addEventListener('click', () => openClipEditor(button.dataset.editClip)));
}

function openClipEditor(clipId) {
  const clip = clipsCache.find(item => item.id === clipId);
  if (!clip || !currentJobId) return;
  currentEditingClip = clip;
  const job = jobsCache.find(item => item.id === currentJobId);
  currentSourceDuration = Number(job?.duration || clip.end || 0);
  $('#editor-title').textContent = clip.title || 'Edit your clip';
  $('#editor-subtitle').textContent = `${timecode(clip.start)} – ${timecode(clip.end)} · ${Math.round(clip.score)} / 10 signal score`;
  $('#editor-video').src = `/api/jobs/${currentJobId}/source`;
  $('#editor-video').load();
  $('#edit-start').value = Number(clip.start).toFixed(1);
  $('#edit-end').value = Number(clip.end).toFixed(1);
  $('#edit-start').max = currentSourceDuration;
  $('#edit-end').max = currentSourceDuration;
  $('#playhead-range').max = currentSourceDuration || 100;
  $('#playhead-range').value = clip.start;
  $('#edit-aspect').value = currentJobOptions.aspect_ratio || '9:16';
  $('#focus-x').value = 0.5; $('#focus-y').value = 0.5; $('#edit-zoom').value = 1;
  $('#edit-speed').value = 1; $('#edit-volume').value = 1; $('#fade-in').value = 0; $('#fade-out').value = 0;
  $('#edit-brightness').value = 0; $('#edit-contrast').value = 1; $('#edit-saturation').value = 1;
  $('#preserve-audio').checked = true; $('#normalize-audio').checked = true; $('#edit-framing-mode').value = 'adaptive';
  $('#captions-enabled').checked = false;
  $('#caption-style').value = 'karaoke'; $('#caption-size').value = 72; $('#caption-position').value = 'bottom';
  $('#caption-color').value = '#FFFFFF'; $('#caption-outline').value = '#000000';
  $('#title-overlay').value = clip.callout_overlay || ''; $('#caption-text').value = '';
  $('#edit-render-result').classList.add('hidden');
  $('#editor-panel').classList.remove('hidden');
  updateEditorPreview(); updateEditorTimeline();
  $('#editor-panel').scrollIntoView({behavior:'smooth', block:'start'});
  $('#editor-video').addEventListener('loadedmetadata', () => {
    $('#editor-video').currentTime = Number($('#edit-start').value || clip.start);
    updateEditorTimeline();
  }, {once:true});
}

function updateEditorPreview() {
  const video = $('#editor-video');
  const ratio = $('#edit-aspect').value;
  const framing = $('#edit-framing-mode').value;
  const ratios = {'9:16':'9 / 16','16:9':'16 / 9','1:1':'1 / 1','original':'16 / 9'};
  $('#preview-stage').style.aspectRatio = ratios[ratio] || '9 / 16';
  $('#preview-format-tag').textContent = `${ratio} · PREVIEW`;
  const x = Number($('#focus-x').value), y = Number($('#focus-y').value), zoom = Number($('#edit-zoom').value);
  video.style.objectFit = (ratio === 'original' || framing === 'full_frame') ? 'contain' : 'cover';
  video.style.objectPosition = `${x * 100}% ${y * 100}%`;
  video.style.transform = framing === 'full_frame' ? 'none' : `scale(${zoom})`;
  $('#focus-x-value').textContent = `${Math.round(x*100)}%`;
  $('#focus-y-value').textContent = `${Math.round(y*100)}%`;
  $('#zoom-value').textContent = `${zoom.toFixed(1)}×`;
  $('#brightness-value').textContent = Number($('#edit-brightness').value).toFixed(2);
  $('#contrast-value').textContent = Number($('#edit-contrast').value).toFixed(2);
  $('#saturation-value').textContent = Number($('#edit-saturation').value).toFixed(2);
  $('#speed-value').textContent = `${Number($('#edit-speed').value).toFixed(2)}×`;
  $('#volume-value').textContent = `${Math.round(Number($('#edit-volume').value)*100)}%`;
}

function updateEditorTimeline() {
  if (!currentEditingClip) return;
  const start = Math.max(0, Number($('#edit-start').value || currentEditingClip.start));
  const end = Math.min(currentSourceDuration || Number($('#edit-end').value), Number($('#edit-end').value || currentEditingClip.end));
  const duration = Math.max(0.1, end-start);
  $('#edit-duration').textContent = timecode(duration);
  const left = currentSourceDuration ? start/currentSourceDuration*100 : 0;
  const width = currentSourceDuration ? duration/currentSourceDuration*100 : 100;
  $('#clip-range').style.left = `${left}%`;
  $('#clip-range').style.width = `${Math.max(0,width)}%`;
  const current = Number($('#editor-video').currentTime || start);
  $('#timeline-playhead').style.left = `${currentSourceDuration ? current/currentSourceDuration*100 : 0}%`;
  $('#playhead-range').value = current;
  $('#preview-timecode').textContent = `${timecode(current)} / ${timecode(currentSourceDuration)}`;
}

function setClipInOut(which, value) {
  if (!currentEditingClip) return;
  const video = $('#editor-video');
  const minGap = 0.2;
  if (which === 'start') {
    const next = Math.max(0, Math.min(Number(value), Number($('#edit-end').value)-minGap));
    $('#edit-start').value = next.toFixed(1);
    if (video.currentTime < next) video.currentTime = next;
  } else {
    const next = Math.max(Number($('#edit-start').value)+minGap, Math.min(Number(value), currentSourceDuration));
    $('#edit-end').value = next.toFixed(1);
    if (video.currentTime > next) video.currentTime = next;
  }
  updateEditorTimeline();
}

function resetEditorControls() {
  if (!currentEditingClip) return;
  $('#edit-start').value = Number(currentEditingClip.start).toFixed(1);
  $('#edit-end').value = Number(currentEditingClip.end).toFixed(1);
  $('#edit-aspect').value = currentJobOptions.aspect_ratio || '9:16'; $('#focus-x').value = 0.5; $('#focus-y').value = 0.5;
  $('#edit-zoom').value = 1; $('#edit-speed').value = 1; $('#edit-volume').value = 1;
  $('#fade-in').value = 0; $('#fade-out').value = 0; $('#edit-brightness').value = 0; $('#edit-contrast').value = 1; $('#edit-saturation').value = 1;
  $('#preserve-audio').checked = true; $('#normalize-audio').checked = true; $('#edit-framing-mode').value = 'adaptive'; $('#captions-enabled').checked = false;
  $('#caption-style').value = 'karaoke'; $('#caption-size').value = 72; $('#caption-position').value = 'bottom';
  $('#caption-color').value = '#FFFFFF'; $('#caption-outline').value = '#000000';
  $('#title-overlay').value = clip.callout_overlay || ''; $('#caption-text').value = '';
  const feedback = $('#ai-assist-feedback'); if (feedback) feedback.classList.add('hidden');
  updateEditorPreview(); updateEditorTimeline();
}

function applySettingsToEditor(s) {
  if (!s) return;
  if (s.start !== undefined) $('#edit-start').value = Number(s.start).toFixed(1);
  if (s.end !== undefined) $('#edit-end').value = Number(s.end).toFixed(1);
  if (s.aspect_ratio !== undefined) $('#edit-aspect').value = s.aspect_ratio;
  if (s.framing_mode !== undefined) $('#edit-framing-mode').value = s.framing_mode;
  if (s.focus_x !== undefined) $('#focus-x').value = s.focus_x;
  if (s.focus_y !== undefined) $('#focus-y').value = s.focus_y;
  if (s.zoom !== undefined) $('#edit-zoom').value = s.zoom;
  if (s.speed !== undefined) $('#edit-speed').value = s.speed;
  if (s.volume !== undefined) $('#edit-volume').value = s.volume;
  if (s.fade_in !== undefined) $('#fade-in').value = s.fade_in;
  if (s.fade_out !== undefined) $('#fade-out').value = s.fade_out;
  if (s.brightness !== undefined) $('#edit-brightness').value = s.brightness;
  if (s.contrast !== undefined) $('#edit-contrast').value = s.contrast;
  if (s.saturation !== undefined) $('#edit-saturation').value = s.saturation;
  if (s.preserve_audio !== undefined) $('#preserve-audio').checked = Boolean(s.preserve_audio);
  if (s.normalize_audio !== undefined) $('#normalize-audio').checked = Boolean(s.normalize_audio);
  if (s.captions_enabled !== undefined) $('#captions-enabled').checked = Boolean(s.captions_enabled);
  if (s.caption_style !== undefined) $('#caption-style').value = s.caption_style;
  if (s.caption_position !== undefined) $('#caption-position').value = s.caption_position;
  if (s.font_size !== undefined) $('#caption-size').value = s.font_size;
  if (s.text_colour !== undefined) $('#caption-color').value = s.text_colour;
  if (s.outline_colour !== undefined) $('#caption-outline').value = s.outline_colour;
  if (s.title_overlay !== undefined) $('#title-overlay').value = s.title_overlay;
  updateEditorPreview();
  updateEditorTimeline();
}

async function renderEditedClip() {
  if (!currentEditingClip) return;
  const start = Number($('#edit-start').value), end = Number($('#edit-end').value);
  if (!(end > start)) return showToast('Out point must be after the in point.');
  const button = $('#render-edit'); button.disabled = true; button.textContent = 'Rendering edit…';
  $('#edit-render-result').classList.add('hidden');
  const activeBrief = ($('#active-campaign-brief')?.value || $('#campaign-brief')?.value || currentJobOptions.campaign_brief || '').trim();
  const logoPlacement = $('#logo-position')?.value || currentJobOptions.logo_position || 'top-right';
  const logoScale = Number($('#logo-scale')?.value || currentJobOptions.logo_scale || 0.20);
  const logoOpacity = Number($('#logo-opacity')?.value || currentJobOptions.logo_opacity || 0.90);
  const logoPath = uploadedLogoPath || currentJobOptions.logo_path || '';

  const payload = {
    start, end, aspect_ratio: $('#edit-aspect').value,
    framing_mode: $('#edit-framing-mode').value, ai_reframe: true, normalize_audio: $('#normalize-audio').checked,
    focus_x: Number($('#focus-x').value), focus_y: Number($('#focus-y').value),
    zoom: Number($('#edit-zoom').value), speed: Number($('#edit-speed').value),
    volume: Number($('#edit-volume').value), preserve_audio: $('#preserve-audio').checked,
    brightness: Number($('#edit-brightness').value), contrast: Number($('#edit-contrast').value), saturation: Number($('#edit-saturation').value),
    fade_in: Number($('#fade-in').value), fade_out: Number($('#fade-out').value),
    captions_enabled: $('#captions-enabled').checked, caption_style: $('#caption-style').value,
    caption_position: $('#caption-position').value, font_size: Number($('#caption-size').value),
    text_colour: $('#caption-color').value, outline_colour: $('#caption-outline').value,
    title_overlay: $('#title-overlay').value, caption_text: $('#caption-text').value,
    preset: 'fast', crf: 18,
    campaign_brief: activeBrief,
    logo_path: logoPath,
    logo_position: logoPlacement,
    logo_scale: logoScale,
    logo_opacity: logoOpacity
  };
  try {
    const result = await api(`/api/jobs/${currentJobId}/clips/${currentEditingClip.id}/render`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    const node = $('#edit-render-result'); node.classList.remove('hidden');
    node.innerHTML = `<strong>Edited clip rendered successfully</strong><p>${timecode(result.duration)} · ${escapeHtml(result.settings.aspect_ratio)} · ${(result.bytes/1024/1024).toFixed(1)} MB · ${escapeHtml(result.reframe?.method || 'framing')}</p>${(result.warnings||[]).map(w=>`<p class="warning-text">${escapeHtml(w)}</p>`).join('')}<div class="edit-result-actions"><a class="primary-button" href="${escapeHtml(result.url)}" download>Download edited MP4 ↓</a><button class="quiet-button" id="load-render-preview">Preview rendered clip</button></div>`;
    $('#load-render-preview').addEventListener('click', () => { $('#editor-video').src = result.url; $('#editor-video').load(); });
    showToast('Your edited clip is ready.');
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; button.innerHTML = 'Render edited clip <span>→</span>'; }
}

async function exportSelected() {
  const selectedClips = clipsCache.filter(c => c.selected);
  if (!selectedClips.length) {
    showToast("Please select at least one clip to export.");
    return;
  }
  const button = $('#export-btn');
  button.disabled = true;
  $('#export-results').classList.remove('hidden');
  $('#export-results').innerHTML = `<strong>Exporting ${selectedClips.length} clip(s)...</strong><div id="export-items-stream" style="margin-top:12px; display:flex; flex-direction:column; gap:8px;"></div>`;
  const streamContainer = $('#export-items-stream');

  let successCount = 0;
  const renderedItems = [];
  const activeBrief = ($('#active-campaign-brief')?.value || $('#campaign-brief')?.value || currentJobOptions.campaign_brief || '').trim();
  const logoPlacement = $('#logo-position')?.value || currentJobOptions.logo_position || 'top-right';
  const logoScale = Number($('#logo-scale')?.value || currentJobOptions.logo_scale || 0.20);
  const logoOpacity = Number($('#logo-opacity')?.value || currentJobOptions.logo_opacity || 0.90);
  const logoPath = uploadedLogoPath || currentJobOptions.logo_path || '';

  const exportPayloadBase = {
    aspect_ratio: $('#aspect-ratio').value,
    preserve_audio: true,
    framing_mode: $('#export-framing-mode').value,
    normalize_audio: $('#export-normalize-audio').checked,
    captions_enabled: $('#export-captions').checked,
    ai_edit: $('#export-ai-edit').checked,
    caption_style: 'karaoke',
    preset: 'fast',
    crf: 18,
    campaign_brief: activeBrief,
    logo_path: logoPath,
    logo_position: logoPlacement,
    logo_scale: logoScale,
    logo_opacity: logoOpacity
  };

  try {
    for (let i = 0; i < selectedClips.length; i++) {
      const clip = selectedClips[i];
      button.textContent = `Rendering clip ${i + 1} of ${selectedClips.length}…`;
      try {
        const result = await api(`/api/jobs/${currentJobId}/export`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({...exportPayloadBase, clip_ids: [clip.id]}),
        });
        const items = result.rendered || [];
        renderedItems.push(...items);
        successCount += result.success_count || 0;

        for (const item of items) {
          const div = document.createElement('div');
          div.style.cssText = 'padding: 10px 12px; background: rgba(255,255,255,0.05); border-radius: 6px; margin-bottom: 6px;';
          const captionHtml = item.social_caption ? `
            <div style="margin-top: 8px; padding: 8px 10px; background: rgba(0,0,0,0.3); border-radius: 4px; display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
              <span style="font-size: 11px; color: #ddd; white-space: pre-wrap; line-height: 1.4;">${escapeHtml(item.social_caption)}</span>
              <button type="button" class="quiet-button copy-caption-btn" data-caption="${escapeHtml(item.social_caption)}" style="padding: 2px 8px; font-size: 11px; color: #10b981; white-space: nowrap; border: 1px solid rgba(16, 185, 129, 0.4);">📋 Copy Caption</button>
            </div>
          ` : '';

          div.innerHTML = item.url
            ? `<div style="display:flex; justify-content:space-between; align-items:center;">
                 <div>
                   <a href="${escapeHtml(item.url)}" download style="font-weight:600; color:var(--accent-color, #10b981);">↓ Download ${escapeHtml(item.title || item.filename)}</a>
                   <small class="muted">(${Number(item.duration||0).toFixed(1)}s · ${item.width||'?'}×${item.height||'?'})</small>
                 </div>
                 <span style="color:#10b981; font-size:12px;">✓ Ready</span>
               </div>${captionHtml}`
            : `<p class="error-text">Clip ${escapeHtml(item.clip_id)}: ${escapeHtml(item.error)}</p>`;
          streamContainer.appendChild(div);
          div.querySelectorAll('.copy-caption-btn').forEach(btn => {
            btn.addEventListener('click', async (e) => {
              e.stopPropagation();
              await navigator.clipboard.writeText(btn.dataset.caption || '');
              showToast('✓ Caption copied to clipboard!');
              btn.textContent = '✓ Copied!';
              setTimeout(() => { btn.textContent = '📋 Copy Caption'; }, 2000);
            });
          });
        }
      } catch (clipErr) {
        const div = document.createElement('div');
        div.innerHTML = `<p class="error-text">Failed to export ${escapeHtml(clip.title || clip.id)}: ${escapeHtml(clipErr.message)}</p>`;
        streamContainer.appendChild(div);
      }
    }
    showToast(`${successCount} of ${selectedClips.length} clip(s) exported successfully!`);
    await refreshSelectedJob();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.innerHTML = 'Export selected <span>→</span>';
  }
}


async function loadSettings() {
  try {
    const settings = await api('/api/settings');
    $('#llm-model').value = settings.llm_model || 'openrouter/auto';
    $('#vision-model').value = settings.vision_model || '';
    $('#vision-enabled').checked = !!settings.vision_enabled;
    $('#send-transcript').checked = !!settings.send_transcript_to_ai;
    $('#send-frames').checked = !!settings.send_video_frames_to_ai;
    $('#transcription-provider').value = settings.transcription_provider || 'openrouter';
    $('#transcription-model').value = settings.transcription_model || 'whisper-large-v3-turbo';
    $('#transcription-enabled').checked = !!settings.transcription_enabled;
    setProviderStatus('openrouter-status', settings.credentials?.openrouter?.configured, settings.credentials?.openrouter?.source);
    const speechCredential = settings.transcription_provider === 'openrouter' ? 'openrouter' : settings.transcription_provider;
    setProviderStatus('transcription-status', settings.credentials?.[speechCredential]?.configured, settings.credentials?.[speechCredential]?.source);
    updateTranscriptionProviderUI(settings.transcription_provider);
    $('#settings-save-state').textContent = 'Preferences loaded · API keys remain hidden';
  } catch (error) { showToast(error.message); }
}

function setProviderStatus(id, configured, source) {
  const node = document.getElementById(id);
  node.textContent = configured ? (source === 'environment' ? 'Environment key' : 'Connected') : 'Not connected';
  node.className = 'provider-status' + (configured ? ' connected' : '');
}

function updateTranscriptionProviderUI(provider) {
  const shared = provider === 'openrouter';
  $('#transcription-openrouter-note').classList.toggle('hidden', !shared);
  $('#transcription-external-key').classList.toggle('hidden', shared || provider === 'disabled');
  $('#transcription-model').value = $('#transcription-model').value || (provider === 'openrouter' ? 'openai/whisper-large-v3-turbo' : provider === 'openai' ? 'gpt-transcribe' : 'whisper-large-v3-turbo');
}

async function saveProviderKey(provider, inputSelector, statusId) {
  const value = $(inputSelector).value.trim();
  if (!value) return showToast('Paste the API key first.');
  try {
    await api(`/api/settings/keys/${provider}`, {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({api_key:value})});
    $(inputSelector).value = '';
    setProviderStatus(statusId, true, 'os-vault');
    showToast(`${provider} key saved in your operating-system credential vault.`);
    if (provider === 'openrouter') await loadOpenRouterModels(false);
  } catch (error) { showToast(error.message); }
}

async function testProvider(provider) {
  const button = provider === 'openrouter' ? $('#test-openrouter') : $('#test-transcription');
  const label = button.textContent; button.disabled = true; button.textContent = 'Testing…';
  try {
    const result = await api(`/api/settings/test/${provider}`, {method:'POST'});
    showToast(result.message || 'Connection successful.');
    if (provider === 'openrouter') setProviderStatus('openrouter-status', true, 'os-vault');
    else setProviderStatus('transcription-status', true, 'os-vault');
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}

async function removeProviderKey(provider, statusId) {
  try {
    await api(`/api/settings/keys/${provider}`, {method:'DELETE'});
    setProviderStatus(statusId, false, 'none');
    showToast(`${provider} key removed from the operating-system credential vault.`);
  } catch (error) { showToast(error.message); }
}

async function loadOpenRouterModels(vision) {
  const button = vision ? $('#load-vision-models') : $('#load-text-models');
  const target = vision ? $('#vision-model') : $('#llm-model');
  const current = target.value;
  const label = button.textContent; button.disabled = true; button.textContent = 'Loading…';
  try {
    const data = await api(`/api/settings/models?vision=${vision ? 'true' : 'false'}`);
    const models = data.models || [];
    target.innerHTML = vision ? '<option value="">Use AI ranking model</option>' : '';
    if (!vision && !models.some(model => model.id === 'openrouter/auto')) {
      target.add(new Option('OpenRouter Auto · openrouter/auto', 'openrouter/auto'));
    }
    for (const model of models) {
      const modalities = model.vision ? ' · vision' : '';
      const context = model.context_length ? ` · ${Math.round(model.context_length / 1000)}k ctx` : '';
      target.add(new Option(`${model.name}${modalities}${context}`, model.id));
    }
    if (current && !Array.from(target.options).some(option => option.value === current)) {
      target.add(new Option(`${current} · saved selection`, current));
    }
    target.value = current || (vision ? '' : 'openrouter/auto');
    showToast(`${models.length} ${vision ? 'vision ' : ''}models loaded.`);
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}

async function testTranscriptionProvider() {
  const button = $('#test-transcription'); const label = button.textContent;
  button.disabled = true; button.textContent = 'Testing…';
  try {
    const result = await api('/api/settings/test-transcription', {method:'POST'});
    showToast(result.message || 'Transcription provider connected.');
    const settings = await api('/api/settings');
    const provider = settings.transcription_provider === 'openrouter' ? 'openrouter' : settings.transcription_provider;
    setProviderStatus('transcription-status', settings.credentials?.[provider]?.configured, settings.credentials?.[provider]?.source);
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}

async function loadTranscriptionModels() {
  const button = $('#load-transcription-models'); const label = button.textContent;
  button.disabled = true; button.textContent = 'Loading…';
  try {
    const data = await api('/api/settings/transcription-models');
    const list = $('#transcription-models'); list.innerHTML = '';
    for (const model of (data.models || [])) list.append(new Option(`${model.name} · ${model.id}`, model.id));
    if (!$('#transcription-model').value && data.models?.length) $('#transcription-model').value = data.models[0].id;
    showToast(`${data.models?.length || 0} ${data.provider} transcription models loaded.`);
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; button.textContent = label; }
}

async function savePreferences() {
  const button = $('#save-preferences'); button.disabled = true;
  try {
    const payload = {
      llm_provider: 'openrouter',
      llm_model: $('#llm-model').value || 'openrouter/auto',
      vision_model: $('#vision-model').value,
      vision_enabled: $('#vision-enabled').checked,
      transcription_provider: $('#transcription-provider').value,
      transcription_model: $('#transcription-model').value.trim() || ($('#transcription-provider').value === 'openai' ? 'gpt-transcribe' : $('#transcription-provider').value === 'openrouter' ? 'openai/whisper-large-v3-turbo' : 'whisper-large-v3-turbo'),
      transcription_enabled: $('#transcription-enabled').checked,
      max_ai_candidates: 12,
      send_transcript_to_ai: $('#send-transcript').checked,
      send_video_frames_to_ai: $('#send-frames').checked
    };
    await api('/api/settings/preferences', {method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    $('#settings-save-state').textContent = 'Saved successfully';
    showToast('AI preferences saved. New and retried jobs will use these settings.');
    await loadSettings();
  } catch (error) { showToast(error.message); }
  finally { button.disabled = false; }
}

function showSettings(show) {
  $('#settings-panel').classList.toggle('hidden', !show);
  $('#create-panel').classList.toggle('hidden', show);
  $('#nav-settings').classList.toggle('active', show);
  $('#nav-create').classList.toggle('active', !show);
  if (show) {
    loadSettings();
    $('#settings-panel').scrollIntoView({behavior:'smooth', block:'start'});
  }
}

async function runRediscoverMoments() {
  if (!currentJobId) return showToast('Select a project first.');
  validateAndSyncDuration(true);
  const mode = $('#active-duration-mode').value || 'exact';
  const exact = Math.round(Number($('#active-exact-seconds').value || 60));
  let min = Math.round(Number($('#active-min-seconds').value || 30));
  let max = Math.round(Number($('#active-max-seconds').value || 60));
  if (mode === 'range') {
    if (min < 5) min = 5;
    if (max > 600) max = 600;
    if (min > max) { const tmp = min; min = max; max = tmp; }
  }
  const btns = [$('#rediscover-btn'), $('#apply-to-active-btn'), $('#update-brief-btn')].filter(Boolean);
  btns.forEach(b => { b.disabled = true; b.textContent = 'Discovering moments…'; });
  try {
    const activeBrief = ($('#active-campaign-brief')?.value || $('#campaign-brief')?.value || '').trim();
    const payload = {
      duration_mode: mode,
      exact_seconds: exact,
      min_seconds: min,
      max_seconds: max,
      editing_intelligence: currentJobOptions.editing_intelligence || 'balanced',
      campaign_brief: activeBrief,
      logo_path: uploadedLogoPath || currentJobOptions.logo_path || '',
      logo_position: $('#logo-position')?.value || currentJobOptions.logo_position || 'top-right',
      logo_scale: Number($('#logo-scale')?.value || currentJobOptions.logo_scale || 0.20),
      logo_opacity: Number($('#logo-opacity')?.value || currentJobOptions.logo_opacity || 0.90)
    };
    const res = await api(`/api/jobs/${currentJobId}/rediscover`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload)
    });
    currentJobOptions = res.options;
    clipsCache = res.clips || [];
    renderClips(clipsCache);
    showToast(`Found ${res.count} moments matching brief and duration!`);
    await refreshSelectedJob();
    $('#detail-panel')?.scrollIntoView({behavior:'smooth', block:'start'});
  } catch (err) {
    showToast(err.message || 'Failed to rediscover moments');
  } finally {
    btns.forEach(b => {
      b.disabled = false;
      if (b.id === 'rediscover-btn') b.innerHTML = '⚡ Re-discover Moments';
      if (b.id === 'apply-to-active-btn') b.innerHTML = '⚡ Apply Duration to Selected Project &amp; Re-discover';
      if (b.id === 'update-brief-btn') b.innerHTML = '🎯 Apply Brief &amp; Re-discover';
    });
  }
}

function bindEvents() {
  $('#video-file').addEventListener('change', event => uploadFile(event.target.files?.[0]));
  $('#url-submit').addEventListener('click', submitUrl);
  $('#duration-mode').addEventListener('change', () => validateAndSyncDuration(true));
  ['clip-exact-seconds', 'clip-min-seconds', 'clip-max-seconds'].forEach(id => {
    $('#' + id)?.addEventListener('input', () => validateAndSyncDuration(true));
    $('#' + id)?.addEventListener('change', () => validateAndSyncDuration(true));
  });
  $('#active-duration-mode')?.addEventListener('change', () => validateAndSyncDuration(false));
  ['active-exact-seconds', 'active-min-seconds', 'active-max-seconds'].forEach(id => {
    $('#' + id)?.addEventListener('input', () => validateAndSyncDuration(false));
    $('#' + id)?.addEventListener('change', () => validateAndSyncDuration(false));
  });
  $('#rediscover-btn')?.addEventListener('click', runRediscoverMoments);
  $('#apply-to-active-btn')?.addEventListener('click', runRediscoverMoments);
  $('#update-brief-btn')?.addEventListener('click', runRediscoverMoments);

  $('#logo-browse-btn')?.addEventListener('click', () => $('#logo-upload-input')?.click());
  $('#logo-upload-input')?.addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const form = new FormData();
    form.append('file', file);
    try {
      showToast('Uploading brand logo…');
      const res = await api(currentJobId ? `/api/jobs/${currentJobId}/logo` : '/api/branding/logo', {
        method: 'POST',
        body: form
      });
      uploadedLogoPath = res.logo_path || (currentJobId ? `branding/${currentJobId}_logo` : '');
      const previewImg = $('#logo-preview-img');
      if (previewImg) previewImg.src = res.logo_url || `/api/branding/file/${res.filename}`;
      const nameSpan = $('#logo-filename');
      if (nameSpan) nameSpan.textContent = res.filename;
      $('#logo-preview-box')?.classList.remove('hidden');
      $('#logo-remove-btn')?.classList.remove('hidden');
      showToast('Brand logo ready.');
    } catch (err) {
      showToast(err.message || 'Logo upload failed');
    }
  });

  $('#logo-remove-btn')?.addEventListener('click', async () => {
    uploadedLogoPath = '';
    if ($('#logo-upload-input')) $('#logo-upload-input').value = '';
    $('#logo-preview-box')?.classList.add('hidden');
    $('#logo-remove-btn')?.classList.add('hidden');
    if (currentJobId) {
      try { await api(`/api/jobs/${currentJobId}/logo`, { method: 'DELETE' }); } catch (_) {}
    }
    showToast('Brand logo removed.');
  });
  $('#video-url').addEventListener('keydown', event => { if (event.key === 'Enter') submitUrl(); });
  $('#refresh-btn').addEventListener('click', () => { refreshJobs(); loadStorageUsage(); });
  $('#open-storage-btn')?.addEventListener('click', openStorageModal);
  $('#nav-storage')?.addEventListener('click', openStorageModal);
  $('#close-storage-modal')?.addEventListener('click', closeStorageModal);
  $('#storage-modal')?.addEventListener('click', (e) => {
    if (e.target === $('#storage-modal')) closeStorageModal();
  });
  $('#btn-clear-all-sources')?.addEventListener('click', async () => {
    if (!confirm('Are you sure you want to delete ALL downloaded and raw source videos from your laptop? (Your candidate clips and exported shorts will be kept.)')) return;
    try {
      $('#btn-clear-all-sources').disabled = true;
      $('#btn-clear-all-sources').textContent = 'Freeing space…';
      const res = await api('/api/storage/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: 'sources' }),
      });
      showToast(`✓ Successfully freed ${res.freed_formatted} from your laptop!`);
      await loadStorageUsage();
      await refreshJobs();
    } catch (err) {
      showToast(`Error: ${err.message}`);
    } finally {
      $('#btn-clear-all-sources').disabled = false;
      $('#btn-clear-all-sources').textContent = 'Free Up Raw Sources';
    }
  });
  $('#btn-clear-all-work')?.addEventListener('click', async () => {
    if (!confirm('Clear all temporary audio waveforms, transcripts, and sampled frames from data/work/?')) return;
    try {
      $('#btn-clear-all-work').disabled = true;
      $('#btn-clear-all-work').textContent = 'Clearing cache…';
      const res = await api('/api/storage/clear', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target: 'work' }),
      });
      showToast(`✓ Successfully freed ${res.freed_formatted} of render cache!`);
      await loadStorageUsage();
    } catch (err) {
      showToast(`Error: ${err.message}`);
    } finally {
      $('#btn-clear-all-work').disabled = false;
      $('#btn-clear-all-work').textContent = 'Clear Render Cache';
    }
  });
  $('#see-all').addEventListener('click', () => $('#jobs-list').scrollIntoView({behavior:'smooth'}));
  $('#nav-create').addEventListener('click', () => showSettings(false));
  $('#nav-settings').addEventListener('click', () => showSettings(true));
  $('#nav-library').addEventListener('click', () => { showSettings(false); $('#jobs-list').scrollIntoView({behavior:'smooth'}); });
  $('#select-all').addEventListener('click', async () => setAllSelection(true));
  $('#select-none').addEventListener('click', async () => setAllSelection(false));
  $('#export-btn').addEventListener('click', exportSelected);
  $('#close-editor').addEventListener('click', () => $('#editor-panel').classList.add('hidden'));
  $('#preview-play').addEventListener('click', () => { const video=$('#editor-video'); if (video.paused) video.play(); else video.pause(); });
  $('#jump-in').addEventListener('click', () => { $('#editor-video').currentTime=Number($('#edit-start').value||0); updateEditorTimeline(); });
  $('#jump-out').addEventListener('click', () => { $('#editor-video').currentTime=Number($('#edit-end').value||0); updateEditorTimeline(); });
  $('#split-at-playhead').addEventListener('click', async () => {
    if (!currentEditingClip) return;
    const splitAt = Number($('#editor-video').currentTime || 0);
    try {
      await api(`/api/jobs/${currentJobId}/clips/${currentEditingClip.id}/split`, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({split_at:splitAt})});
      $('#editor-panel').classList.add('hidden'); currentEditingClip = null;
      await refreshSelectedJob(); showToast('Clip split into two editable parts.');
    } catch (error) { showToast(error.message); }
  });
  $('#edit-start').addEventListener('change', () => setClipInOut('start',$('#edit-start').value));
  $('#edit-end').addEventListener('change', () => setClipInOut('end',$('#edit-end').value));
  $('#playhead-range').addEventListener('input', () => { $('#editor-video').currentTime=Number($('#playhead-range').value); updateEditorTimeline(); });
  $('#editor-video').addEventListener('timeupdate', () => updateEditorTimeline());
  $('#editor-video').addEventListener('ended', () => updateEditorTimeline());
  ['edit-aspect','edit-framing-mode','focus-x','focus-y','edit-zoom','edit-speed','edit-volume','edit-brightness','edit-contrast','edit-saturation'].forEach(id => $('#'+id).addEventListener('input', () => { updateEditorPreview(); updateEditorTimeline(); }));
  document.querySelectorAll('[data-focus]').forEach(button => button.addEventListener('click', () => {
    const preset=button.dataset.focus; $('#focus-x').value=preset==='left'?0:preset==='right'?1:0.5;
    document.querySelectorAll('[data-focus]').forEach(item=>item.classList.toggle('active',item===button)); updateEditorPreview();
  }));
  $('#reset-edit').addEventListener('click', resetEditorControls);
  $('#render-edit').addEventListener('click', renderEditedClip);
  const aiAssistBtn = $('#ai-assist-btn');
  if (aiAssistBtn) {
    aiAssistBtn.addEventListener('click', async () => {
      const input = $('#ai-assist-input');
      const command = (input?.value || '').trim();
      if (!command || !currentEditingClip || !currentJobId) return;
      aiAssistBtn.disabled = true;
      aiAssistBtn.textContent = '...';
      const feedback = $('#ai-assist-feedback');
      try {
        const current = {
          start: Number($('#edit-start').value || currentEditingClip.start),
          end: Number($('#edit-end').value || currentEditingClip.end),
          aspect_ratio: $('#edit-aspect').value,
          framing_mode: $('#edit-framing-mode').value,
          focus_x: Number($('#focus-x').value),
          focus_y: Number($('#focus-y').value),
          zoom: Number($('#edit-zoom').value),
          speed: Number($('#edit-speed').value),
          volume: Number($('#edit-volume').value),
          fade_in: Number($('#fade-in').value),
          fade_out: Number($('#fade-out').value),
          brightness: Number($('#edit-brightness').value),
          contrast: Number($('#edit-contrast').value),
          saturation: Number($('#edit-saturation').value),
          preserve_audio: $('#preserve-audio').checked,
          normalize_audio: $('#normalize-audio').checked,
          captions_enabled: $('#captions-enabled').checked,
          caption_style: $('#caption-style').value,
          caption_position: $('#caption-position').value,
          font_size: Number($('#caption-size').value),
          title_overlay: $('#title-overlay').value
        };
        const res = await api(`/api/jobs/${currentJobId}/clips/${currentEditingClip.id}/ai-assist`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({command, current_settings: current})
        });
        if (res.settings) {
          applySettingsToEditor(res.settings);
          if (feedback) {
            feedback.classList.remove('hidden');
            feedback.textContent = (res.applied_changes || []).join(' ');
          }
          showToast('AI edit applied to timeline.');
        }
      } catch (err) {
        showToast(err.message || 'AI Assistant failed');
      } finally {
        aiAssistBtn.disabled = false;
        aiAssistBtn.textContent = 'Apply';
      }
    });
    $('#ai-assist-input')?.addEventListener('keydown', event => { if (event.key === 'Enter') aiAssistBtn.click(); });
  }
  $('#save-openrouter-key').addEventListener('click', () => saveProviderKey('openrouter', '#openrouter-key', 'openrouter-status'));
  $('#test-openrouter').addEventListener('click', () => testProvider('openrouter'));
  $('#delete-openrouter-key').addEventListener('click', () => removeProviderKey('openrouter', 'openrouter-status'));
  $('#save-transcription-key').addEventListener('click', () => saveProviderKey($('#transcription-provider').value === 'openai' ? 'openai' : 'groq', '#transcription-key', 'transcription-status'));
  $('#test-transcription').addEventListener('click', testTranscriptionProvider);
  $('#load-transcription-models').addEventListener('click', loadTranscriptionModels);
  $('#delete-transcription-key').addEventListener('click', () => removeProviderKey($('#transcription-provider').value === 'openai' ? 'openai' : 'groq', 'transcription-status'));
  $('#load-text-models').addEventListener('click', () => loadOpenRouterModels(false));
  $('#load-vision-models').addEventListener('click', () => loadOpenRouterModels(true));
  $('#save-preferences').addEventListener('click', savePreferences);
  $('#transcription-provider').addEventListener('change', () => {
    const provider = $('#transcription-provider').value;
    $('#transcription-model').value = provider === 'openai' ? 'gpt-transcribe' : provider === 'openrouter' ? 'openai/whisper-large-v3-turbo' : 'whisper-large-v3-turbo';
    updateTranscriptionProviderUI(provider);
    api('/api/settings').then(settings => {
      const credential = provider === 'openrouter' ? 'openrouter' : provider;
      setProviderStatus('transcription-status', settings.credentials?.[credential]?.configured, settings.credentials?.[credential]?.source);
    }).catch(() => setProviderStatus('transcription-status', false, 'none'));
  });
  const dropzone = $('#dropzone');
  for (const name of ['dragenter','dragover']) dropzone.addEventListener(name, event => {event.preventDefault();dropzone.classList.add('dragging');});
  for (const name of ['dragleave','drop']) dropzone.addEventListener(name, event => {event.preventDefault();dropzone.classList.remove('dragging');});
  dropzone.addEventListener('drop', event => uploadFile(event.dataTransfer.files?.[0]));

  // Quick Duration Preset Pills
  document.querySelectorAll('.duration-pill').forEach(btn => btn.addEventListener('click', () => {
    document.querySelectorAll('.duration-pill').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const preset = btn.dataset.preset;
    if (preset === 'custom') {
      $('#duration-mode').value = 'range';
    } else {
      $('#duration-mode').value = 'exact';
      $('#clip-exact-seconds').value = preset;
    }
    validateAndSyncDuration(true);
  }));

  // Viral Formula Quick Templates
  const formulaTemplates = {
    twitch: "Target high-energy climax (yelling, shock, peak audio) and hold camera on streamer for 1.5s post-event for full reaction. Use dynamic punch-in 1.15x. Required caption: 'Follow for daily stream highlights!'. Tags: #Gaming #TwitchClips #Streamer",
    epiphany: "Hook in first 2 seconds with a counterintuitive statement ('Most people think X, but actually Y'). Fast visual cut into clear breakdown, ending with punchy takeaway. Required tags: #Mindset #Insight #Knowledge",
    story: "Start mid-action or mid-confession ('I was completely wrong about...'). Maintain continuous narrative tension, reveal turning point, and finish on moral/lesson. Tags: #Storytime #Confession #Lessons"
  };
  document.querySelectorAll('.brief-formula-tag').forEach(btn => btn.addEventListener('click', () => {
    const formula = btn.dataset.formula;
    const text = formulaTemplates[formula];
    if (!text) return;
    if ($('#campaign-brief')) $('#campaign-brief').value = text;
    if ($('#active-campaign-brief')) $('#active-campaign-brief').value = text;
    showToast('✓ Viral formula loaded into brief!');
  }));

  // URL Clipboard Paste Button
  $('#paste-url-btn')?.addEventListener('click', async () => {
    try {
      const text = (await navigator.clipboard.readText() || '').trim();
      if (text.startsWith('http://') || text.startsWith('https://')) {
        $('#video-url').value = text;
        showToast('✓ URL pasted from clipboard!');
      } else {
        showToast('Clipboard does not contain a valid URL.');
      }
    } catch (_) {
      showToast('Clipboard access denied or unavailable.');
    }
  });

  // Safe Zone Mobile Guides Overlay Toggle
  $('#toggle-safe-zone')?.addEventListener('click', () => {
    const overlay = $('#safe-zone-overlay');
    const btn = $('#toggle-safe-zone');
    if (!overlay || !btn) return;
    const isHidden = overlay.classList.toggle('hidden');
    btn.classList.toggle('active', !isHidden);
    btn.textContent = isHidden ? '📱 Safe Zone Guides' : '✓ Safe Zone Visible';
  });

  // AI Assistant Quick Action Chips
  document.querySelectorAll('.ai-prompt-chip').forEach(btn => btn.addEventListener('click', () => {
    const prompt = btn.dataset.ai;
    const input = $('#ai-assist-input');
    if (!prompt || !input) return;
    input.value = prompt;
    $('#ai-assist-btn')?.click();
  }));
}

async function setAllSelection(selected) {
  if (!currentJobId) return;
  await Promise.all(clipsCache.map(clip => api(`/api/jobs/${currentJobId}/clips/${clip.id}`,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected})})));
  clipsCache.forEach(clip => clip.selected = selected); renderClips(clipsCache);
}

bindEvents();
updateDurationControls();
api('/api/health').then(status => {
  $('#ffmpeg-state').textContent = status.ffmpeg && status.ffprobe ? 'Detected' : 'Not found';
  $('#ffmpeg-state').className = status.ffmpeg && status.ffprobe ? 'good' : 'checking';
  const downloaderReady = Boolean(status.yt_dlp_available);
  $('#ytdlp-state').textContent = downloaderReady
    ? `v${status.yt_dlp_version || 'installed'}${status.youtube_js_runtime ? ` · ${status.youtube_js_runtime}` : ' · no JS runtime'}`
    : 'Not installed';
  $('#ytdlp-state').className = downloaderReady && status.youtube_js_runtime ? 'good' : 'optional';
  $('#openrouter-health').textContent = status.openrouter_configured ? 'Configured' : 'Not configured';
  $('#openrouter-health').className = status.openrouter_configured ? 'good' : 'optional';
  let visionLabel = 'Not configured';
  if (status.vision_model_configured) {
    if (status.active_vision_model && status.active_vision_model.includes('nemotron')) {
      visionLabel = 'Nemotron 120B VL (Auto)';
    } else if (status.active_vision_model && status.active_vision_model.includes('gpt-4o')) {
      visionLabel = 'GPT-4o Vision (Auto)';
    } else if (status.active_vision_model) {
      visionLabel = `${status.active_vision_model.split('/').pop()} (Auto)`;
    } else {
      visionLabel = 'Auto-Optimized';
    }
  }
  $('#vision-model-health').textContent = visionLabel;
  $('#vision-model-health').className = status.vision_model_configured ? 'good' : 'optional';
  $('#speech-health').textContent = status.transcription_configured ? `${status.transcription_provider} ready` : 'Not configured';
  $('#speech-health').className = status.transcription_configured ? 'good' : 'optional';
  if (status.openrouter_configured && $('#studio-engine-badge')) {
    const speech = status.transcription_configured ? (status.transcription_provider === 'groq' ? 'Groq Whisper LPU' : 'Whisper') : 'Speech AI';
    $('#studio-engine-badge').innerHTML = `<span class="engine-dot"></span> NVIDIA Nemotron 550B &amp; ${speech} active`;
  }
}).catch(() => { $('#ffmpeg-state').textContent = 'Unavailable'; });
loadSettings();
refreshJobs();
loadStorageUsage();
