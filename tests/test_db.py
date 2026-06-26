import pytest
import sqlite3
import os
import json
from utils.db import init_db, store_analysis, get_permafail_status, get_pr_permafail_status, clear_override

def test_init_db_creates_tables(tmp_path):
    """Test that init_db creates job_analyses table with correct schema"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Check table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_analyses'")
        assert cursor.fetchone() is not None

        # Check schema
        cursor.execute("PRAGMA table_info(job_analyses)")
        columns = {row[1]: row[2] for row in cursor.fetchall()}

        assert columns['job_url'] == 'TEXT'
        assert columns['pr_number'] == 'INTEGER'
        assert columns['repo'] == 'TEXT'
        assert columns['job_name'] == 'TEXT'
        assert columns['signature'] == 'TEXT'
        assert columns['analyzed_at'] == 'TIMESTAMP'
        assert columns['permafail_result'] == 'TEXT'
        assert columns['override'] == 'BOOLEAN'
    finally:
        if conn:
            conn.close()


def test_init_db_idempotent(tmp_path):
    """Test that init_db can be called multiple times without error"""
    db_path = tmp_path / "test.db"

    # Call init_db multiple times
    init_db(str(db_path))
    init_db(str(db_path))
    init_db(str(db_path))

    # Verify table still exists and is intact
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='job_analyses'")
        assert cursor.fetchone() is not None
    finally:
        if conn:
            conn.close()


def test_init_db_with_existing_database(tmp_path):
    """Test that init_db works with an existing database"""
    db_path = tmp_path / "test.db"

    # Create initial database
    init_db(str(db_path))

    # Insert some test data
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO job_analyses
            (job_url, pr_number, repo, job_name, signature, permafail_result, override)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("http://example.com", 123, "test-repo", "test-job", "sig123", "permafail", 0))
        conn.commit()
    finally:
        if conn:
            conn.close()

    # Call init_db again
    init_db(str(db_path))

    # Verify data is still there
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM job_analyses")
        count = cursor.fetchone()[0]
        assert count == 1
    finally:
        if conn:
            conn.close()


def test_init_db_override_check_constraint(tmp_path):
    """Test that override column has CHECK constraint for boolean values"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        # Valid values should work
        cursor.execute("""
            INSERT INTO job_analyses
            (job_url, pr_number, repo, job_name, signature, permafail_result, override)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("http://example1.com", 1, "repo1", "job1", "sig1", "permafail", 0))

        cursor.execute("""
            INSERT INTO job_analyses
            (job_url, pr_number, repo, job_name, signature, permafail_result, override)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("http://example2.com", 2, "repo2", "job2", "sig2", "permafail", 1))

        conn.commit()

        # Invalid value should fail
        with pytest.raises(sqlite3.IntegrityError):
            cursor.execute("""
                INSERT INTO job_analyses
                (job_url, pr_number, repo, job_name, signature, permafail_result, override)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, ("http://example3.com", 3, "repo3", "job3", "sig3", "permafail", 2))
            conn.commit()
    finally:
        if conn:
            conn.close()


def test_init_db_invalid_path():
    """Test that init_db raises RuntimeError for invalid database paths"""
    # Try to create database in non-existent directory
    invalid_path = "/nonexistent/directory/that/does/not/exist/database.db"

    with pytest.raises(RuntimeError, match="Failed to initialize database"):
        init_db(invalid_path)


def test_store_analysis_inserts_new_record(tmp_path):
    """Test that store_analysis inserts a new analysis result"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    job_url = "https://prow.ci.openshift.org/view/gs/123"
    signature = {"type": "test_failure", "tests": ["TestFoo"]}
    permafail_result = {"permafail": True, "reason": "TestFoo failed"}

    store_analysis(
        job_url=job_url,
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature=signature,
        permafail_result=permafail_result,
        db_path=str(db_path)
    )

    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM job_analyses WHERE job_url = ?", (job_url,))
        row = cursor.fetchone()

        assert row is not None
        assert row[0] == job_url  # job_url
        assert row[1] == 1234  # pr_number
        assert row[2] == "openshift/ovn-kubernetes"  # repo
        assert row[3] == "e2e-aws-ovn"  # job_name
        assert json.loads(row[4]) == signature  # signature
        assert row[5] is not None  # analyzed_at
        assert json.loads(row[6]) == permafail_result  # permafail_result
        assert row[7] == 0  # override (default)
    finally:
        if conn:
            conn.close()


def test_store_analysis_updates_existing(tmp_path):
    """Test that store_analysis updates existing record (INSERT OR REPLACE)"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    job_url = "https://prow.ci.openshift.org/view/gs/456"
    signature_v1 = {"type": "test_failure", "tests": ["TestBar"]}
    permafail_result_v1 = {"permafail": False, "reason": "No permafail"}

    # First store
    store_analysis(
        job_url=job_url,
        pr_number=5678,
        repo="openshift/kubernetes",
        job_name="e2e-aws",
        signature=signature_v1,
        permafail_result=permafail_result_v1,
        db_path=str(db_path)
    )

    # Update with new data
    signature_v2 = {"type": "test_failure", "tests": ["TestBar", "TestBaz"]}
    permafail_result_v2 = {"permafail": True, "reason": "New failure detected"}

    store_analysis(
        job_url=job_url,
        pr_number=9999,
        repo="openshift/kubernetes-updated",
        job_name="e2e-aws-updated",
        signature=signature_v2,
        permafail_result=permafail_result_v2,
        db_path=str(db_path)
    )

    # Verify update occurred
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM job_analyses WHERE job_url = ?", (job_url,))
        count = cursor.fetchone()[0]
        assert count == 1  # Still only one record

        cursor.execute("SELECT * FROM job_analyses WHERE job_url = ?", (job_url,))
        row = cursor.fetchone()

        assert row[1] == 9999  # Updated pr_number
        assert row[2] == "openshift/kubernetes-updated"  # Updated repo
        assert row[3] == "e2e-aws-updated"  # Updated job_name
        assert json.loads(row[4]) == signature_v2  # Updated signature
        assert json.loads(row[6]) == permafail_result_v2  # Updated permafail_result
    finally:
        if conn:
            conn.close()


def test_store_analysis_invalid_path():
    """Test that store_analysis raises RuntimeError for invalid database paths"""
    invalid_path = "/nonexistent/directory/that/does/not/exist/database.db"

    with pytest.raises(RuntimeError, match="Failed to store analysis"):
        store_analysis(
            job_url="https://example.com/job",
            pr_number=123,
            repo="test-repo",
            job_name="test-job",
            signature={"type": "test"},
            permafail_result={"permafail": False},
            db_path=invalid_path
        )


def test_store_analysis_database_error_handling(tmp_path):
    """Test that store_analysis handles database errors gracefully"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Use a non-writable path after creating the db to simulate error
    # (we'll pass a path that looks valid but can't be written to)
    # Create a read-only directory
    readonly_dir = tmp_path / "readonly"
    readonly_dir.mkdir()
    readonly_db = readonly_dir / "test.db"
    readonly_dir.chmod(0o444)  # Make directory read-only

    try:
        with pytest.raises(RuntimeError, match="Failed to store analysis"):
            store_analysis(
                job_url="https://example.com/job",
                pr_number=123,
                repo="test-repo",
                job_name="test-job",
                signature={"type": "test"},
                permafail_result={"permafail": False},
                db_path=str(readonly_db)
            )
    finally:
        # Restore permissions for cleanup
        readonly_dir.chmod(0o755)


def test_get_permafail_status_returns_dict(tmp_path):
    """Test that get_permafail_status returns correct status for job URLs"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Store some analysis results
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/123",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={"type": "test_failure", "tests": ["TestFoo"]},
        permafail_result={"permafail": True, "reason": "TestFoo failed"},
        db_path=str(db_path)
    )

    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/456",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-gcp-ovn",
        signature={"type": "test_failure", "tests": ["TestBar"]},
        permafail_result={"permafail": False, "reason": "Flaky"},
        db_path=str(db_path)
    )

    job_urls = [
        "https://prow.ci.openshift.org/view/gs/123",
        "https://prow.ci.openshift.org/view/gs/456",
        "https://prow.ci.openshift.org/view/gs/789"  # Not in DB
    ]

    result = get_permafail_status(job_urls, str(db_path))

    # Check first URL (permafail)
    assert "https://prow.ci.openshift.org/view/gs/123" in result
    assert result["https://prow.ci.openshift.org/view/gs/123"]["permafail"] is True
    assert result["https://prow.ci.openshift.org/view/gs/123"]["reason"] == "TestFoo failed"
    assert result["https://prow.ci.openshift.org/view/gs/123"]["override"] is False

    # Check second URL (not permafail)
    assert "https://prow.ci.openshift.org/view/gs/456" in result
    assert result["https://prow.ci.openshift.org/view/gs/456"]["permafail"] is False
    assert result["https://prow.ci.openshift.org/view/gs/456"]["override"] is False

    # Check third URL (not in database)
    assert "https://prow.ci.openshift.org/view/gs/789" not in result


def test_get_permafail_status_empty_list(tmp_path):
    """Test that get_permafail_status handles empty list correctly"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    result = get_permafail_status([], str(db_path))

    assert result == {}


def test_get_permafail_status_database_error(tmp_path):
    """Test that get_permafail_status raises RuntimeError on database error"""
    invalid_path = tmp_path / "nonexistent" / "test.db"

    with pytest.raises(RuntimeError) as exc_info:
        get_permafail_status(["https://example.com"], str(invalid_path))

    assert "Failed to get permafail status" in str(exc_info.value)


def test_get_permafail_status_with_override(tmp_path):
    """Test that get_permafail_status correctly returns override=True"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Store analysis
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/123",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={"type": "test_failure", "tests": ["TestFoo"]},
        permafail_result={"permafail": True, "reason": "TestFoo failed"},
        db_path=str(db_path)
    )

    # Manually set override to 1
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("UPDATE job_analyses SET override = 1 WHERE job_url = ?",
                      ("https://prow.ci.openshift.org/view/gs/123",))
        conn.commit()
    finally:
        if conn:
            conn.close()

    # Verify override is True
    result = get_permafail_status(["https://prow.ci.openshift.org/view/gs/123"], str(db_path))

    assert result["https://prow.ci.openshift.org/view/gs/123"]["override"] is True


def test_get_permafail_status_supports_legacy_is_permafail_key(tmp_path):
    """Test cached analysis using is_permafail is normalized to permafail=True"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    job_url = "https://prow.ci.openshift.org/view/gs/legacy"
    store_analysis(
        job_url=job_url,
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={},
        permafail_result={
            "is_permafail": True,
            "reason": "3/3 runs share common test failures"
        },
        db_path=str(db_path)
    )

    result = get_permafail_status([job_url], str(db_path))

    assert result[job_url]["permafail"] is True
    assert result[job_url]["reason"] == "3/3 runs share common test failures"


def test_get_permafail_status_malformed_json(tmp_path):
    """Test that get_permafail_status raises RuntimeError on malformed JSON"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Insert record with invalid JSON
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO job_analyses (job_url, pr_number, repo, job_name, signature, analyzed_at, permafail_result, override)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
        """, ("https://prow.ci.openshift.org/view/gs/999", 1234, "test/repo", "job", "{}", "2024-01-01", "INVALID JSON"))
        conn.commit()
    finally:
        if conn:
            conn.close()

    # Verify RuntimeError is raised
    with pytest.raises(RuntimeError) as exc_info:
        get_permafail_status(["https://prow.ci.openshift.org/view/gs/999"], str(db_path))

    assert "Invalid JSON in database" in str(exc_info.value)


def test_clear_override_resets_flag(tmp_path):
    """Test that clear_override sets override to 0"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    job_url = "https://prow.ci.openshift.org/view/gs/123"

    # Store analysis
    store_analysis(
        job_url=job_url,
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={"type": "test_failure", "tests": ["TestFoo"]},
        permafail_result={"permafail": True, "reason": "TestFoo failed"},
        db_path=str(db_path)
    )

    # Set override to 1 (simulating user previously cleared permafail)
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("UPDATE job_analyses SET override = 1 WHERE job_url = ?", (job_url,))
        conn.commit()
    finally:
        if conn:
            conn.close()

    # Clear override
    clear_override(job_url, str(db_path))

    # Verify override is now 0
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("SELECT override FROM job_analyses WHERE job_url = ?", (job_url,))
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == 0
    finally:
        if conn:
            conn.close()


def test_get_pr_permafail_status_groups_by_job_name(tmp_path):
    """Test that get_pr_permafail_status groups multiple URLs by job_name"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Store multiple failures for same job
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/123",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={"type": "test_failure"},
        permafail_result={"permafail": True, "reason": "Flaky test"},
        db_path=str(db_path)
    )
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/456",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={"type": "test_failure"},
        permafail_result={"permafail": True, "reason": "Flaky test"},
        db_path=str(db_path)
    )

    result = get_pr_permafail_status("openshift/ovn-kubernetes", 1234, str(db_path))

    assert len(result) == 1
    assert "e2e-aws-ovn" in result
    assert result["e2e-aws-ovn"]["permafail"] is True
    assert result["e2e-aws-ovn"]["reason"] == "Flaky test"
    assert len(result["e2e-aws-ovn"]["job_urls"]) == 2


def test_get_pr_permafail_status_excludes_non_permafail(tmp_path):
    """Test that get_pr_permafail_status only returns permafail=True jobs"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Store permafail
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/123",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={},
        permafail_result={"permafail": True, "reason": "Flaky"},
        db_path=str(db_path)
    )

    # Store non-permafail
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/456",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-metal-ipi",
        signature={},
        permafail_result={"permafail": False, "reason": "OK"},
        db_path=str(db_path)
    )

    result = get_pr_permafail_status("openshift/ovn-kubernetes", 1234, str(db_path))

    # Only permafail job should be returned
    assert len(result) == 1
    assert "e2e-aws-ovn" in result
    assert "e2e-metal-ipi" not in result


def test_get_pr_permafail_status_excludes_override(tmp_path):
    """Test that get_pr_permafail_status excludes jobs with override=1"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    job_url = "https://prow.ci.openshift.org/view/gs/123"

    # Store permafail
    store_analysis(
        job_url=job_url,
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={},
        permafail_result={"permafail": True, "reason": "Flaky"},
        db_path=str(db_path)
    )

    # Set override
    conn = None
    try:
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()
        cursor.execute("UPDATE job_analyses SET override = 1 WHERE job_url = ?", (job_url,))
        conn.commit()
    finally:
        if conn:
            conn.close()

    result = get_pr_permafail_status("openshift/ovn-kubernetes", 1234, str(db_path))

    # Should be empty because override=1
    assert len(result) == 0


def test_get_pr_permafail_status_filters_by_pr(tmp_path):
    """Test that get_pr_permafail_status only returns jobs for the specified PR"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Store permafail for PR 1234
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/123",
        pr_number=1234,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={},
        permafail_result={"permafail": True, "reason": "Flaky"},
        db_path=str(db_path)
    )

    # Store permafail for PR 5678
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/456",
        pr_number=5678,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-metal-ipi",
        signature={},
        permafail_result={"permafail": True, "reason": "Timeout"},
        db_path=str(db_path)
    )

    result = get_pr_permafail_status("openshift/ovn-kubernetes", 1234, str(db_path))

    # Should only return PR 1234's job
    assert len(result) == 1
    assert "e2e-aws-ovn" in result
    assert "e2e-metal-ipi" not in result


def test_get_pr_permafail_status_empty_for_no_results(tmp_path):
    """Test that get_pr_permafail_status returns empty dict when no permafails exist"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    result = get_pr_permafail_status("openshift/ovn-kubernetes", 1234, str(db_path))

    assert result == {}


def test_get_pr_permafail_status_supports_legacy_is_permafail_key(tmp_path):
    """Test PR-level permafail status includes legacy is_permafail cached rows"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    store_analysis(
        job_url="https://prow.ci.openshift.org/view/gs/legacy",
        pr_number=3243,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn",
        signature={},
        permafail_result={
            "is_permafail": True,
            "reason": "3/3 runs share common test failures"
        },
        db_path=str(db_path)
    )

    result = get_pr_permafail_status("openshift/ovn-kubernetes", 3243, str(db_path))

    assert "e2e-aws-ovn" in result
    assert result["e2e-aws-ovn"]["permafail"] is True
    assert result["e2e-aws-ovn"]["reason"] == "3/3 runs share common test failures"


def test_verdict_permafail_overrides_false_boolean(tmp_path):
    """Test verdict: PERMAFAIL is treated as permafail even if permafail: false"""
    db_path = tmp_path / "test.db"
    init_db(str(db_path))

    # Store analysis with verdict-based schema
    store_analysis(
        job_url="https://prow.ci.openshift.org/view/1",
        pr_number=3243,
        repo="openshift/ovn-kubernetes",
        job_name="e2e-aws-ovn-local-gateway",
        signature={},
        permafail_result={
            "permafail": False,
            "verdict": "PERMAFAIL",
            "confidence": 1.0,
            "matching_runs": 3,
            "comparable_runs": 3,
            "failure_type": "test_failure",
            "all_common_tests": ["TestFoo", "TestBar"]
        },
        db_path=str(db_path)
    )

    # Test URL-level status
    status = get_permafail_status(["https://prow.ci.openshift.org/view/1"], str(db_path))
    assert status["https://prow.ci.openshift.org/view/1"]["permafail"] is True
    assert len(status["https://prow.ci.openshift.org/view/1"]["reason"]) > 0
    assert "PERMAFAIL" in status["https://prow.ci.openshift.org/view/1"]["reason"]

    # Test PR-level status
    pr_status = get_pr_permafail_status("openshift/ovn-kubernetes", 3243, str(db_path))
    assert "e2e-aws-ovn-local-gateway" in pr_status
    assert pr_status["e2e-aws-ovn-local-gateway"]["permafail"] is True
    assert len(pr_status["e2e-aws-ovn-local-gateway"]["reason"]) > 0


def test_normalize_permafail_synthesizes_reason_from_verdict(tmp_path):
    """Test normalize_permafail_result() synthesizes reason when missing"""
    from utils.db import normalize_permafail_result

    result = normalize_permafail_result({
        "permafail": False,
        "verdict": "PERMAFAIL",
        "confidence": 1.0,
        "matching_runs": 3,
        "comparable_runs": 3,
        "failure_type": "test_failure",
        "all_common_tests": ["TestFoo", "TestBar", "TestBaz"]
    })

    assert result["permafail"] is True
    assert "reason" in result
    assert len(result["reason"]) > 0
    assert "PERMAFAIL" in result["reason"]
    assert "3/3" in result["reason"]
    assert "100%" in result["reason"]

    string_confidence_result = normalize_permafail_result({
        "verdict": "PERMAFAIL",
        "confidence": "high"
    })

    assert string_confidence_result["permafail"] is True
    assert "Confidence: high" in string_confidence_result["reason"]
