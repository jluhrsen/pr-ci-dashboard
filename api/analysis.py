# api/analysis.py
import json
from flask import Blueprint, request, jsonify, current_app, Response
from utils.db import store_analysis, get_permafail_status, set_override, delete_cached_analyses
from utils.ai_analyzer import analyze_permafail, analyze_permafail_streaming

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
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        required_fields = ["pr", "repo", "job_name", "job_urls"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        # Allow 2-10 URLs for permafail detection patterns:
        # - 3/3 (100% match in last 3)
        # - 4/5 (80% match in last 5)
        # - 7/10 (70% match in last 10)
        if len(data["job_urls"]) < 2 or len(data["job_urls"]) > 10:
            return jsonify({"error": "2 to 10 job URLs required"}), 400

        # Parse PR info
        pr_parts = data["pr"].split("#")
        if len(pr_parts) != 2:
            return jsonify({"error": "Invalid PR format"}), 400

        try:
            pr_number = int(pr_parts[1])
        except ValueError:
            return jsonify({"error": "Invalid PR number: must be an integer"}), 400

        # Run AI analysis
        result = analyze_permafail(
            data["job_urls"],
            data["job_name"],
            data["pr"]
        )

        # Cache results for each URL (for both success and error cases)
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

        # Check if AI analyzer returned an error
        if "error" in result:
            return jsonify({
                "permafail": False,
                "reason": "Analysis unavailable, manual check needed",
                "error": result["error"]
            })

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
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        required_fields = ["pr", "repo", "job_name", "job_urls"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Missing field: {field}"}), 400

        if len(data["job_urls"]) < 2 or len(data["job_urls"]) > 10:
            return jsonify({"error": "2 to 10 job URLs required"}), 400

        # Parse PR info
        pr_parts = data["pr"].split("#")
        if len(pr_parts) != 2:
            return jsonify({"error": "Invalid PR format"}), 400

        try:
            pr_number = int(pr_parts[1])
        except ValueError:
            return jsonify({"error": "Invalid PR number: must be an integer"}), 400

        # Capture db_path before generator starts (while in Flask request context)
        db_path = current_app.config.get('DB_PATH')

        def generate():
            """Generator function for SSE stream"""
            final_result = None

            # Stream analysis output
            for event_json in analyze_permafail_streaming(
                data["job_urls"],
                data["job_name"],
                data["pr"]
            ):
                event = json.loads(event_json)

                if event["type"] == "result":
                    final_result = event["data"]

                # Send as SSE event
                yield f"data: {event_json}\n\n"

            # Cache results if we got a final result
            if final_result:
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
