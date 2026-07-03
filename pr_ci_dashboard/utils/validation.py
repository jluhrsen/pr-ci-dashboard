"""Input validation for values that reach subprocesses or Claude prompts.

Everything here rejects rather than sanitizes: values that fail the checks
never reach a bash script argument, a gh CLI invocation, or an AI analysis
prompt. The subprocess calls all use list args (no shell=True), so this is
defense in depth against script-internal interpolation and prompt injection,
not a substitute for it.
"""
import re
from urllib.parse import urlsplit

# GitHub owner/repo segments: word chars, dots, dashes. No slashes, spaces,
# quotes, or shell metacharacters.
_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,100}$')

# Prow job names, e.g. pull-ci-openshift-ovn-kubernetes-master-e2e-aws-ovn
_JOB_NAME_RE = re.compile(r'^[A-Za-z0-9_.-]{1,300}$')

# Job URLs are fetched artifacts and embedded in analysis prompts; only the
# OpenShift Prow viewer is a legitimate source.
JOB_URL_PREFIX = 'https://prow.ci.openshift.org/view/gs/'
_MAX_JOB_URL_LEN = 500
# Conservative charset for Prow/GCS view paths: letters, digits, slash, dot,
# underscore, dash, percent, plus, equals. No backticks, angle brackets,
# braces, pipes, backslashes, or anything prompt-hostile.
_JOB_URL_PATH_RE = re.compile(r'^/view/gs/[A-Za-z0-9/._%+=-]{1,450}$')


def valid_name(value):
    """True for a valid GitHub owner or repo segment."""
    return isinstance(value, str) and bool(_NAME_RE.match(value))


def valid_repo_full(value):
    """True for a valid "owner/repo" string."""
    if not isinstance(value, str) or value.count('/') != 1:
        return False
    owner, repo = value.split('/')
    return valid_name(owner) and valid_name(repo)


def valid_pr_number(value):
    """True for a plausible PR number."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10**9


def valid_job_name(value):
    """True for a valid Prow job name."""
    return isinstance(value, str) and bool(_JOB_NAME_RE.match(value))


def valid_job_url(value):
    """True for a Prow job URL from the allowlisted viewer.

    Structurally parsed, not just prefix-matched: https scheme, exact Prow
    host, /view/gs/ path in a conservative charset, no query/fragment, no
    control characters or non-ASCII. Checked character-by-character before
    urlsplit because urlsplit itself strips some control characters.
    """
    if not isinstance(value, str) or not (0 < len(value) <= _MAX_JOB_URL_LEN):
        return False
    if not value.isascii() or any(ord(c) < 0x20 or ord(c) == 0x7f for c in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return (parts.scheme == 'https'
            and parts.netloc == 'prow.ci.openshift.org'
            and not parts.query
            and not parts.fragment
            and bool(_JOB_URL_PATH_RE.match(parts.path)))


def valid_job_urls(values):
    """True when every entry is a valid Prow job URL."""
    return isinstance(values, list) and all(valid_job_url(u) for u in values)
