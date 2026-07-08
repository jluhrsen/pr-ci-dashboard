import subprocess
import json
import os
import re
import tempfile

# Overall deadline for a single permafail analysis (both streaming and
# non-streaming paths)
ANALYSIS_TIMEOUT_SECONDS = 600


def build_claude_env(google_adc=None):
    """
    Build the env for a `claude` subprocess, optionally as a specific user.

    When google_adc (an authorized_user credentials dict) is given, it is
    written to a transient file (mode 0600) and GOOGLE_APPLICATION_CREDENTIALS
    points the subprocess at it, so Vertex calls run as that user. Callers
    must pass the returned adc_path to cleanup_adc() once the subprocess
    finishes. Without google_adc, the process env (pod-level credentials)
    is used unchanged.

    Returns:
        (env dict, adc_path or None)
    """
    env = {**os.environ, 'PYTHONUNBUFFERED': '1'}
    adc_path = None
    if google_adc:
        fd, adc_path = tempfile.mkstemp(prefix='user-adc-', suffix='.json')
        with os.fdopen(fd, 'w') as f:
            json.dump(google_adc, f)
        env['GOOGLE_APPLICATION_CREDENTIALS'] = adc_path
    return env, adc_path


def cleanup_adc(adc_path):
    """Delete a transient per-user credentials file (no-op for None)."""
    if adc_path:
        try:
            os.unlink(adc_path)
        except OSError:
            pass


def get_claude_workdir():
    """
    Get working directory for Claude CLI subprocess.

    After package migration, we use the current process working directory
    instead of the package installation directory. This allows Claude to:
    - Access git repository context if running from a git clone
    - Write to a writable directory (not site-packages)
    - Use the user's current context

    Can be overridden via PR_CI_DASHBOARD_CLAUDE_WORKDIR environment variable.
    """
    override = os.environ.get('PR_CI_DASHBOARD_CLAUDE_WORKDIR')
    if override:
        return override

    # Use current working directory (where the user launched the app)
    return os.getcwd()


def analyze_permafail_streaming(job_urls, job_name, pr_info, google_adc=None):
    """
    Analyze job URLs for permafail pattern using ci:detect-permafail command with streaming output

    Args:
        job_urls: List of 2-10 consecutive Prow job URLs
        job_name: Name of the job (e.g., "e2e-aws-ovn")
        pr_info: PR identifier (e.g., "openshift/ovn-kubernetes#1234")
        google_adc: Optional per-user authorized_user credentials dict;
                    Vertex analysis then runs as that user

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

    env, adc_path = build_claude_env(google_adc)
    process = None
    stdout_thread = None
    stderr_thread = None
    try:
        # Use Popen for streaming output
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=0,  # Unbuffered
            cwd=get_claude_workdir(),
            env=env
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
            # Overall deadline (matches the non-streaming path's timeout).
            # Without this the loop had no exit while the child hung, and
            # the TimeoutExpired handler below was unreachable.
            if __import__('time').time() - start_time > ANALYSIS_TIMEOUT_SECONDS:
                yield json.dumps({
                    "type": "result",
                    "data": {
                        "permafail": False,
                        "error": f"Analysis timed out after {ANALYSIS_TIMEOUT_SECONDS // 60} minutes",
                        "signatures": []
                    }
                })
                return  # finally terminates the child and cleans up

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
        return_code = process.wait(timeout=ANALYSIS_TIMEOUT_SECONDS)

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
    finally:
        # Runs on normal completion AND when the SSE client disconnects
        # (Flask closes the abandoned generator -> GeneratorExit unwinds
        # through here). The child must not outlive this generator: it would
        # keep consuming resources for a request nobody is watching, and its
        # credentials file is deleted below.
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        for thread in (stdout_thread, stderr_thread):
            if thread is not None:
                thread.join(timeout=1)
        if process is not None:
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream:
                        stream.close()
                except OSError:
                    pass
        cleanup_adc(adc_path)


def analyze_permafail(job_urls, job_name, pr_info, google_adc=None):
    """
    Analyze job URLs for permafail pattern using ci:detect-permafail command from ai-helpers plugin

    Args:
        job_urls: List of 2-10 consecutive Prow job URLs
        job_name: Name of the job (e.g., "e2e-aws-ovn")
        pr_info: PR identifier (e.g., "openshift/ovn-kubernetes#1234")
        google_adc: Optional per-user authorized_user credentials dict;
                    Vertex analysis then runs as that user

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

    env, adc_path = build_claude_env(google_adc)
    try:
        result = subprocess.run(
            cmd,
            input=prompt,  # Pass prompt via stdin
            capture_output=True,
            text=True,
            timeout=ANALYSIS_TIMEOUT_SECONDS,
            cwd=get_claude_workdir(),
            env=env
        )

        if result.returncode != 0:
            error_msg = f"Skill execution failed: {result.stderr}"
            print(f"[ERROR] {error_msg}")
            print(f"[ERROR] stdout: {result.stdout[:500]}")
            return {
                "permafail": False,
                "error": error_msg,
                "signatures": []
            }

        # Extract JSON from output (skill may output explanatory text before JSON)
        output = result.stdout.strip()

        if not output:
            error_msg = "Skill returned empty output"
            print(f"[ERROR] {error_msg} for job_name={job_name}")
            return {
                "permafail": False,
                "error": error_msg,
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
            parsed_result = json.loads(output)
            print(f"[DEBUG] AI analysis for {job_name}: permafail={parsed_result.get('permafail')}, reason={parsed_result.get('reason', '')[:100]}")
            return parsed_result
        except json.JSONDecodeError:
            pass

        # If that fails, try to find JSON object in output
        # Look for the last occurrence of a complete JSON object
        json_start = output.rfind('{')
        if json_start == -1:
            error_msg = f"No JSON found in skill output. Output: {output[:200]}"
            print(f"[ERROR] {error_msg}")
            return {
                "permafail": False,
                "error": error_msg,
                "signatures": []
            }

        json_str = output[json_start:]
        try:
            parsed_result = json.loads(json_str)
            print(f"[DEBUG] AI analysis for {job_name}: permafail={parsed_result.get('permafail')}, reason={parsed_result.get('reason', '')[:100]}")
            return parsed_result
        except json.JSONDecodeError as e:
            error_msg = f"Failed to parse JSON from output: {e}. Output snippet: {output[:200]}"
            print(f"[ERROR] {error_msg}")
            return {
                "permafail": False,
                "error": error_msg,
                "signatures": []
            }

    except subprocess.TimeoutExpired:
        error_msg = "Analysis timed out after 5 minutes"
        print(f"[ERROR] {error_msg} for job_name={job_name}, pr={pr_info}")
        return {
            "permafail": False,
            "error": error_msg,
            "signatures": []
        }
    except Exception as e:
        error_msg = f"Unexpected error: {e}"
        print(f"[ERROR] {error_msg} for job_name={job_name}, pr={pr_info}")
        return {
            "permafail": False,
            "error": error_msg,
            "signatures": []
        }
    finally:
        cleanup_adc(adc_path)
