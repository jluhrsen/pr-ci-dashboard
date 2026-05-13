# api/analysis.py
from flask import Blueprint, request, jsonify, current_app
from utils.db import store_analysis, get_permafail_status
from utils.ai_analyzer import analyze_permafail

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

        if len(data["job_urls"]) != 3:
            return jsonify({"error": "Exactly 3 job URLs required"}), 400

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

        # Check if AI analyzer returned an error
        if "error" in result:
            return jsonify({
                "permafail": False,
                "reason": result["error"],
                "test_names": []
            })

        # Cache results for each URL
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
