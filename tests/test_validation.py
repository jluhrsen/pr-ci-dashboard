"""Tests for input validation on subprocess/prompt-bound values."""
import pytest
from unittest.mock import patch
from pr_ci_dashboard.server import app
from pr_ci_dashboard.utils import validation
from pr_ci_dashboard.utils.db import init_db


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "test.db"
    init_db(str(db_path))
    app.config['TESTING'] = True
    app.config['CSRF_ENABLED'] = False
    app.config['DB_PATH'] = str(db_path)
    with app.test_client() as client:
        yield client


PROW = 'https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs'


# ========== validation module ==========

def test_valid_name():
    assert validation.valid_name('openshift')
    assert validation.valid_name('ovn-kubernetes')
    assert validation.valid_name('release-4.20')
    for bad in ('', 'a/b', 'a b', 'a;b', 'a$(x)', '-' * 101, None, 42, 'a"b', "a'b"):
        assert not validation.valid_name(bad), f"should reject {bad!r}"


def test_valid_repo_full():
    assert validation.valid_repo_full('openshift/ovn-kubernetes')
    for bad in ('openshift', 'a/b/c', '/repo', 'owner/', 'a b/c', None):
        assert not validation.valid_repo_full(bad), f"should reject {bad!r}"


def test_valid_pr_number():
    assert validation.valid_pr_number(1)
    assert validation.valid_pr_number(999999)
    for bad in (0, -1, 10**9, True, '5', None, 1.5):
        assert not validation.valid_pr_number(bad), f"should reject {bad!r}"


def test_valid_job_name():
    assert validation.valid_job_name('e2e-aws-ovn')
    assert validation.valid_job_name('periodic-ci-openshift-release-master-nightly-4.20-e2e-metal-ipi')
    for bad in ('', 'job name', 'job;rm -rf', 'a/b', 'x' * 301, None, '`cmd`'):
        assert not validation.valid_job_name(bad), f"should reject {bad!r}"


def test_valid_job_url():
    # Representative real Prow viewer URL shape
    assert validation.valid_job_url(
        'https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/'
        'openshift_ovn-kubernetes/2586/pull-ci-openshift-ovn-kubernetes-master-e2e-aws-ovn/1943454064)'
        .rstrip(')'))
    assert validation.valid_job_url(f'{PROW}/pull-ci-x/123')
    for bad in ('https://evil.example/view/gs/x',
                'http://prow.ci.openshift.org/view/gs/x',        # not https
                'https://prow.ci.openshift.org/view/other/x',    # not /view/gs/
                'https://prow.ci.openshift.org.evil.example/view/gs/x',  # host suffix trick
                f'{PROW}/x y',                                   # whitespace
                f'{PROW}/x"quote',
                f"{PROW}/x'quote",
                f'{PROW}/`cmd`',                                 # backtick
                f'{PROW}/<tag>',                                 # angle brackets
                f'{PROW}/x\x00nul',                              # raw NUL
                f'{PROW}/x\nnewline',                            # raw control
                f'{PROW}/x?query=1',                             # query
                f'{PROW}/x#fragment',                            # fragment
                f'{PROW}/x|pipe',
                f'{PROW}/x\\back',
                f'{PROW}/éaccent',                          # non-ASCII
                PROW + '/' + 'a' * 500,                          # too long
                None, 42):
        assert not validation.valid_job_url(bad), f"should reject {bad!r}"


def test_valid_job_urls():
    assert validation.valid_job_urls([f'{PROW}/1', f'{PROW}/2'])
    assert not validation.valid_job_urls([f'{PROW}/1', 'https://evil.example/2'])
    assert not validation.valid_job_urls('not-a-list')


# ========== endpoint enforcement ==========

def test_retest_rejects_bad_fields(client):
    base = {"owner": "openshift", "repo": "origin", "pr": 1,
            "jobs": ["e2e-aws"], "type": "e2e"}
    bad_variants = [
        {**base, "owner": "open shift"},
        {**base, "repo": "origin;rm"},
        {**base, "pr": "1; echo"},
        {**base, "jobs": ["e2e-aws", "job`x`"]},
        {**base, "jobs": "e2e-aws"},
    ]
    for body in bad_variants:
        response = client.post('/api/retest', json=body)
        assert response.status_code == 400, f"should reject {body}"


def test_retest_accepts_valid_fields(client):
    with patch('pr_ci_dashboard.server.retest_jobs', return_value={"success": True}):
        response = client.post('/api/retest', json={
            "owner": "openshift", "repo": "ovn-kubernetes", "pr": 1234,
            "jobs": ["e2e-aws-ovn", "e2e-gcp-ovn-upgrade"], "type": "e2e"})
    assert response.status_code == 200


def test_pr_jobs_rejects_bad_owner_repo(client):
    assert client.get('/api/pr/open%20shift/origin/1').status_code == 400
    assert client.get('/api/pr/openshift/ori;gin/1').status_code == 400


def test_analyze_rejects_non_prow_urls(client):
    response = client.post('/api/jobs/analyze', json={
        "pr": "openshift/origin#1", "repo": "openshift/origin",
        "job_name": "e2e-aws",
        "job_urls": ["https://evil.example/1", "https://evil.example/2"]})
    assert response.status_code == 400
    assert 'prow.ci.openshift.org' in response.get_json()['error']


def test_analyze_rejects_bad_job_name_and_repo(client):
    base = {"pr": "openshift/origin#1", "repo": "openshift/origin",
            "job_name": "e2e-aws", "job_urls": [f"{PROW}/1", f"{PROW}/2"]}
    for body in ({**base, "job_name": "e2e; rm -rf /"},
                 {**base, "repo": "not-a-repo"},
                 {**base, "pr": "no-hash"},
                 {**base, "pr": "bad repo#1"}):
        response = client.post('/api/jobs/analyze', json=body)
        assert response.status_code == 400, f"should reject {body}"


def test_analyze_stream_shares_validation(client):
    response = client.post('/api/jobs/analyze-stream', json={
        "pr": "openshift/origin#1", "repo": "openshift/origin",
        "job_name": "e2e-aws",
        "job_urls": ["https://evil.example/1", "https://evil.example/2"]})
    assert response.status_code == 400


def test_search_rejects_oversized_query_and_bad_pagination(client):
    assert client.post('/api/search', json={"query": "x" * 513}).status_code == 400
    assert client.post('/api/search', json={"query": "is:pr", "page": 0}).status_code == 400
    assert client.post('/api/search', json={"query": "is:pr", "per_page": 1000}).status_code == 400
    assert client.post('/api/search', json={"query": "is:pr", "page": "1"}).status_code == 400


def test_retest_accepts_string_pr_number(client):
    """Regression: the frontend sends pr as a string (from prKey.split);
    digit-strings must be accepted, non-numeric strings still rejected."""
    with patch('pr_ci_dashboard.server.retest_jobs', return_value={"success": True}) as mock_retest:
        response = client.post('/api/retest', json={
            "owner": "openshift", "repo": "ovn-kubernetes", "pr": "3279",
            "jobs": ["e2e-aws-ovn"], "type": "e2e"})
    assert response.status_code == 200
    assert mock_retest.call_args[0][2] == 3279  # coerced to int

    for bad_pr in ("3279; rm", "\u00b3", "-1", "1.5", ""):
        response = client.post('/api/retest', json={
            "owner": "openshift", "repo": "ovn-kubernetes", "pr": bad_pr,
            "jobs": ["e2e-aws-ovn"], "type": "e2e"})
        assert response.status_code == 400, f"should reject pr={bad_pr!r}"
