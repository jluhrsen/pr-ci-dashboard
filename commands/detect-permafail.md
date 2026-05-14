---
description: Analyze consecutive job failures to determine if they represent a permafail pattern
argument-hint: --job-urls=<urls> --job-name=<name> --pr=<pr-info>
---

## Name
pr-ci-dashboard:detect-permafail

# Detect Permafail

## When to Use This Skill

Use this skill when you have 2-10 consecutive failures of the same job and need to determine if the failures represent a systematic/permanent failure (permafail) versus a flaky failure. This is critical for CI/CD pipeline analysis to distinguish between:

- **Permafail**: A systematic failure affecting the same test(s) or infrastructure issue, detected using these patterns:
  - 3/3: All 3 most recent runs have the same failure (100% match)
  - 4/5: At least 4 of the last 5 runs have the same failure (80% match)
  - 7/10: At least 7 of the last 10 runs have the same failure (70% match)
- **Flaky**: Non-deterministic failures with varying root causes, or failures that don't meet the permafail thresholds

## Prerequisites

- Access to the `ci-prow-navigation` skill for analyzing Prow job logs
- Access to the `Skill` tool for spawning parallel subagents to fetch job details concurrently
- 2-10 URLs pointing to consecutive job failures (from Prow/OpenShift CI, ordered newest to oldest)
- Job name context to verify consistency across all failures
- PR information to provide context for analysis

## Implementation Steps

### Step 1: Validate Inputs

Verify that all required inputs are present and properly formatted:
- `failure_urls`: Array of 2-10 job URLs (must be consecutive runs, ordered newest to oldest)
- `job_name`: String identifier of the job being analyzed
- `pr_info`: Object containing PR number and repository context
- Each URL must be a valid Prow job URL format

Reject requests if:
- URLs count is less than 2 or more than 10
- Job names don't match across all URLs
- PR context is missing

Note: URL ordering (newest first) is assumed but not validated - the frontend provides them in this order.

### Step 2: Spawn Parallel Subagents Using Skill Tool

Create N parallel tasks (where N = number of URLs provided, 2-10) to analyze each failure concurrently using the `Skill` tool:

```
Task 1: Analyze failure_urls[0] with ci-prow-navigation
Task 2: Analyze failure_urls[1] with ci-prow-navigation
...
Task N: Analyze failure_urls[N-1] with ci-prow-navigation
```

Each subagent task should:
- Invoke the `ci-prow-navigation` skill
- Pass the job URL and job name as parameters
- Be independent and non-blocking
- Timeout after 60 seconds per subagent (5 minutes total for all N in parallel)

### Step 3: Invoke ci-prow-Navigation Skill

Each subagent calls the `ci-prow-navigation` skill with parameters:
- `job_url`: The specific Prow job URL
- `job_name`: Name of the job being analyzed

Expected response structure from ci-prow-navigation:
```json
{
  "job_url": "string",
  "job_name": "string",
  "status": "FAILURE",
  "failure_type": "test_failure | infra_failure",
  "details": {
    "tests": ["test1", "test2"],
    "error_message": "string",
    "log_snippet": "string"
  }
}
```

### Step 4: Extract Failure Signatures from Each Result

Transform each ci-prow-navigation result into a normalized failure signature:

**For test failures:**
```json
{
  "type": "test_failure",
  "url": "job_url",
  "tests": ["failing_test_name1", "failing_test_name2"],
  "test_count": 2
}
```

**For infrastructure failures:**
```json
{
  "type": "infra_failure",
  "url": "job_url",
  "error": "normalized_error_message",
  "error_hash": "md5_of_error_message"
}
```

Extract and normalize error messages:
- Remove timestamps and build IDs
- Remove stack traces
- Keep the error classification and core message
- Generate MD5 hash for quick comparison

### Step 5: Compare Signatures for Permafail Pattern

Apply permafail detection logic based on the number of URLs and failure patterns:

**Detection Thresholds:**
- If N=2-3 URLs: Check if all N have the same failure (100% match required)
- If N=4-5 URLs: Check if ≥4 have the same failure (80% match required)
- If N=6-10 URLs: Check if ≥7 have the same failure (70% match required)

**For Test Failures:**
1. Extract all test names from each signature
2. For each unique test name, count how many signatures contain it
3. If ANY test name appears in enough signatures to meet the threshold above: **PERMAFAIL = TRUE**
   - Report which test(s) met the threshold and their occurrence count
4. If no test meets the threshold: **PERMAFAIL = FALSE**

Example: With 8 URLs, if "TestNetworkPolicy" appears in 7 of them → PERMAFAIL (meets 7/10 threshold)

**For Infrastructure Failures:**
1. Extract error messages from each signature
2. Group similar errors (exact match or >70% character similarity)
3. If ANY error group has enough occurrences to meet the threshold above: **PERMAFAIL = TRUE**
   - Report the error message and occurrence count
4. If no error group meets the threshold: **PERMAFAIL = FALSE**

**For Mixed Failure Types:**
1. Group signatures by type (test_failure vs infra_failure)
2. Apply the same threshold logic separately to each group:
   - **Test failures**: Count how many test_failure signatures exist. If this count meets the threshold for N total signatures, check if they have common failing tests.
   - **Infra failures**: Count how many infra_failure signatures exist. If this count meets the threshold for N total signatures, check if they have common errors.
3. If either group alone meets the permafail pattern: **PERMAFAIL = TRUE**
   - Report the dominant pattern (the one that triggered permafail)
   - Explain which runs contributed to the permafail and which were ignored (e.g., "4 out of 4 runs that reached e2e tests failed on TestNetworkPolicy. 3 other runs failed during cluster setup and are not relevant to this analysis.")
4. If neither group meets the threshold: **PERMAFAIL = FALSE**

**Example Scenario:**
- 7 total runs provided
- 3 runs: infra_failure (cluster creation failed)
- 4 runs: test_failure (all failing on TestNetworkPolicy)

**Analysis:**
- For 7 URLs, threshold is 7/10 (70%) = need 5 matches
- Test failures: 4 runs (doesn't meet 7/10 threshold for all 7)
- BUT: Consider test failures independently: 4/4 (100%) have the same test = PERMAFAIL
- Verdict: **PERMAFAIL = TRUE**
- Reason: "All 4 runs that reached e2e tests failed on TestNetworkPolicy (100% match). 3 additional runs failed during infrastructure setup and are not relevant to this test failure pattern."

### Step 6: Generate Verdict and Return JSON

Construct the final response object with:
- Boolean verdict: `permafail` (true/false)
- Reason string explaining the determination
- Complete failure signatures array
- Common tests array (if applicable and permafail=true)
- Confidence score (0.0-1.0)

## Output Format

The skill returns a JSON object with this schema:

```json
{
  "permafail": true,
  "confidence": 0.95,
  "reason": "All 3 runs show the same failing test 'test_node_scale' - consistent permanent failure",
  "failure_type": "test_failure",
  "signatures": [
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://bucket/logs/...",
      "tests": ["test_node_scale"],
      "test_count": 1
    },
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://bucket/logs/...",
      "tests": ["test_node_scale"],
      "test_count": 1
    },
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://bucket/logs/...",
      "tests": ["test_node_scale"],
      "test_count": 1
    }
  ],
  "common_tests": ["test_node_scale"]
}
```

### For Infrastructure Failure (permafail=true)

```json
{
  "permafail": true,
  "confidence": 0.92,
  "reason": "All 3 runs fail at cluster creation with identical error: 'Insufficient quota for machine type n1-standard-4'",
  "failure_type": "infra_failure",
  "signatures": [
    {
      "type": "infra_failure",
      "url": "https://prow.ci.openshift.org/view/gs://bucket/logs/...",
      "error": "Insufficient quota for machine type n1-standard-4",
      "error_hash": "a1b2c3d4e5f6g7h8"
    },
    {
      "type": "infra_failure",
      "url": "https://prow.ci.openshift.org/view/gs://bucket/logs/...",
      "error": "Insufficient quota for machine type n1-standard-4",
      "error_hash": "a1b2c3d4e5f6g7h8"
    },
    {
      "type": "infra_failure",
      "url": "https://prow.ci.openshift.org/view/gs://bucket/logs/...",
      "error": "Insufficient quota for machine type n1-standard-4",
      "error_hash": "a1b2c3d4e5f6g7h8"
    }
  ]
}
```

### For Non-Permafail (mixed or varying failures)

```json
{
  "permafail": false,
  "confidence": 0.88,
  "reason": "Mixed failure types detected: Run 1 is test_failure, Run 2 is infra_failure, Run 3 is test_failure. Inconsistent pattern indicates flaky behavior, not a systematic permafail.",
  "failure_type": "mixed",
  "signatures": [
    {
      "type": "test_failure",
      "url": "...",
      "tests": ["test_networking"],
      "test_count": 1
    },
    {
      "type": "infra_failure",
      "url": "...",
      "error": "Pod evicted due to memory pressure",
      "error_hash": "x1y2z3a4b5c6d7e8"
    },
    {
      "type": "test_failure",
      "url": "...",
      "tests": ["test_storage", "test_deployment"],
      "test_count": 2
    }
  ]
}
```

## Failure Signature Format

### Test Failure Signature

```json
{
  "type": "test_failure",
  "url": "string (job URL)",
  "tests": ["array", "of", "failing_test_names"],
  "test_count": "integer (length of tests array)"
}
```

**Fields:**
- `type`: Always "test_failure"
- `url`: The Prow job URL for this run
- `tests`: Array of test names extracted from failure logs (deduplicated)
- `test_count`: Count of unique failing tests

### Infrastructure Failure Signature

```json
{
  "type": "infra_failure",
  "url": "string (job URL)",
  "error": "string (normalized error message)",
  "error_hash": "string (MD5 hash of normalized error)"
}
```

**Fields:**
- `type`: Always "infra_failure"
- `url`: The Prow job URL for this run
- `error`: Normalized error message with timestamps and build IDs removed
- `error_hash`: MD5 hash for fast similarity comparison

## Permafail Detection Logic

### Test Failure Logic

1. Collect all unique test names from failures 1, 2, and 3
2. Find the intersection: tests that appear in ALL 3 failures
3. **Permafail = TRUE** if intersection size ≥ 1
4. Set confidence based on:
   - 100% if all 3 have identical test set (confidence: 0.99)
   - 95% if all 3 have ≥1 common test (confidence: 0.95)
   - 85% if 2 of 3 have common tests (confidence: 0.85)

### Infrastructure Failure Logic

1. Extract error messages from all failures
2. Compare error_hash values:
   - If all hashes are identical: **Permafail = TRUE** (confidence: 0.99)
3. If hashes differ, perform string similarity check on error messages:
   - Calculate Levenshtein distance or use simple substring matching
   - If enough errors are >80% similar to meet the threshold: **Permafail = TRUE** (confidence: 0.92)
   - Otherwise: **Permafail = FALSE** (confidence: 0.70)

### Mixed Type Logic

When both test_failure and infra_failure types are present:
1. **Analyze each group independently** using their respective thresholds
2. **Test failures**: Check if test_failure signatures have common failing tests
3. **Infra failures**: Check if infra_failure signatures have common errors
4. If **either group meets the permafail criteria**: **PERMAFAIL = TRUE**
   - Report the pattern that triggered permafail (tests or infra)
   - Explain the breakdown (e.g., "4 of 4 test runs failed on the same test; 3 other runs failed during setup")
5. If **neither group meets criteria**: **PERMAFAIL = FALSE**
   - Reason: "No consistent pattern found in test failures or infrastructure failures"

**Key Principle**: Infrastructure failures (cluster setup, resource quota, network issues) are **orthogonal** to test failures. A PR can have a systematic test failure (permafail) even if some runs fail during infrastructure setup. Analyze each type separately and detect permafails in either category.

## Error Handling

### Scenario 1: ci-prow-Navigation Skill Unavailable

If the ci-prow-navigation skill cannot be invoked:
- Return status: "error"
- Return error message: "ci-prow-navigation skill unavailable"
- Fallback: Attempt to parse job URLs directly (if implemented)
- Do NOT return a permafail verdict

**Response:**
```json
{
  "status": "error",
  "error": "ci-prow-navigation skill unavailable. Required for job analysis.",
  "action": "check_skill_availability"
}
```

### Scenario 2: Timeout on Job Analysis

If a subagent task exceeds 60 seconds:
- Log the timeout event
- Continue with results from successful tasks if ≥2 completed
- If only 1 or 0 tasks completed: Return error

**Response:**
```json
{
  "status": "error",
  "error": "Analysis timeout: Only 2 of 3 jobs analyzed successfully. Insufficient data for permafail determination.",
  "completed_jobs": 2,
  "action": "retry_with_single_job"
}
```

### Scenario 3: Invalid Job URLs

If URL validation fails:
- Return status: "error"
- Return specific validation error message
- Do NOT attempt analysis

**Response:**
```json
{
  "status": "error",
  "error": "Invalid job URL format: 'url3' is not a valid Prow job URL",
  "invalid_url": "url3",
  "action": "provide_valid_urls"
}
```

### Scenario 4: Job Names Don't Match

If the job_name parameter doesn't match the actual job names extracted from URLs:
- Return status: "error"
- Return the expected vs actual job names

**Response:**
```json
{
  "status": "error",
  "error": "Job name mismatch. Expected 'pull-ci-job-xyz' but found 'pull-ci-job-abc' in run 2",
  "expected_job": "pull-ci-job-xyz",
  "actual_job": "pull-ci-job-abc",
  "action": "provide_matching_job_urls"
}
```

### Scenario 5: Failure to Extract Failure Details

If the ci-prow-navigation response doesn't contain expected failure information:
- Mark this run as "incomplete"
- Continue with other runs
- If ≥2 runs have valid failure data, proceed with analysis
- Otherwise, return error

**Response:**
```json
{
  "status": "incomplete",
  "warning": "Run 1 analysis incomplete: could not extract failure details",
  "completed_jobs": 2,
  "incomplete_jobs": 1,
  "permafail": "unknown",
  "recommendation": "Review job logs manually or retry analysis"
}
```

## Examples

### Example 1: Permafail - Identical Failing Test

**Input:**
```json
{
  "failure_urls": [
    "https://prow.ci.openshift.org/view/gs://..../logs/pull-ci-openshift-origin-master-e2e-aws/1234567",
    "https://prow.ci.openshift.org/view/gs://..../logs/pull-ci-openshift-origin-master-e2e-aws/1234568",
    "https://prow.ci.openshift.org/view/gs://..../logs/pull-ci-openshift-origin-master-e2e-aws/1234569"
  ],
  "job_name": "pull-ci-openshift-origin-master-e2e-aws",
  "pr_info": {
    "pr_number": 12345,
    "repository": "openshift/origin"
  }
}
```

**ci-prow-navigation Results for all 3 runs:**
- Run 1: Failed tests = ["[sig-api] API discovery should provide capability information"]
- Run 2: Failed tests = ["[sig-api] API discovery should provide capability information"]
- Run 3: Failed tests = ["[sig-api] API discovery should provide capability information"]

**Output:**
```json
{
  "permafail": true,
  "confidence": 0.99,
  "reason": "All 3 consecutive runs fail with identical test: '[sig-api] API discovery should provide capability information'. This is a systematic permanent failure.",
  "failure_type": "test_failure",
  "signatures": [
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/pull-ci-openshift-origin-master-e2e-aws/1234567",
      "tests": ["[sig-api] API discovery should provide capability information"],
      "test_count": 1
    },
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/pull-ci-openshift-origin-master-e2e-aws/1234568",
      "tests": ["[sig-api] API discovery should provide capability information"],
      "test_count": 1
    },
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/pull-ci-openshift-origin-master-e2e-aws/1234569",
      "tests": ["[sig-api] API discovery should provide capability information"],
      "test_count": 1
    }
  ],
  "common_tests": ["[sig-api] API discovery should provide capability information"]
}
```

### Example 2: Permafail Despite Mixed Failure Types

**Input:**
```json
{
  "failure_urls": [
    "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1111",
    "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1112",
    "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1113",
    "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1114",
    "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1115",
    "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1116",
    "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1117"
  ],
  "job_name": "periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node",
  "pr_info": {
    "pr_number": 3186,
    "repository": "openshift/ovn-kubernetes"
  }
}
```

**ci-prow-navigation Results:**
- Run 1: Infrastructure failure = "Cluster creation timeout" (infra_failure)
- Run 2: Failed tests = ["[sig-network] Networking should provide connectivity"] (test_failure)
- Run 3: Infrastructure failure = "AWS quota exceeded" (infra_failure)
- Run 4: Failed tests = ["[sig-network] Networking should provide connectivity"] (test_failure)
- Run 5: Failed tests = ["[sig-network] Networking should provide connectivity"] (test_failure)
- Run 6: Infrastructure failure = "Cluster creation timeout" (infra_failure)
- Run 7: Failed tests = ["[sig-network] Networking should provide connectivity"] (test_failure)

**Analysis:**
- 7 total runs: 3 infra_failures, 4 test_failures
- Test failures: 4/4 (100%) have identical failing test
- Infra failures: 3 runs, but different errors (not a permafail pattern in infra)
- Verdict: **PERMAFAIL = TRUE** based on test failure group

**Output:**
```json
{
  "permafail": true,
  "confidence": 0.99,
  "reason": "All 4 runs that reached e2e tests failed on '[sig-network] Networking should provide connectivity' (100% match). 3 additional runs failed during infrastructure setup (cluster creation, AWS quota) and are not relevant to this test failure pattern. This is a systematic test failure caused by the PR changes.",
  "failure_type": "test_failure",
  "signatures": [
    {
      "type": "infra_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1111",
      "error": "Cluster creation timeout",
      "error_hash": "a1b2c3d4"
    },
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1112",
      "tests": ["[sig-network] Networking should provide connectivity"],
      "test_count": 1
    },
    {
      "type": "infra_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1113",
      "error": "AWS quota exceeded",
      "error_hash": "e5f6g7h8"
    },
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1114",
      "tests": ["[sig-network] Networking should provide connectivity"],
      "test_count": 1
    },
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1115",
      "tests": ["[sig-network] Networking should provide connectivity"],
      "test_count": 1
    },
    {
      "type": "infra_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1116",
      "error": "Cluster creation timeout",
      "error_hash": "a1b2c3d4"
    },
    {
      "type": "test_failure",
      "url": "https://prow.ci.openshift.org/view/gs://..../logs/periodic-ci-openshift-release-main-ci-4.19-e2e-aws-upgrade-ovn-single-node/1117",
      "tests": ["[sig-network] Networking should provide connectivity"],
      "test_count": 1
    }
  ],
  "common_tests": ["[sig-network] Networking should provide connectivity"]
}
```

### Example 3: Non-Permafail - No Consistent Pattern

**Input:** 3 runs with different test failures

**ci-prow-navigation Results:**
- Run 1: Failed tests = ["[sig-network] networking should support networking"] (test_failure)
- Run 2: Failed tests = ["[sig-storage] storage should support volumes"] (test_failure)
- Run 3: Failed tests = ["[sig-api] API discovery should work"] (test_failure)

**Output:**
```json
{
  "permafail": false,
  "confidence": 0.70,
  "reason": "No consistent failure pattern detected. Each of the 3 runs failed with different tests: networking, storage, API discovery. This indicates flaky/non-deterministic behavior rather than a systematic permafail.",
  "failure_type": "test_failure",
  "signatures": [
    {
      "type": "test_failure",
      "url": "...",
      "tests": ["[sig-network] networking should support networking"],
      "test_count": 1
    },
    {
      "type": "test_failure",
      "url": "...",
      "tests": ["[sig-storage] storage should support volumes"],
      "test_count": 1
    },
    {
      "type": "test_failure",
      "url": "...",
      "tests": ["[sig-api] API discovery should work"],
      "test_count": 1
    }
  ]
}
```

## Technical Details

### How ci-prow-Navigation Is Used

The `ci-prow-navigation` skill serves as the abstraction layer for accessing Prow job data. This skill:

1. **Accepts a job URL** and navigates the Prow UI/logs
2. **Extracts failure information** from structured test output and logs
3. **Returns normalized data** in a consistent format regardless of Prow version or test framework

This decoupling allows the detect-permafail skill to remain stable even as the underlying Prow infrastructure changes.

### Parallel Execution Strategy

Three independent tasks are spawned using the `Skill` tool:

```
Subagent 1 ━━━ ci-prow-navigation(url[0]) ━━━┐
Subagent 2 ━━━ ci-prow-navigation(url[1]) ━━━┼━━━ Wait for all ━━━ Compare signatures
Subagent 3 ━━━ ci-prow-navigation(url[2]) ━━━┘
```

**Benefits:**
- All 3 job analyses run concurrently (3x faster than sequential)
- If one task fails, others continue (fault tolerance)
- Better utilization of available resources

**Synchronization:**
- Use Promise.all() or equivalent in implementation
- 60-second timeout per subagent task
- Require ≥2 successful results to proceed with analysis

### Error Message Normalization

Normalize infrastructure error messages for comparison:

1. Remove timestamps: `2025-05-12T14:32:10Z` → ""
2. Remove build IDs: `build-12345-xyz` → ""
3. Remove resource names with IDs: `pod-abc123xyz` → "pod-*"
4. Remove request/limit values: Numbers in memory/CPU specs → ""
5. Keep: Error classification, core message, error type

**Example normalization:**
```
Input:  "Pod evicted at 2025-05-12T14:32:10Z (build-12345): insufficient memory (512M < 1Gi required)"
Output: "Pod evicted: insufficient memory (* < *Gi required)"
```

### Confidence Scoring

Confidence reflects how certain the permafail verdict is:

- **0.99**: All 3 runs have identical failure signature (test names or error hashes)
- **0.95**: All 3 runs have ≥1 common failing test in test_failure
- **0.92**: All 3 runs have >80% similar error messages in infra_failure
- **0.85**: 2 of 3 runs share common failure or mixed types detected
- **0.70**: Insufficient data or ambiguous failure patterns

Use confidence to determine remediation priority:
- Confidence ≥ 0.95: High priority permafail, block PR merge
- Confidence 0.85-0.94: Medium priority, warn but allow manual override
- Confidence < 0.85: Low confidence verdict, require manual review
