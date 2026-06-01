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

// Auto-retest state tracking
const autoRetestEnabled = new Map(); // "owner/repo/number" -> boolean
const jobFailureCounters = new Map(); // "owner/repo/number/jobName" -> count
const jobStateCache = new Map(); // "owner/repo/number/jobName" -> 'success'|'failure'|'pending'
const pollingIntervals = new Map(); // "owner/repo/number" -> intervalId

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

    // Load auto-retest state from localStorage
    loadAutoRetestState();

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
    document.getElementById('forceReanalyzeItem').addEventListener('click', handleForceReanalyze);
    document.addEventListener('click', hideContextMenu);

    // Terminal modal event listener
    document.getElementById('terminalClose').addEventListener('click', hideTerminalModal);

    // Check for Permafail button event delegation
    document.addEventListener('click', async (e) => {
        if (e.target.classList.contains('check-permafail-btn')) {
            const jobElement = e.target.closest('.job-item');
            await manualPermafailCheck(jobElement, e.target);
        }
    });

    // Auto-retest toggle event delegation
    document.addEventListener('change', (e) => {
        if (e.target.classList.contains('auto-retest-toggle')) {
            const prKey = e.target.dataset.prKey;
            const enabled = e.target.checked;
            autoRetestEnabled.set(prKey, enabled);
            saveAutoRetestState();

            if (enabled) {
                startPollingForPR(prKey);
                showToast(`Auto-retest enabled for PR ${prKey.split('/').pop()}`, 'success');
            } else {
                stopPollingForPR(prKey);
                showToast(`Auto-retest disabled for PR ${prKey.split('/').pop()}`, 'info');
            }
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
// Auto-Retest State Management
// ========================================
function loadAutoRetestState() {
    try {
        const saved = localStorage.getItem('autoRetestEnabled');
        if (saved) {
            const parsed = JSON.parse(saved);
            Object.entries(parsed).forEach(([key, val]) => {
                autoRetestEnabled.set(key, val);
            });
            console.log(`Loaded auto-retest state for ${Object.keys(parsed).length} PR(s)`);
        }
    } catch (error) {
        console.error('Failed to load auto-retest state:', error);
    }
}

function saveAutoRetestState() {
    try {
        const obj = {};
        autoRetestEnabled.forEach((val, key) => {
            obj[key] = val;
        });
        localStorage.setItem('autoRetestEnabled', JSON.stringify(obj));
    } catch (error) {
        console.error('Failed to save auto-retest state:', error);
    }
}

function startPollingForPR(prKey) {
    // Don't start if already polling
    if (pollingIntervals.has(prKey)) {
        return;
    }

    console.log(`Starting auto-retest polling for ${prKey}`);

    // Poll immediately, then every 30 seconds
    checkJobStatesForAutoRetest(prKey);
    const intervalId = setInterval(() => {
        checkJobStatesForAutoRetest(prKey);
    }, 30000);

    pollingIntervals.set(prKey, intervalId);
}

function stopPollingForPR(prKey) {
    const intervalId = pollingIntervals.get(prKey);
    if (intervalId) {
        clearInterval(intervalId);
        pollingIntervals.delete(prKey);
        console.log(`Stopped auto-retest polling for ${prKey}`);
    }
}

async function checkJobStatesForAutoRetest(prKey) {
    const [owner, repo, number] = prKey.split('/');

    try {
        const response = await fetch(`/api/pr/${owner}/${repo}/${number}`);
        if (!response.ok) {
            console.error(`Failed to fetch jobs for ${prKey}`);
            return;
        }

        const data = await response.json();
        const allJobs = [...data.e2e, ...data.payload];

        for (const job of allJobs) {
            const jobKey = `${prKey}/${job.name}`;
            const currentState = job.state;
            const previousState = jobStateCache.get(jobKey);

            // Detect state transition: success -> failure
            if (previousState === 'success' && currentState === 'failure') {
                const count = (jobFailureCounters.get(jobKey) || 0) + 1;
                jobFailureCounters.set(jobKey, count);

                console.log(`Job ${job.name} failed (attempt ${count})`);

                if (count <= MAX_AUTO_RETEST_FAILURES) {
                    // Auto-retest immediately (1st or 2nd failure)
                    console.log(`Auto-retesting ${job.name} (attempt ${count})`);
                    await retestJob(owner, repo, number, [job.name], job.type || 'e2e');
                    showToast(`🔄 Retesting ${job.name} (attempt ${count})`, 'info');
                } else if (count === PERMAFAIL_CHECK_THRESHOLD) {
                    // Check permafail before retesting on 3rd failure
                    console.log(`Checking permafail for ${job.name} before attempt ${count}`);
                    await checkPermafailBeforeRetest(owner, repo, number, job, prKey);
                }
            }

            // Update cache
            jobStateCache.set(jobKey, currentState);
        }
    } catch (error) {
        console.error(`Error checking job states for ${prKey}:`, error);
    }
}

async function checkPermafailBeforeRetest(owner, repo, number, job, prKey) {
    try {
        // Get job URLs from the job object
        const jobUrls = job.urls || [];

        if (jobUrls.length < 2) {
            console.warn(`Not enough job URLs for permafail check on ${job.name}`);
            // Retest anyway if we can't check
            await retestJob(owner, repo, number, [job.name], job.type || 'e2e');
            return;
        }

        showToast(`Checking for permafail pattern on ${job.name}...`, 'info');

        const response = await fetch('/api/jobs/analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pr: `${owner}/${repo}#${number}`,
                repo: `${owner}/${repo}`,
                job_name: job.name,
                job_urls: jobUrls
            })
        });

        const result = await response.json();

        if (result.permafail) {
            // Permafail detected - disable auto-retest for this PR
            autoRetestEnabled.set(prKey, false);
            saveAutoRetestState();
            stopPollingForPR(prKey);

            // Update UI toggle
            const toggleElement = document.querySelector(`[data-pr-key="${prKey}"]`);
            if (toggleElement) {
                toggleElement.checked = false;
                toggleElement.disabled = true;
            }

            showToast(`⚠️ Permafail detected on ${job.name}: ${result.reason}`, 'error');
            console.log(`Disabled auto-retest for ${prKey} due to permafail`);
        } else {
            // Not a permafail - proceed with retest
            console.log(`No permafail detected on ${job.name}, retesting...`);
            await retestJob(owner, repo, number, [job.name], job.type || 'e2e');
            showToast(`🔄 Retesting ${job.name} (permafail check passed)`, 'info');
        }
    } catch (error) {
        console.error('Error checking permafail:', error);
        // On error, allow retest (fail open)
        await retestJob(owner, repo, number, [job.name], job.type || 'e2e');
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
    icon.title = 'Click to view permafail details';
    icon.onclick = () => showPermafailModal(reason);
    jobHeader.appendChild(icon);

    // Disable retest button
    const retestBtn = jobElement.querySelector('.job-actions button.btn:not(.btn-secondary)');
    if (retestBtn) {
        retestBtn.disabled = true;
    }
}

function renderNonPermafailInfo(jobElement, reason) {
    const jobHeader = jobElement.querySelector('.job-name') || jobElement;

    // Remove existing info icon if present
    const existing = jobElement.querySelector('.analysis-info-icon');
    if (existing) existing.remove();

    // Add info icon (using emoji)
    const icon = document.createElement('span');
    icon.className = 'analysis-info-icon';
    icon.textContent = 'ℹ️';
    icon.title = 'Click to view analysis reasoning';
    icon.style.cursor = 'pointer';
    icon.style.marginLeft = '8px';
    icon.style.fontSize = '16px';
    icon.onclick = () => showPermafailModal(reason, false);
    jobHeader.appendChild(icon);
}

function showPermafailModal(reason, isPermafail = true) {
    // Create modal if it doesn't exist
    let modal = document.getElementById('permafail-modal');
    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'permafail-modal';
        modal.className = 'permafail-modal';
        modal.innerHTML = `
            <div class="permafail-modal-content">
                <div class="permafail-modal-header">
                    <h3 id="permafail-modal-title">
                        <img src="/static/dumpster-fire.svg" width="24" height="24" alt="" id="permafail-modal-icon">
                        <span id="permafail-modal-title-text">Permafail Detected</span>
                    </h3>
                    <button class="permafail-modal-close">&times;</button>
                </div>
                <div class="permafail-modal-body"></div>
            </div>
        `;
        document.body.appendChild(modal);

        // Close when clicking X or outside modal
        const closeModal = () => {
            modal.style.display = 'none';
            document.removeEventListener('keydown', handleEscape);
        };

        const handleEscape = (e) => {
            if (e.key === 'Escape') {
                closeModal();
            }
        };

        modal.querySelector('.permafail-modal-close').onclick = closeModal;
        modal.onclick = (e) => {
            if (e.target === modal) {
                closeModal();
            }
        };
    }

    // Update modal title and icon based on type
    const titleText = modal.querySelector('#permafail-modal-title-text');
    const icon = modal.querySelector('#permafail-modal-icon');
    const header = modal.querySelector('.permafail-modal-header h3');

    if (isPermafail) {
        titleText.textContent = 'Permafail Detected';
        icon.style.display = 'inline';
        header.style.color = '#dc3545';
    } else {
        titleText.textContent = 'Analysis Result';
        icon.style.display = 'none';
        header.style.color = '#28a745';
    }

    // Update modal content and show
    modal.querySelector('.permafail-modal-body').textContent = reason;
    modal.style.display = 'block';

    // Add escape key listener
    const handleEscape = (e) => {
        if (e.key === 'Escape' && modal.style.display === 'block') {
            modal.style.display = 'none';
            document.removeEventListener('keydown', handleEscape);
        }
    };
    document.addEventListener('keydown', handleEscape);
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

async function loadCachedPermafailResults(list, failedJobs) {
    // Collect all job URLs
    const jobUrls = [];
    failedJobs.forEach(job => {
        if (job.urls && job.urls.length > 0) {
            jobUrls.push(job.urls[0]);
        }
    });

    if (jobUrls.length === 0) return;

    try {
        // Fetch cached results from database
        const response = await fetch(`/api/jobs/status?job_urls=${encodeURIComponent(JSON.stringify(jobUrls))}`);
        if (!response.ok) {
            console.error('Failed to load cached permafail results:', response.status);
            return;
        }

        const status = await response.json();

        // Apply cached results to UI
        for (const [url, result] of Object.entries(status)) {
            if (result.permafail && !result.override) {
                const jobElement = list.querySelector(`[data-job-url="${url}"]`);
                if (jobElement) {
                    const owner = jobElement.dataset.owner;
                    const repo = jobElement.dataset.repo;
                    const pr = jobElement.dataset.pr;
                    const jobName = jobElement.dataset.jobName;
                    const jobKey = `${owner}/${repo}/${pr}/${jobName}`;

                    renderPermafailIcon(jobElement, result.reason);
                    permafailJobs.set(jobKey, result);
                }
            }
        }
    } catch (error) {
        console.error('Error loading cached permafail results:', error);
    }
}

// ========================================
// Context Menu
// ========================================
function showContextMenu(event, jobElement, jobKey) {
    event.preventDefault();

    const menu = document.getElementById('contextMenu');
    const jobUrls = JSON.parse(jobElement.dataset.jobUrls || '[]');

    // Only show menu if job has 2+ consecutive failures (analyzable)
    if (jobUrls.length < 2) {
        return;
    }

    contextMenuTarget = { jobElement, jobKey };

    // Show/hide menu items based on permafail status
    const clearItem = document.getElementById('clearPermafailItem');
    const reanalyzeItem = document.getElementById('forceReanalyzeItem');

    if (permafailJobs.has(jobKey)) {
        clearItem.style.display = 'block';
        reanalyzeItem.style.display = 'block';
    } else {
        clearItem.style.display = 'none';
        reanalyzeItem.style.display = 'block';
    }

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

async function handleForceReanalyze() {
    if (!contextMenuTarget) return;

    const { jobElement, jobKey } = contextMenuTarget;
    const jobUrls = JSON.parse(jobElement.dataset.jobUrls || '[]');

    if (!jobUrls || jobUrls.length === 0) {
        console.error('No job URLs found on element');
        hideContextMenu();
        return;
    }

    hideContextMenu();

    // Declare button reference outside try block for access in catch
    let checkPermafailBtn = null;

    try {
        // Delete cached analysis for all URLs
        showToast('Deleting cached analysis...', 'info');

        const deleteResponse = await fetch('/api/jobs/delete-cache', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ job_urls: jobUrls })
        });

        if (!deleteResponse.ok) {
            const error = await deleteResponse.json();
            showToast('Failed to delete cache: ' + (error.error || 'Unknown error'), 'error');
            return;
        }

        const deleteResult = await deleteResponse.json();
        console.log(`Deleted ${deleteResult.deleted_count} cached record(s)`);

        // Clear permafail UI
        clearPermafailUI(jobElement, jobKey);

        // Find and update button to show analyzing state (after clearing UI)
        checkPermafailBtn = jobElement.querySelector('.check-permafail-btn');
        if (checkPermafailBtn) {
            checkPermafailBtn.disabled = true;
            checkPermafailBtn.textContent = 'Analyzing...';
            checkPermafailBtn.style.display = 'inline-block';
        } else {
            console.warn('Check permafail button not found for job:', jobKey);
        }

        // Trigger fresh streaming analysis
        showToast('Starting fresh analysis...', 'info');

        const owner = jobElement.dataset.owner;
        const repo = jobElement.dataset.repo;
        const pr = jobElement.dataset.pr;
        const jobName = jobElement.dataset.jobName;

        const result = await analyzeWithStreaming(
            `${owner}/${repo}#${pr}`,
            `${owner}/${repo}`,
            jobName,
            jobUrls.slice(0, 10)
        );

        // Check for analysis error
        if (result.error) {
            showToast(`Fresh analysis failed: ${result.reason}`, 'error');
            if (checkPermafailBtn) {
                checkPermafailBtn.textContent = 'Analysis failed';
                setTimeout(() => {
                    checkPermafailBtn.textContent = 'Check for Permafail';
                    checkPermafailBtn.disabled = false;
                }, 2000);
            }
            return;
        }

        if (result.permafail) {
            // Mark as permafail with fresh analysis
            renderPermafailIcon(jobElement, result.reason);
            permafailJobs.set(jobKey, result);
            if (checkPermafailBtn) {
                checkPermafailBtn.style.display = 'none';
            }
            showToast('Fresh analysis: Permafail detected - ' + result.reason, 'error');
        } else {
            // No permafail detected - store result and show info icon
            permafailJobs.set(jobKey, result);
            renderNonPermafailInfo(jobElement, result.reason);
            if (checkPermafailBtn) {
                checkPermafailBtn.textContent = 'No permafail detected';
                setTimeout(() => {
                    checkPermafailBtn.textContent = 'Check for Permafail';
                    checkPermafailBtn.disabled = false;
                }, 2000);
            }
            showToast('Fresh analysis: No permafail detected - safe to retest', 'success');
        }
    } catch (error) {
        console.error('Force re-analyze failed:', error);
        if (checkPermafailBtn) {
            checkPermafailBtn.textContent = 'Check Failed';
            setTimeout(() => {
                checkPermafailBtn.textContent = 'Check for Permafail';
                checkPermafailBtn.disabled = false;
            }, 2000);
        }
        showToast('Force re-analyze failed: ' + error.message, 'error');
    }
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

    // Auto-retest toggle
    const prKey = `${pr.owner}/${pr.repo}/${pr.number}`;
    const autoRetestControl = createElement('div', 'auto-retest-control');
    const toggleLabel = createElement('label');
    const toggleCheckbox = createElement('input', 'auto-retest-toggle');
    toggleCheckbox.type = 'checkbox';
    toggleCheckbox.dataset.prKey = prKey;
    toggleCheckbox.checked = autoRetestEnabled.get(prKey) || false;

    toggleLabel.appendChild(toggleCheckbox);
    toggleLabel.appendChild(document.createTextNode(' 🔄 Auto-retest on failure'));
    autoRetestControl.appendChild(toggleLabel);

    // Start polling if toggle is already enabled
    if (toggleCheckbox.checked) {
        startPollingForPR(prKey);
    }

    // Job sections container
    const jobSectionsContainer = createElement('div', 'job-sections-container');
    jobSectionsContainer.appendChild(createJobSectionPlaceholder('e2e', pr.owner, pr.repo, pr.number));
    jobSectionsContainer.appendChild(createJobSectionPlaceholder('payload', pr.owner, pr.repo, pr.number));

    card.appendChild(prHeader);
    card.appendChild(autoRetestControl);
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

async function renderJobSection(cardElement, sectionId, jobData, owner, repo, number, displayType, jobType) {
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

        // Load cached permafail results after rendering
        await loadCachedPermafailResults(list, failed);
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
                    job_urls: jobUrls.slice(0, 10)  // Send up to 10 URLs for pattern detection
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
    buttonElement.textContent = 'Checking...';

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
        // First check if we already have cached results
        const statusResponse = await fetch(`/api/jobs/status?job_urls=${encodeURIComponent(JSON.stringify([jobUrls[0]]))}`);
        if (statusResponse.ok) {
            const statusData = await statusResponse.json();
            const cachedResult = statusData[jobUrls[0]];

            if (cachedResult && cachedResult.permafail && !cachedResult.override) {
                // We have a cached permafail result - use it
                renderPermafailIcon(jobElement, cachedResult.reason);
                permafailJobs.set(jobKey, cachedResult);
                buttonElement.style.display = 'none';
                showToast('Using cached permafail result', 'info');
                return;
            } else if (cachedResult && !cachedResult.permafail) {
                // We have a cached "not permafail" result - show it with info icon
                permafailJobs.set(jobKey, cachedResult);
                renderNonPermafailInfo(jobElement, cachedResult.reason);
                buttonElement.textContent = 'No permafail (cached)';
                setTimeout(() => {
                    buttonElement.textContent = 'Check for Permafail';
                    buttonElement.disabled = false;
                }, 2000);
                showToast('No permafail detected (cached result)', 'success');
                return;
            }
        }

        // No cached result, run streaming analysis
        buttonElement.textContent = 'Analyzing...';
        const result = await analyzeWithStreaming(
            `${owner}/${repo}#${pr}`,
            `${owner}/${repo}`,
            jobName,
            jobUrls.slice(0, 10)  // Send up to 10 URLs for pattern detection
        );

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
            // No permafail detected - store result and show info icon
            permafailJobs.set(jobKey, result);
            renderNonPermafailInfo(jobElement, result.reason);
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

// Terminal Modal Functions
function showTerminalModal() {
    const modal = document.getElementById('terminalModal');
    const body = document.getElementById('terminalBody');
    body.innerHTML = ''; // Clear previous content
    modal.style.display = 'flex';
}

function hideTerminalModal() {
    const modal = document.getElementById('terminalModal');
    modal.style.display = 'none';
}

function appendTerminalLine(text) {
    const body = document.getElementById('terminalBody');
    const line = document.createElement('div');
    line.className = 'terminal-line';
    line.textContent = text;
    body.appendChild(line);
    // Auto-scroll to bottom
    body.scrollTop = body.scrollHeight;
}

// SSE Streaming Analysis
async function analyzeWithStreaming(pr, repo, jobName, jobUrls) {
    return new Promise((resolve, reject) => {
        const eventSource = new EventSource('/api/jobs/analyze-stream');

        // Show terminal modal
        showTerminalModal();

        // Send analysis request via fetch first
        fetch('/api/jobs/analyze-stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                pr: pr,
                repo: repo,
                job_name: jobName,
                job_urls: jobUrls
            })
        }).then(async response => {
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line in buffer

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        const data = JSON.parse(line.substring(6));

                        if (data.type === 'output') {
                            appendTerminalLine(data.line);
                        } else if (data.type === 'result') {
                            resolve(data.data);
                            return;
                        } else if (data.type === 'error') {
                            appendTerminalLine('ERROR: ' + data.message);
                            reject(new Error(data.message));
                            return;
                        }
                    }
                }
            }
        }).catch(error => {
            appendTerminalLine('Connection error: ' + error.message);
            reject(error);
        });
    });
}
