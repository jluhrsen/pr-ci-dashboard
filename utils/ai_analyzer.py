import subprocess
import json
import os
import re


def analyze_permafail_streaming(job_urls, job_name, pr_info):
    """
    Analyze job URLs for permafail pattern using ci:detect-permafail command with streaming output

    Args:
        job_urls: List of 2-10 consecutive Prow job URLs
        job_name: Name of the job (e.g., "e2e-aws-ovn")
        pr_info: PR identifier (e.g., "openshift/ovn-kubernetes#1234")

    Yields:
        str: Lines of output from the Claude CLI process

    Returns:
        dict: Final analysis result (via final yield)

    Prerequisites:
    - Claude CLI must be installed
    - ci@ai-helpers plugin must be installed and up to date (>= 0.0.43)
    """
    # Build prompt to invoke the ci:detect-permafail command
    urls_json = json.dumps(job_urls)
    prompt = f"""Use the /ci:detect-permafail command to analyze these jobs for permafail.

--job-urls='{urls_json}'
--job-name="{job_name}"
--pr="{pr_info}"

Return ONLY the final JSON result with no additional explanation."""

    # Send initial status
    yield json.dumps({"type": "output", "line": "==> Starting Claude CLI analysis..."})
    yield json.dumps({"type": "output", "line": f"==> Analyzing {len(job_urls)} job URLs for permafail patterns"})
    yield json.dumps({"type": "output", "line": "==> Using ci:detect-permafail command from ai-helpers plugin"})
    yield json.dumps({"type": "output", "line": ""})

    cmd = [
        'claude',
        '--allowedTools', 'Skill,WebFetch,Bash'
        # Removed --print to see interactive output
    ]

    try:
        # Use Popen for streaming output
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0,  # Unbuffered
            cwd=project_root,
            env={**os.environ, 'PYTHONUNBUFFERED': '1'}
        )

        # Write prompt to stdin and close it
        process.stdin.write(prompt)
        process.stdin.flush()
        process.stdin.close()

        # Stream output line by line from both stdout and stderr
        import threading
        output_lines = []

        def read_stream(stream, prefix=""):
            """Read from stream and yield lines"""
            for line in iter(stream.readline, ''):
                if line:
                    output_lines.append(line)

        # Start threads to read both streams
        stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, "OUT: "))
        stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, "ERR: "))
        stdout_thread.daemon = True
        stderr_thread.daemon = True
        stdout_thread.start()
        stderr_thread.start()

        # Wait for process with periodic output
        start_time = __import__('time').time()
        last_output_time = start_time
        last_line_count = 0

        while process.poll() is None:
            __import__('time').sleep(0.5)

            # Yield any new lines that were captured
            if len(output_lines) > last_line_count:
                for line in output_lines[last_line_count:]:
                    yield json.dumps({"type": "output", "line": line.rstrip('\n')})
                    last_output_time = __import__('time').time()
                last_line_count = len(output_lines)

            # Show periodic heartbeat if no output for a while
            elapsed = __import__('time').time() - last_output_time
            if elapsed > 10:
                total_elapsed = int(__import__('time').time() - start_time)
                yield json.dumps({"type": "output", "line": f"[Still analyzing... {total_elapsed}s elapsed]"})
                last_output_time = __import__('time').time()

        # Wait for threads to finish reading
        stdout_thread.join(timeout=1)
        stderr_thread.join(timeout=1)

        # Yield any remaining lines
        if len(output_lines) > last_line_count:
            for line in output_lines[last_line_count:]:
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
    Analyze job URLs for permafail pattern using ci:detect-permafail command from ai-helpers plugin

    Args:
        job_urls: List of 2-10 consecutive Prow job URLs
        job_name: Name of the job (e.g., "e2e-aws-ovn")
        pr_info: PR identifier (e.g., "openshift/ovn-kubernetes#1234")

    Returns:
        dict: Analysis result with permafail verdict and signatures.
              On error, returns dict with permafail=False, error message, and empty signatures list.

    Prerequisites:
    - Claude CLI must be installed
    - ci@ai-helpers plugin must be installed and up to date (>= 0.0.43)
    """

    # Build prompt to invoke the ci:detect-permafail command
    urls_json = json.dumps(job_urls)
    prompt = f"""Use the /ci:detect-permafail command to analyze these jobs for permafail.

--job-urls='{urls_json}'
--job-name="{job_name}"
--pr="{pr_info}"

Return ONLY the final JSON result with no additional explanation."""

    cmd = [
        'claude',
        '--allowedTools', 'Skill,WebFetch,Bash',
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
