// ========================================
// Global State
// ========================================
let currentPRs = [];
let currentPage = 1;
let totalResults = 0;

// Track retested jobs: Map<"owner/repo/pr/jobName", {startTime, pollInterval}>
const retestedJobs = new Map();
const POLL_INTERVAL = 5000; // 5 seconds
const MAX_POLL_TIME = 5 * 60 * 1000; // 5 minutes

// Permafail detection thresholds
const MAX_AUTO_RETEST_FAILURES = 2; // Auto-retest up to 2 consecutive failures
const PERMAFAIL_CHECK_THRESHOLD = 3; // Check for permafail on 3rd consecutive failure

// Permafail tracking
const permafailJobs = new Map(); // jobKey -> {permafail: bool, reason: str, override: bool}

// Context menu tracking
let contextMenuTarget = null;

// DOM element cache
const DOM = {
    searchInput: null,
    searchBtn: null,
    refreshBtn: null,
    loadMoreBtn: null,
    loadMoreText: null,
    authBanner: null,
    prContainer: null,
    toastContainer: null
};

// ========================================
// Initialization
// ========================================
document.addEventListener('DOMContentLoaded', async () => {
    await init();
});

async function init() {
    // Cache DOM elements
    DOM.searchInput = document.getElementById('search-input');
    DOM.searchBtn = document.getElementById('search-btn');
    DOM.refreshBtn = document.getElementById('refresh-btn');
    DOM.loadMoreBtn = document.getElementById('load-more-btn');
    DOM.loadMoreText = document.getElementById('load-more-text');
    DOM.authBanner = document.getElementById('auth-banner');
    DOM.prContainer = document.getElementById('pr-cards-container');
    DOM.toastContainer = document.getElementById('toast-container');

    // Check auth status
    const authStatus = await checkAuth();
    if (!authStatus.authenticated) {
        showAuthBanner(authStatus.error);
    }

    // Load default query
    const defaultQuery = await fetch('/api/default-query').then(r => r.json());
    DOM.searchInput.value = defaultQuery.query;

    // Auto-execute search
    await executeSearch(defaultQuery.query);

    // Set up event listeners
    DOM.searchBtn.addEventListener('click', () => {
        currentPage = 1;
        DOM.prContainer.innerHTML = '';
        executeSearch(DOM.searchInput.value);
    });
    DOM.searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            currentPage = 1;
            DOM.prContainer.innerHTML = '';
            executeSearch(DOM.searchInput.value);
        }
    });
    DOM.refreshBtn.addEventListener('click', () => {
        currentPage = 1;
        DOM.prContainer.innerHTML = '';
        executeSearch(DOM.searchInput.value);
    });
    DOM.loadMoreBtn.addEventListener('click', () => {
        currentPage++;
        executeSearch(DOM.searchInput.value, true);
    });

    // Context menu event listeners
    document.getElementById('clearPermafailItem').addEventListener('click', handleClearPermafail);
    document.addEventListener('click', hideContextMenu);

    // Check for Permafail button event delegation
    document.addEventListener('click', async (e) => {
        if (e.target.classList.contains('check-permafail-btn')) {
            const jobElement = e.target.closest('.job-item');
            await manualPermafailCheck(jobElement, e.target);
        }
    });
}

async function checkAuth() {
    try {
        const response = await fetch('/api/auth/status');
        return await response.json();
    } catch (error) {
        console.error('Auth check failed:', error);
        return { authenticated: false, error: 'Failed to check authentication status' };
    }
}

// ========================================
// Utility Helpers
// ========================================
function createElement(tag, className, textContent, attributes = {}) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (textContent) el.textContent = textContent;
    Object.entries(attributes).forEach(([key, value]) => el[key] = value);
    return el;
}

function extractJobNames(section) {
    return Array.from(section.querySelectorAll('.job-item'))
        .map(item => item.querySelector('.job-name').textContent.match(/❌ (.+?) \(/)?.[1])
        .filter(Boolean);
}

function getAge(createdAt) {
    const created = new Date(createdAt);
    const now = new Date();
    const diffDays = Math.floor((now - created) / (1000 * 60 * 60 * 24));
    if (diffDays === 0) return 'today';
    if (diffDays === 1) return '1 day old';
    return `${diffDays} days old`;
}

function isJobRetesting(owner, repo, number, jobName) {
    const jobKey = `${owner}/${repo}/${number}/${jobName}`;
    return retestedJobs.has(jobKey);
}

function renderPermafailIcon(jobElement, reason) {
    const jobHeader = jobElement.querySelector('.job-name') || jobElement;

    // Remove existing icon if present
    const existing = jobElement.querySelector('.permafail-icon');
    if (existing) existing.remove();

    // Add dumpster fire icon
    const icon = document.createElement('img');
    icon.src = '/static/dumpster-fire.svg';
    icon.className = 'permafail-icon';
    icon.alt = 'Permafail detected';
    icon.title = reason;
    jobHeader.appendChild(icon);

    // Add warning banner
    const warning = document.createElement('div');
    warning.className = 'permafail-warning';
    warning.textContent = `Permafail: ${reason}`;
    jobElement.appendChild(warning);

    // Disable retest button
    const retestBtn = jobElement.querySelector('.job-actions button.btn:not(.btn-secondary)');
    if (retestBtn) {
        retestBtn.disabled = true;
    }
}

function clearPermafailUI(jobElement, jobKey) {
    // Remove icon
    const icon = jobElement.querySelector('.permafail-icon');
    if (icon) icon.remove();

    // Remove warning
    const warning = jobElement.querySelector('.permafail-warning');
    if (warning) warning.remove();

    // Re-enable retest button
    const retestBtn = jobElement.querySelector('.job-actions button.btn:not(.btn-secondary)');
    if (retestBtn) {
        retestBtn.disabled = false;
    }

    // Update state
    permafailJobs.delete(jobKey);
}

// ========================================
// Context Menu
// ========================================
function showContextMenu(event, jobElement, jobKey) {
    event.preventDefault();

    const menu = document.getElementById('contextMenu');

    // Only show menu if job has permafail
    if (!permafailJobs.has(jobKey)) {
        return;
    }

    contextMenuTarget = { jobElement, jobKey };

    // Position menu at click location
    menu.style.left = event.pageX + 'px';
    menu.style.top = event.pageY + 'px';
    menu.style.display = 'block';
}

function hideContextMenu() {
    const menu = document.getElementById('contextMenu');
    menu.style.display = 'none';
    contextMenuTarget = null;
}

async function handleClearPermafail() {
    if (!contextMenuTarget) return;

    const { jobElement, jobKey } = contextMenuTarget;
    const jobUrl = jobElement.dataset.jobUrl;

    if (!jobUrl) {
        console.error('No job URL found on element');
        hideContextMenu();
        return;
    }

    try {
        const response = await fetch('/api/jobs/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_url: jobUrl })
        });

        if (response.ok) {
            clearPermafailUI(jobElement, jobKey);
            showToast('Permafail cleared successfully', 'success');
        } else {
            const error = await response.json();
            showToast('Failed to clear permafail: ' + (error.error || 'Unknown error'), 'error');
        }
    } catch (error) {
        console.error('Failed to clear permafail:', error);
        showToast('Failed to clear permafail: ' + error.message, 'error');
    }

    hideContextMenu();
}

function attachJobCardEvents(jobElement, jobKey) {
    // Add right-click handler
    jobElement.addEventListener('contextmenu', (e) => {
        showContextMenu(e, jobElement, jobKey);
    });
}

// ========================================
// Search & PR Rendering
// ========================================
async function executeSearch(query, append = false) {
    if (!append) {
        showLoading('Searching PRs...');
    }

    try {
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, page: currentPage, per_page: 30 })
        });

        const data = await response.json();

        if (data.error) {
            showToast(data.error, 'error');
            hideLoading();
            return;
        }

        if (append) {
            currentPRs = [...currentPRs, ...data.prs];
        } else {
            currentPRs = data.prs;
        }
        totalResults = data.total;

        hideLoading();
        renderPRCards(data.prs, append);
        updateLoadMoreButton();
    } catch (error) {
        console.error('Search failed:', error);
        showToast('Search failed: ' + error.message, 'error');
        hideLoading();
    }
}

function renderPRCards(prs, append = false) {
    if (!append && prs.length === 0) {
        DOM.prContainer.innerHTML = '<div class="loading">No PRs found</div>';
        return;
    }

    prs.forEach(pr => {
        const card = createPRCard(pr);
        DOM.prContainer.appendChild(card);
        loadPRJobs(pr.owner, pr.repo, pr.number, card);
    });
}

function updateLoadMoreButton() {
    const currentCount = currentPRs.length;
    const hasMore = currentCount < totalResults;
    const remaining = totalResults - currentCount;

    DOM.loadMoreText.textContent = `showing ${currentCount} of ${totalResults} (${remaining} more)`;

    if (hasMore) {
        DOM.loadMoreBtn.classList.remove('hidden');
    } else {
        DOM.loadMoreBtn.classList.add('hidden');
    }
}

// ========================================
// DOM Helpers - PR Card Creation
// ========================================
function createPRCard(pr) {
    const card = createElement('div', 'pr-card');
    card.id = `pr-${pr.owner}-${pr.repo}-${pr.number}`;

    // PR Header
    const prHeader = createElement('div', 'pr-header');
    const prTitle = createElement('div', 'pr-title');

    const prLink = createElement('a', '', `#${pr.number}`, {
        href: `https://github.com/${pr.owner}/${pr.repo}/pull/${pr.number}`,
        target: '_blank'
    });
    prLink.style.color = 'var(--primary)';
    prLink.style.textDecoration = 'none';
    prLink.style.fontWeight = 'bold';

    prTitle.appendChild(prLink);
    prTitle.appendChild(document.createTextNode(` - ${pr.title}`));

    const prMeta = createElement('div', 'pr-meta', `${pr.owner}/${pr.repo} • ${pr.author} • ${getAge(pr.created_at)}`);

    prHeader.appendChild(prTitle);
    prHeader.appendChild(prMeta);

    // Job sections container
    const jobSectionsContainer = createElement('div', 'job-sections-container');
    jobSectionsContainer.appendChild(createJobSectionPlaceholder('e2e', pr.owner, pr.repo, pr.number));
    jobSectionsContainer.appendChild(createJobSectionPlaceholder('payload', pr.owner, pr.repo, pr.number));

    card.appendChild(prHeader);
    card.appendChild(jobSectionsContainer);

    return card;
}

function createJobSectionPlaceholder(type, owner, repo, number) {
    const section = createElement('div', 'job-section');
    section.id = `${type}-${owner}-${repo}-${number}`;

    const header = createElement('div', 'job-section-header', `▶ ${type.toUpperCase()} Jobs (loading...)`);
    const list = createElement('div', 'job-list');

    section.appendChild(header);
    section.appendChild(list);

    return section;
}

// ========================================
// Job Loading & Rendering
// ========================================
async function loadPRJobs(owner, repo, number, cardElement) {
    try {
        const response = await fetch(`/api/pr/${owner}/${repo}/${number}`);
        const data = await response.json();
        updateCardWithJobs(cardElement, data, owner, repo, number);
    } catch (error) {
        showCardError(cardElement, error.message);
    }
}

function updateCardWithJobs(cardElement, data, owner, repo, number) {
    renderJobSection(cardElement, `e2e-${owner}-${repo}-${number}`, data.e2e, owner, repo, number, 'E2E', 'e2e');
    renderJobSection(cardElement, `payload-${owner}-${repo}-${number}`, data.payload, owner, repo, number, 'Payload', 'payload');
}

function renderJobSection(cardElement, sectionId, jobData, owner, repo, number, displayType, jobType) {
    const section = cardElement.querySelector(`#${sectionId}`);
    const header = section.querySelector('.job-section-header');
    const list = section.querySelector('.job-list');

    // Filter jobs (remove ones that are now running after retest)
    let failed = (jobData.failed || []).filter(job => {
        const jobKey = `${owner}/${repo}/${number}/${job.name}`;
        const retestInfo = retestedJobs.get(jobKey);
        if (retestInfo) {
            const isRunning = (jobData.running || []).some(r => r.name === job.name);
            if (isRunning) {
                clearInterval(retestInfo.pollInterval);
                retestedJobs.delete(jobKey);
                return false;
            }
        }
        return true;
    });

    const running = jobData.running || [];

    // Update header
    header.textContent = `▶ ${displayType} Jobs (${failed.length} failed | ${running.length} running)`;

    // Add toggle listener
    const newHeader = header.cloneNode(true);
    header.parentNode.replaceChild(newHeader, header);
    newHeader.addEventListener('click', () => list.classList.toggle('expanded'));

    // Render jobs
    list.innerHTML = '';

    if (failed.length > 0) {
        const activeRetestCount = renderJobItems(list, failed, owner, repo, number, jobType);
        list.appendChild(createRetestAllButton(owner, repo, number, displayType, jobType, activeRetestCount));
    } else {
        list.appendChild(createElement('div', '', '✅ No failed jobs', { style: 'padding: 0.5rem;' }));
    }
}

function renderJobItems(list, failedJobs, owner, repo, number, jobType) {
    let activeRetestCount = 0;

    failedJobs.forEach(job => {
        const jobItem = createElement('div', 'job-item');
        const jobName = createJobNameWithLinks(job);
        const jobActions = createElement('div', 'job-actions');

        const retestBtn = createRetestButton(job, owner, repo, number, jobType);
        const analyzeBtn = createAnalyzeButton();
        const checkPermafailBtn = createCheckPermafailButton(job, owner, repo, number);

        if (!retestBtn.disabled) activeRetestCount++;

        jobActions.appendChild(retestBtn);
        jobActions.appendChild(analyzeBtn);
        jobActions.appendChild(checkPermafailBtn);
        jobItem.appendChild(jobActions);
        jobItem.appendChild(jobName);

        // Store job URL and job data for permafail checking
        if (job.urls && job.urls.length > 0) {
            jobItem.dataset.jobUrl = job.urls[0];
        }
        jobItem.dataset.jobName = job.name;
        jobItem.dataset.jobUrls = JSON.stringify(job.urls || []);
        jobItem.dataset.owner = owner;
        jobItem.dataset.repo = repo;
        jobItem.dataset.pr = number;

        const jobKey = `${owner}/${repo}/${number}/${job.name}`;
        attachJobCardEvents(jobItem, jobKey);

        list.appendChild(jobItem);
    });

    return activeRetestCount;
}

function createJobNameWithLinks(job) {
    const jobName = createElement('div', 'job-name');

    // Add job name prefix
    jobName.appendChild(document.createTextNode(`❌ ${job.name} (`));

    // Create clickable links for each consecutive failure
    if (job.urls && job.urls.length > 0) {
        job.urls.forEach((url, index) => {
            if (index > 0) {
                jobName.appendChild(document.createTextNode(','));
            }

            const link = createElement('a', 'failure-link', (index + 1).toString(), {
                href: url,
                target: '_blank'
            });
            link.style.color = 'var(--primary)';
            link.style.textDecoration = 'underline';
            link.style.marginLeft = index === 0 ? '0' : '2px';
            link.style.marginRight = '2px';

            jobName.appendChild(link);
        });

        jobName.appendChild(document.createTextNode(' consecutive)'));
    } else {
        // Fallback if no URLs (shouldn't happen with new scripts)
        jobName.appendChild(document.createTextNode(`${job.consecutive} consecutive)`));
    }

    return jobName;
}

function createRetestButton(job, owner, repo, number, jobType) {
    const btn = createElement('button', 'btn');

    if (isJobRetesting(owner, repo, number, job.name)) {
        btn.textContent = '⏳ Retesting...';
        btn.disabled = true;
    } else {
        btn.textContent = 'Retest';
        btn.addEventListener('click', (e) => {
            e.target.textContent = '⏳ Retesting...';
            e.target.disabled = true;
            retestJob(owner, repo, number, [job.name], jobType);
        });
    }

    return btn;
}

function createAnalyzeButton() {
    const btn = createElement('button', 'btn btn-secondary', 'Analyze');
    btn.disabled = true;
    return btn;
}

function createCheckPermafailButton(job, owner, repo, number) {
    const btn = createElement('button', 'btn btn-secondary check-permafail-btn', 'Check for Permafail');

    // Show button only if 2+ consecutive failures
    const consecutiveFailures = job.urls?.length || job.consecutive || 0;
    if (consecutiveFailures < 2) {
        btn.style.display = 'none';
    }

    // Check if job already has permafail status
    const jobKey = `${owner}/${repo}/${number}/${job.name}`;
    if (permafailJobs.has(jobKey)) {
        btn.style.display = 'none';
    }

    return btn;
}

function createRetestAllButton(owner, repo, number, displayType, jobType, activeRetestCount) {
    const btn = createElement('button', 'btn');

    if (activeRetestCount === 0) {
        btn.textContent = `Retest All ${displayType} (all retesting...)`;
        btn.disabled = true;
    } else {
        btn.textContent = `Retest All ${displayType}`;
        btn.addEventListener('click', (e) => retestAllJobs(owner, repo, number, jobType, e));
    }

    return btn;
}

// ========================================
// Permafail Detection
// ========================================
async function handleFailedJob(job, consecutiveFailures, owner, repo, pr) {
    const jobKey = `${owner}/${repo}/${pr}/${job.name}`;

    if (consecutiveFailures <= MAX_AUTO_RETEST_FAILURES) {
        // 1st or 2nd failure: would auto-retest immediately (future enhancement)
        return;
    }

    if (consecutiveFailures === PERMAFAIL_CHECK_THRESHOLD) {
        // 3rd failure: check for permafail
        const jobUrls = job.urls || [];

        if (jobUrls.length < PERMAFAIL_CHECK_THRESHOLD) {
            // Not enough data, would allow retest (future enhancement)
            return;
        }

        // Trigger analysis
        try {
            const response = await fetch('/api/jobs/analyze', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    pr: `${owner}/${repo}#${pr}`,
                    repo: `${owner}/${repo}`,
                    job_name: job.name,
                    job_urls: jobUrls.slice(0, PERMAFAIL_CHECK_THRESHOLD)
                })
            });

            if (!response.ok) {
                console.error('Permafail analysis request failed:', response.status, response.statusText);
                return; // Fail open: allow retest
            }

            const result = await response.json();

            // Check for analysis error
            if (result.error) {
                showToast(`Analysis failed: ${result.reason}`, 'error');
                return; // Fail open - don't set permafail, leave retest enabled
            }

            if (result.permafail) {
                // Mark as permafail, disable retest
                const jobElement = document.querySelector(`[data-job-url="${jobUrls[0]}"]`);
                if (jobElement) {
                    renderPermafailIcon(jobElement, result.reason);
                    permafailJobs.set(jobKey, result);
                }
                return; // Don't retest
            }
        } catch (error) {
            console.error('Permafail analysis failed:', error);
            // Fail open: allow retest
        }

        // Not a permafail, continue retesting (future enhancement)
    }
}

async function manualPermafailCheck(jobElement, buttonElement) {
    // Disable button and show loading state
    buttonElement.disabled = true;
    buttonElement.textContent = 'Analyzing...';

    // Extract job data from element
    const jobName = jobElement.dataset.jobName;
    const jobUrls = JSON.parse(jobElement.dataset.jobUrls || '[]');
    const owner = jobElement.dataset.owner;
    const repo = jobElement.dataset.repo;
    const pr = jobElement.dataset.pr;

    if (!jobName || !owner || !repo || !pr) {
        console.error('Missing job data on element:', jobElement);
        buttonElement.textContent = 'Error: Missing data';
        setTimeout(() => {
            buttonElement.textContent = 'Check for Permafail';
            buttonElement.disabled = false;
        }, 2000);
        return;
    }

    if (jobUrls.length < 2) {
        buttonElement.textContent = 'Not enough failures';
        setTimeout(() => {
            buttonElement.textContent = 'Check for Permafail';
            buttonElement.disabled = false;
        }, 2000);
        return;
    }

    const jobKey = `${owner}/${repo}/${pr}/${jobName}`;

    try {
        const response = await fetch('/api/jobs/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pr: `${owner}/${repo}#${pr}`,
                repo: `${owner}/${repo}`,
                job_name: jobName,
                job_urls: jobUrls
            })
        });

        if (!response.ok) {
            throw new Error(`Analysis request failed: ${response.status} ${response.statusText}`);
        }

        const result = await response.json();

        // Check for analysis error
        if (result.error) {
            showToast(`Analysis failed: ${result.reason}`, 'error');
            buttonElement.textContent = 'Analysis failed';
            setTimeout(() => {
                buttonElement.textContent = 'Check for Permafail';
                buttonElement.disabled = false;
            }, 2000);
            return; // Fail open - don't set permafail
        }

        if (result.permafail) {
            // Mark as permafail
            renderPermafailIcon(jobElement, result.reason);
            permafailJobs.set(jobKey, result);
            buttonElement.style.display = 'none';
            showToast('Permafail detected: ' + result.reason, 'error');
        } else {
            // No permafail detected
            buttonElement.textContent = 'No permafail detected';
            setTimeout(() => {
                buttonElement.textContent = 'Check for Permafail';
                buttonElement.disabled = false;
            }, 2000);
            showToast('No permafail detected - safe to retest', 'success');
        }
    } catch (error) {
        console.error('Manual permafail check failed:', error);
        buttonElement.textContent = 'Check Failed';
        setTimeout(() => {
            buttonElement.textContent = 'Check for Permafail';
            buttonElement.disabled = false;
        }, 2000);
        showToast('Permafail check failed: ' + error.message, 'error');
    }
}

// ========================================
// Retest Logic
// ========================================
async function retestJob(owner, repo, pr, jobs, type) {
    try {
        const response = await fetch('/api/retest', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ owner, repo, pr, jobs, type })
        });

        const result = await response.json();

        if (result.error === 'auth_failed') {
            showAuthBanner('GitHub CLI not authenticated. Run: gh auth login');
            disableAllRetestButtons();
        } else if (result.success) {
            showToast(`✅ Retest triggered for ${jobs.length} job(s)`, 'success');
            trackRetestedJobs(owner, repo, pr, jobs);
        } else {
            showToast(`❌ Error: ${result.error}`, 'error');
        }
    } catch (error) {
        console.error('Retest failed:', error);
        showToast('Retest failed: ' + error.message, 'error');
    }
}

function retestAllJobs(owner, repo, pr, type, event) {
    if (event?.target) {
        event.target.disabled = true;
        event.target.textContent = '⏳ Retesting all...';
    }

    const card = document.getElementById(`pr-${owner}-${repo}-${pr}`);
    const section = card.querySelector(`#${type}-${owner}-${repo}-${pr}`);
    const jobs = extractJobNames(section);

    // Disable all individual retest buttons
    section.querySelectorAll('.job-item button.btn:not(.btn-secondary)').forEach(btn => {
        if (!btn.disabled) {
            btn.textContent = '⏳ Retesting...';
            btn.disabled = true;
        }
    });

    retestJob(owner, repo, pr, jobs, type);
}

function trackRetestedJobs(owner, repo, pr, jobs) {
    jobs.forEach(jobName => {
        const jobKey = `${owner}/${repo}/${pr}/${jobName}`;
        const startTime = Date.now();

        const pollInterval = setInterval(async () => {
            const elapsed = Date.now() - startTime;

            if (elapsed > MAX_POLL_TIME) {
                clearInterval(pollInterval);
                retestedJobs.delete(jobKey);
                return;
            }

            const card = document.getElementById(`pr-${owner}-${repo}-${pr}`);
            if (card) {
                // Load updated job data
                await loadPRJobs(owner, repo, pr, card);

                // Check if job is still failed with 3+ consecutive failures
                await checkForPermafail(owner, repo, pr, jobName);
            }
        }, POLL_INTERVAL);

        retestedJobs.set(jobKey, { startTime, pollInterval });
    });
}

async function checkForPermafail(owner, repo, pr, jobName) {
    try {
        // Fetch current job data
        const response = await fetch(`/api/pr/${owner}/${repo}/${pr}`);

        if (!response.ok) {
            console.error('Failed to fetch PR job data:', response.status, response.statusText);
            return;
        }

        const data = await response.json();

        // Check both e2e and payload jobs
        const allFailedJobs = [...(data.e2e?.failed || []), ...(data.payload?.failed || [])];
        const job = allFailedJobs.find(j => j.name === jobName);

        if (job && job.consecutive >= PERMAFAIL_CHECK_THRESHOLD) {
            // Job is still failed with 3+ consecutive failures - check for permafail
            await handleFailedJob(job, job.consecutive, owner, repo, pr);
        }
    } catch (error) {
        console.error('Failed to check for permafail:', error);
    }
}

function disableAllRetestButtons() {
    document.querySelectorAll('button').forEach(btn => {
        if (btn.textContent.includes('Retest')) {
            btn.disabled = true;
        }
    });
}

// ========================================
// UI Feedback
// ========================================
function showToast(message, type = 'success') {
    const toast = createElement('div', `toast ${type}`, message);
    DOM.toastContainer.appendChild(toast);

    setTimeout(() => toast.remove(), 5000);
}

function showLoading(message) {
    DOM.prContainer.innerHTML = `<div class="loading">${message}</div>`;
}

function hideLoading() {
    const loading = DOM.prContainer.querySelector('.loading');
    if (loading?.textContent.includes('Searching')) {
        loading.remove();
    }
}

function showAuthBanner(message) {
    DOM.authBanner.textContent = '⚠️ ' + message;
    DOM.authBanner.classList.remove('hidden');
}

function showCardError(cardElement, message) {
    cardElement.innerHTML += `<div style="color: var(--primary); padding: 1rem;">⚠️ Error: ${message}</div>`;
}
