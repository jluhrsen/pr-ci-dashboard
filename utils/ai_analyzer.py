import subprocess
import json
import os
import re


def analyze_permafail_streaming(job_urls, job_name, pr_info):
    """
    Analyze job URLs for permafail pattern using Claude Code CLI with streaming output

    Args:
        job_urls: List of 2-10 consecutive Prow job URLs
        job_name: Name of the job (e.g., "e2e-aws-ovn")
        pr_info: PR identifier (e.g., "openshift/ovn-kubernetes#1234")

    Yields:
        str: Lines of output from the Claude CLI process

    Returns:
        dict: Final analysis result (via final yield)
    """
    # Get the project root directory (where .claude-plugin/ exists)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Read both skill definitions
    detect_permafail_path = os.path.join(project_root, 'commands', 'detect-permafail.md')
    ci_prow_nav_path = os.path.join(project_root, '.claude', 'skills', 'ci-prow-navigation', 'SKILL.md')

    try:
        with open(detect_permafail_path, 'r') as f:
            detect_permafail_content = f.read()
        with open(ci_prow_nav_path, 'r') as f:
            ci_prow_nav_content = f.read()
    except FileNotFoundError as e:
        yield json.dumps({
            "type": "error",
            "message": f"Skill file not found: {e}"
        })
        return

    # Build prompt with skill definitions and instructions
    urls_json = json.dumps(job_urls)
    prompt = f"""{detect_permafail_content}

---SKILL---

{ci_prow_nav_content}

---TASK---

Using the detect-permafail logic and ci-prow-navigation skill defined above, analyze these jobs for permafail. Do NOT use the Skill tool — execute the ci-prow-navigation steps directly using WebFetch and Bash.

Jobs: {urls_json}
Job name: {job_name}
PR: {pr_info}

Return ONLY the final JSON result with no additional explanation."""

    cmd = [
        'claude',
        '--allowedTools', 'WebFetch,Bash',
        '--print'
    ]

    try:
        # Use Popen for streaming output
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
            cwd=project_root
        )

        # Write prompt to stdin and close it
        process.stdin.write(prompt)
        process.stdin.close()

        # Stream output line by line
        output_lines = []
        for line in process.stdout:
            output_lines.append(line)
            yield json.dumps({"type": "output", "line": line.rstrip('\n')})

        # Wait for process to complete
        return_code = process.wait(timeout=300)

        if return_code != 0:
            yield json.dumps({
                "type": "result",
                "data": {
                    "permafail": False,
                    "error": "Skill execution failed",
                    "signatures": []
                }
            })
            return

        # Parse final output
        full_output = ''.join(output_lines).strip()

        if not full_output:
            yield json.dumps({
                "type": "result",
                "data": {
                    "permafail": False,
                    "error": "Skill returned empty output",
                    "signatures": []
                }
            })
            return

        # Strip markdown code fences if present
        full_output = re.sub(r'^```(?:json)?\s*', '', full_output)
        full_output = re.sub(r'\s*```\s*$', '', full_output)
        full_output = full_output.strip()

        # Try parsing as pure JSON first
        try:
            result = json.loads(full_output)
            yield json.dumps({"type": "result", "data": result})
            return
        except json.JSONDecodeError:
            pass

        # If that fails, try to find JSON object in output
        json_start = full_output.rfind('{')
        if json_start == -1:
            yield json.dumps({
                "type": "result",
                "data": {
                    "permafail": False,
                    "error": f"No JSON found in skill output",
                    "signatures": []
                }
            })
            return

        json_str = full_output[json_start:]
        try:
            result = json.loads(json_str)
            yield json.dumps({"type": "result", "data": result})
        except json.JSONDecodeError as e:
            yield json.dumps({
                "type": "result",
                "data": {
                    "permafail": False,
                    "error": f"Failed to parse JSON from output: {e}",
                    "signatures": []
                }
            })

    except subprocess.TimeoutExpired:
        yield json.dumps({
            "type": "result",
            "data": {
                "permafail": False,
                "error": "Analysis timed out after 5 minutes",
                "signatures": []
            }
        })
    except Exception as e:
        yield json.dumps({
            "type": "result",
            "data": {
                "permafail": False,
                "error": f"Unexpected error: {e}",
                "signatures": []
            }
        })


def analyze_permafail(job_urls, job_name, pr_info):
    """
    Analyze job URLs for permafail pattern using Claude Code CLI

    Args:
        job_urls: List of 2-10 consecutive Prow job URLs
        job_name: Name of the job (e.g., "e2e-aws-ovn")
        pr_info: PR identifier (e.g., "openshift/ovn-kubernetes#1234")

    Returns:
        dict: Analysis result with permafail verdict and signatures.
              On error, returns dict with permafail=False, error message, and empty signatures list.
    """
    import os

    # Get the project root directory (where .claude-plugin/ exists)
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Read both skill definitions
    detect_permafail_path = os.path.join(project_root, 'commands', 'detect-permafail.md')
    ci_prow_nav_path = os.path.join(project_root, '.claude', 'skills', 'ci-prow-navigation', 'SKILL.md')

    try:
        with open(detect_permafail_path, 'r') as f:
            detect_permafail_content = f.read()
        with open(ci_prow_nav_path, 'r') as f:
            ci_prow_nav_content = f.read()
    except FileNotFoundError as e:
        return {
            "permafail": False,
            "error": f"Skill file not found: {e}",
            "signatures": []
        }

    # Build prompt with skill definitions and instructions
    urls_json = json.dumps(job_urls)
    prompt = f"""{detect_permafail_content}

---SKILL---

{ci_prow_nav_content}

---TASK---

Using the detect-permafail logic and ci-prow-navigation skill defined above, analyze these jobs for permafail. Do NOT use the Skill tool — execute the ci-prow-navigation steps directly using WebFetch and Bash.

Jobs: {urls_json}
Job name: {job_name}
PR: {pr_info}

Return ONLY the final JSON result with no additional explanation."""

    cmd = [
        'claude',
        '--allowedTools', 'WebFetch,Bash',
        '--print'
    ]

    try:
        result = subprocess.run(
            cmd,
            input=prompt,  # Pass prompt via stdin
            capture_output=True,
            text=True,
            timeout=300,  # 5 minute timeout
            cwd=project_root  # Run in project directory to access .claude/skills
        )

        if result.returncode != 0:
            return {
                "permafail": False,
                "error": f"Skill execution failed: {result.stderr}",
                "signatures": []
            }

        # Extract JSON from output (skill may output explanatory text before JSON)
        output = result.stdout.strip()

        if not output:
            return {
                "permafail": False,
                "error": "Skill returned empty output",
                "signatures": []
            }

        # Strip markdown code fences if present
        import re
        # Match ```json or ``` at start, and ``` at end
        output = re.sub(r'^```(?:json)?\s*', '', output)
        output = re.sub(r'\s*```\s*$', '', output)
        output = output.strip()

        # Try parsing as pure JSON first
        try:
            return json.loads(output)
        except json.JSONDecodeError:
            pass

        # If that fails, try to find JSON object in output
        # Look for the last occurrence of a complete JSON object
        json_start = output.rfind('{')
        if json_start == -1:
            return {
                "permafail": False,
                "error": f"No JSON found in skill output. Output: {output[:200]}",
                "signatures": []
            }

        json_str = output[json_start:]
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            return {
                "permafail": False,
                "error": f"Failed to parse JSON from output: {e}. Output snippet: {output[:200]}",
                "signatures": []
            }

    except subprocess.TimeoutExpired:
        return {
            "permafail": False,
            "error": "Analysis timed out after 5 minutes",
            "signatures": []
        }
    except Exception as e:
        return {
            "permafail": False,
            "error": f"Unexpected error: {e}",
            "signatures": []
        }
