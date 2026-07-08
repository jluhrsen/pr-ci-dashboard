# api/analysis.py
import json
from flask import Blueprint, request, jsonify, current_app, Response
from ..utils.db import store_analysis, get_permafail_status, get_pr_permafail_status, set_override, delete_cached_analyses, normalize_permafail_result, record_audit
from ..utils.ai_analyzer import analyze_permafail, analyze_permafail_streaming
from ..utils.session_store import get_session_google, current_actor, session_id
from ..utils import validation
from ..utils import rate_limit

# Analyses spawn Claude subprocesses - the expensive operation to abuse
ANALYZE_RATE = (4, 60)


def _analyze_rate_limited():
    return not rate_limit.allow(f'analyze:{session_id()}', *ANALYZE_RATE)


def _session_google_adc():
    """Per-user Google credentials for Vertex, or None (pod-level fallback)."""
    google = get_session_google()
    return google['adc'] if google else None


def _validate_analyze_request(data):
    """Validate an analyze request body. These values are embedded in the
    Claude analysis prompt and determine which artifacts get fetched, so
    they are strictly checked.

    Returns:
        (pr_number, None) on success, (None, (response, status)) on failure
    """
    if not data:
        return None, (jsonify({"error": "Invalid JSON"}), 400)

    for field in ["pr", "repo", "job_name", "job_urls"]:
        if field not in data:
            return None, (jsonify({"error": f"Missing field: {field}"}), 400)

    # Allow 2-10 URLs for permafail detection patterns:
    # - 3/3 (100% match in last 3)
    # - 4/5 (80% match in last 5)
    # - 7/10 (70% match in last 10)
    if not isinstance(data["job_urls"], list) or not (2 <= len(data["job_urls"]) <= 10):
        return None, (jsonify({"error": "2 to 10 job URLs required"}), 400)
    if not validation.valid_job_urls(data["job_urls"]):
        return None, (jsonify({"error": f"Job URLs must start with {validation.JOB_URL_PREFIX}"}), 400)

    if not validation.valid_repo_full(data["repo"]):
        return None, (jsonify({"error": "Invalid repo"}), 400)
    if not validation.valid_job_name(data["job_name"]):
        return None, (jsonify({"error": "Invalid job name"}), 400)

    # PR format: "owner/repo#123"
    pr_parts = data["pr"].split("#") if isinstance(data["pr"], str) else []
    if len(pr_parts) != 2 or not validation.valid_repo_full(pr_parts[0]):
        return None, (jsonify({"error": "Invalid PR format"}), 400)
    try:
        pr_number = int(pr_parts[1])
    except ValueError:
        return None, (jsonify({"error": "Invalid PR number: must be an integer"}), 400)
    if not validation.valid_pr_number(pr_number):
        return None, (jsonify({"error": "Invalid PR number"}), 400)

    return pr_number, None

analysis_bp = Blueprint('analysis', __name__)

@analysis_bp.route('/api/jobs/analyze', methods=['POST'])
def analyze_job():
    """
    Trigger permafail analysis for a job

    Request: {
        "pr": "openshift/ovn-kubernetes#1234",
        "repo": "openshift/ovn-kubernetes",
        "job_name": "e2e-aws-ovn",
        "job_urls": ["url1", "url2", "url3"]
    }

    Response: {
        "permafail": bool,
        "reason": str,
        "test_names": []
    }
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        pr_number, error = _validate_analyze_request(data)
        if error:
            return error

        if _analyze_rate_limited():
            return jsonify({"error": "Rate limit exceeded; try again shortly"}), 429

        # Run AI analysis (as the signed-in Google user when available)
        result = analyze_permafail(
            data["job_urls"],
            data["job_name"],
            data["pr"],
            google_adc=_session_google_adc()
        )
        record_audit(current_actor(), 'analyze',
                     f"{data['pr']} {data['job_name']}",
                     f"error: {result['error']}" if 'error' in result
                     else f"permafail={normalize_permafail_result(result).get('permafail')}",
                     db_path=current_app.config.get('DB_PATH'))
        result = normalize_permafail_result(result)

        # Check if AI analyzer returned an error
        if "error" in result:
            print(f"[DEBUG] AI analysis failed: {result.get('error')}")
            print(f"[DEBUG] NOT caching error result to allow retry")
            return jsonify({
                "permafail": False,
                "reason": "Analysis unavailable, manual check needed",
                "error": result["error"]
            })

        # Cache successful results only (don't cache errors)
        db_path = current_app.config.get('DB_PATH')
        for i, url in enumerate(data["job_urls"]):
            signature = result.get("signatures", [])[i] if i < len(result.get("signatures", [])) else {}
            store_analysis(
                job_url=url,
                pr_number=pr_number,
                repo=data["repo"],
                job_name=data["job_name"],
                signature=signature,
                permafail_result=result,
                db_path=db_path
            )

        return jsonify({
            "permafail": result.get("permafail", False),
            "reason": result.get("reason", ""),
            "test_names": result.get("common_tests", [])
        })

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@analysis_bp.route('/api/jobs/analyze-stream', methods=['POST'])
def analyze_job_stream():
    """
    Trigger permafail analysis with streaming output via SSE

    Request: {
        "pr": "openshift/ovn-kubernetes#1234",
        "repo": "openshift/ovn-kubernetes",
        "job_name": "e2e-aws-ovn",
        "job_urls": ["url1", "url2", "url3"]
    }

    Response: Server-Sent Events stream with:
        - type: "output" - line of output from Claude CLI
        - type: "result" - final analysis result
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    try:
        pr_number, error = _validate_analyze_request(data)
        if error:
            return error

        if _analyze_rate_limited():
            return jsonify({"error": "Rate limit exceeded; try again shortly"}), 429

        # Capture db_path and session credentials before the generator starts
        # (both need the Flask request context)
        db_path = current_app.config.get('DB_PATH')
        google_adc = _session_google_adc()
        actor = current_actor()

        def generate():
            """Generator function for SSE stream"""
            final_result = None

            try:
                # Stream analysis output (as the signed-in Google user when available)
                for event_json in analyze_permafail_streaming(
                    data["job_urls"],
                    data["job_name"],
                    data["pr"],
                    google_adc=google_adc
                ):
                    event = json.loads(event_json)

                    if event["type"] == "result":
                        final_result = normalize_permafail_result(event["data"])
                        event["data"] = final_result
                        event_json = json.dumps(event)

                    # Send as SSE event
                    yield f"data: {event_json}\n\n"
            finally:
                # Cache results even if client disconnects (runs in finally block)
                print(f"[DEBUG] Finally block: final_result={final_result is not None}, has_error={'error' in final_result if final_result else 'N/A'}")
                if final_result:
                    # Cache ALL results including errors, because user saw the terminal output
                    # and took action. Not caching means they have to analyze again.
                    print(f"[DEBUG] Caching analysis for {data['job_name']}: {len(data['job_urls'])} URLs, permafail={final_result.get('permafail')}, has_error={'error' in final_result}")
                    for i, url in enumerate(data["job_urls"]):
                        signature = final_result.get("signatures", [])[i] if i < len(final_result.get("signatures", [])) else {}
                        store_analysis(
                            job_url=url,
                            pr_number=pr_number,
                            repo=data["repo"],
                            job_name=data["job_name"],
                            signature=signature,
                            permafail_result=final_result,
                            db_path=db_path
                        )
                    print(f"[DEBUG] Successfully cached {len(data['job_urls'])} URL(s) for {data['job_name']}")
                else:
                    print(f"[DEBUG] NOT caching - no final_result")

                # Audit with the actor resolved back in request context
                if final_result is not None:
                    record_audit(actor, 'analyze-stream',
                                 f"{data['pr']} {data['job_name']}",
                                 f"error: {final_result['error']}" if 'error' in final_result
                                 else f"permafail={final_result.get('permafail')}",
                                 db_path=db_path)

        return Response(generate(), mimetype='text/event-stream')

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@analysis_bp.route('/api/jobs/override', methods=['POST'])
def override_permafail():
    """
    Clear permafail flag for a job

    Request: {"job_url": "https://..."}
    Response: {"success": bool}
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    if not data or 'job_url' not in data:
        return jsonify({"error": "Missing job_url"}), 400

    try:
        db_path = current_app.config.get('DB_PATH')
        set_override(data['job_url'], db_path=db_path)
        record_audit(current_actor(), 'override', data['job_url'], 'success',
                     db_path=db_path)

        return jsonify({"success": True})

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@analysis_bp.route('/api/jobs/delete-cache', methods=['POST'])
def delete_cache():
    """
    Delete cached analysis for job URLs and optionally re-analyze

    Request: {
        "job_urls": ["url1", "url2", ...],
        "reanalyze": bool (optional, default false)
    }
    Response: {"success": bool, "deleted_count": int}
    """
    try:
        data = request.get_json(force=True, silent=False)
    except Exception:
        return jsonify({"error": "Invalid JSON"}), 400

    if not data or 'job_urls' not in data:
        return jsonify({"error": "Missing job_urls"}), 400

    try:
        db_path = current_app.config.get('DB_PATH')
        deleted_count = delete_cached_analyses(data['job_urls'], db_path=db_path)
        record_audit(current_actor(), 'delete-cache',
                     f"{len(data['job_urls'])} URL(s)", f"deleted={deleted_count}",
                     db_path=db_path)

        return jsonify({
            "success": True,
            "deleted_count": deleted_count
        })

    except Exception as e:
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@analysis_bp.route('/api/jobs/status', methods=['GET'])
def get_job_status():
    """
    Get permafail status for multiple jobs

    Query: ?job_urls=["url1", "url2", ...]
    Response: {
        "url1": {"permafail": bool, "reason": str, "override": bool},
        ...
    }
    """
    job_urls_param = request.args.get('job_urls')
    if not job_urls_param:
        return jsonify({"error": "Missing job_urls parameter"}), 400

    try:
        job_urls = json.loads(job_urls_param)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON in job_urls"}), 400

    try:
        db_path = current_app.config.get('DB_PATH')
        print(f"[DEBUG] Cache check for {len(job_urls)} URLs")
        print(f"[DEBUG] Using DB path: {db_path}")
        status = get_permafail_status(job_urls, db_path=db_path)
        print(f"[DEBUG] Found {len(status)} cached results out of {len(job_urls)} requested")

        # Log which URLs are missing from cache
        missing_urls = [url for url in job_urls if url not in status]
        if missing_urls:
            print(f"[DEBUG] URLs not in cache:")
            for url in missing_urls:
                print(f"[DEBUG]   {url[:100]}...")

        return jsonify(status)

    except Exception as e:
        print(f"[DEBUG] Cache check failed: {str(e)}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500


@analysis_bp.route('/api/pr/<owner>/<repo>/<int:number>/permafails', methods=['GET'])
def get_pr_permafails(owner, repo, number):
    """
    Get all permafail jobs for a PR

    Response: {
        "job_name1": {"permafail": true, "reason": str, "override": false, "job_urls": [...]},
        "job_name2": {...},
        ...
    }
    """
    try:
        db_path = current_app.config.get('DB_PATH')
        repo_full = f"{owner}/{repo}"

        print(f"[DEBUG] Fetching permafails for {repo_full}#{number}")
        jobs = get_pr_permafail_status(repo_full, number, db_path=db_path)
        print(f"[DEBUG] Found {len(jobs)} permafail job(s)")

        return jsonify(jobs)

    except Exception as e:
        print(f"[DEBUG] Get PR permafails failed: {str(e)}")
        return jsonify({"error": f"Internal server error: {str(e)}"}), 500
