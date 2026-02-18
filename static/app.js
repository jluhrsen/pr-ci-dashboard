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

// DOM element cache
const DOM = {
    searchInput: null,
    searchBtn: null,
    refreshBtn: null,
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
    DOM.searchBtn.addEventListener('click', () => executeSearch(DOM.searchInput.value));
    DOM.searchInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') executeSearch(DOM.searchInput.value);
    });
    DOM.refreshBtn.addEventListener('click', () => {
        currentPage = 1;
        DOM.prContainer.innerHTML = '';
        executeSearch(DOM.searchInput.value);
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

// ========================================
// Search & PR Rendering
// ========================================
async function executeSearch(query) {
    showLoading('Searching PRs...');

    try {
        const response = await fetch('/api/search', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ query, page: currentPage, per_page: 10 })
        });

        const data = await response.json();

        if (data.error) {
            showToast(data.error, 'error');
            hideLoading();
            return;
        }

        currentPRs = data.prs;
        totalResults = data.total;

        hideLoading();
        renderPRCards(data.prs);
    } catch (error) {
        console.error('Search failed:', error);
        showToast('Search failed: ' + error.message, 'error');
        hideLoading();
    }
}

function renderPRCards(prs) {
    if (prs.length === 0) {
        DOM.prContainer.innerHTML = '<div class="loading">No PRs found</div>';
        return;
    }

    prs.forEach(pr => {
        const card = createPRCard(pr);
        DOM.prContainer.appendChild(card);
        loadPRJobs(pr.owner, pr.repo, pr.number, card);
    });
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
        const jobName = createElement('div', 'job-name', `❌ ${job.name} (${job.consecutive} consecutive)`);
        const jobActions = createElement('div', 'job-actions');

        const retestBtn = createRetestButton(job, owner, repo, number, jobType);
        const analyzeBtn = createAnalyzeButton();

        if (!retestBtn.disabled) activeRetestCount++;

        jobActions.appendChild(retestBtn);
        jobActions.appendChild(analyzeBtn);
        jobItem.appendChild(jobActions);
        jobItem.appendChild(jobName);
        list.appendChild(jobItem);
    });

    return activeRetestCount;
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

        const pollInterval = setInterval(() => {
            const elapsed = Date.now() - startTime;

            if (elapsed > MAX_POLL_TIME) {
                clearInterval(pollInterval);
                retestedJobs.delete(jobKey);
                return;
            }

            const card = document.getElementById(`pr-${owner}-${repo}-${pr}`);
            if (card) {
                loadPRJobs(owner, repo, pr, card);
            }
        }, POLL_INTERVAL);

        retestedJobs.set(jobKey, { startTime, pollInterval });
    });
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
