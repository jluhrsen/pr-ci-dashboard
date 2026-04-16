#!/bin/bash

set -euo pipefail

# ci:payload-retest - Find and retest failed payload jobs on a PR
# Usage: ./payload-retest.sh [--json] [repo] <pr-number>

# Parse --json flag
JSON_OUTPUT=false
if [ $# -gt 0 ] && [ "$1" = "--json" ]; then
  JSON_OUTPUT=true
  shift
fi

# Parse arguments
if [ $# -eq 1 ]; then
  PR_NUMBER="$1"
  # Auto-detect from git remote
  REPO=$(git remote -v | head -1 | sed -E 's/.*github\.com[:/]([^/]+\/[^ .]+).*/\1/' | sed 's/\.git$//' || true)
  if [ -z "$REPO" ]; then
    echo "❌ Error: Could not detect repository from git remote." >&2
    echo "Please specify repo: $0 [--json] <repo> <pr-number>" >&2
    exit 1
  fi
  if [ "$JSON_OUTPUT" = false ]; then
    echo "Repository: $REPO"
  fi

elif [ $# -eq 2 ]; then
  REPO_ARG="$1"
  PR_NUMBER="$2"

  # Check if contains slash (org/repo format)
  if [[ "$REPO_ARG" == *"/"* ]]; then
    REPO="$REPO_ARG"
  else
    # Assume openshift org
    REPO="openshift/$REPO_ARG"
  fi
  if [ "$JSON_OUTPUT" = false ]; then
    echo "Repository: $REPO"
  fi

else
  echo "❌ Error: Invalid arguments" >&2
  echo "Usage: $0 [--json] [repo] <pr-number>" >&2
  exit 1
fi

# Validate PR number
if ! [[ "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "❌ Error: PR number must be numeric" >&2
  exit 1
fi

if [ "$JSON_OUTPUT" = false ]; then
  echo ""
fi

# Get payload URLs from PR comments
if [ "$JSON_OUTPUT" = false ]; then
  echo "Searching for payload runs..."
fi

if ! PR_COMMENTS=$(gh pr view ${PR_NUMBER} --repo ${REPO} --json comments 2>/dev/null); then
  if [ "$JSON_OUTPUT" = true ]; then
    echo '{"error":"Failed to fetch PR data from GitHub","failed":[],"running":[]}' >&2
  else
    echo "Error: Failed to fetch PR data from GitHub" >&2
  fi
  exit 1
fi

PAYLOAD_URLS=$(echo "$PR_COMMENTS" | \
  jq -r '.comments[].body' | \
  grep -oE 'https://pr-payload-tests[^ )]+' | \
  sort -u || true)

if [ -z "$PAYLOAD_URLS" ]; then
  if [ "$JSON_OUTPUT" = true ]; then
    echo '{"failed":[],"running":[]}'
  else
    echo "No payload runs found for this PR"
  fi
  exit 0
fi

NUM_URLS=$(echo "$PAYLOAD_URLS" | wc -l)
if [ "$JSON_OUTPUT" = false ]; then
  echo "Found $NUM_URLS payload run(s)"
  echo ""
fi

# Create unique temp directory and setup cleanup
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Fetch all payload pages in parallel
i=1
while read -r url; do
  {
    curl -sL "$url" > "$TMPDIR/payload_$i.html" 2>/dev/null
  } &
  i=$((i+1))
done <<< "$PAYLOAD_URLS"
wait

if [ "$JSON_OUTPUT" = false ]; then
  echo "Analyzing all payload runs..."
  echo ""
fi

# Parse each payload run and build a data structure
# Format: job_name|timestamp|status|url (one line per job per run)

i=1
while read -r url; do
  html_file="$TMPDIR/payload_$i.html"

  # Get timestamp for this run
  timestamp=$(grep -E 'Created:' "$html_file" 2>/dev/null | \
    sed -E 's/.*Created: ([^<]+).*/\1/' | head -1 || echo "")

  if [ -z "$timestamp" ]; then
    i=$((i+1))
    continue
  fi

  # Parse all jobs with their status
  # NOTE: This relies on specific HTML structure from pr-payload-tests dashboard:
  #   - Failed jobs: <span class="text-danger">job-name</span>
  #   - Success jobs: <span class="text-success">job-name</span>
  #   - Running jobs: <span class="">job-name</span>
  #   - Job names start with: periodic-ci-
  # If HTML structure changes, parsing may fail silently.

  # Write to per-iteration file to avoid concurrent append issues
  jobs_file="$TMPDIR/jobs_$i.txt"

  # Parse jobs with URLs using awk to handle multi-line patterns
  # HTML structure:
  #   <span class="text-danger">job-name</span>:
  #
  #   <a href="url">guid</a>

  # Process failed jobs
  awk '
    /<span class="text-danger">/ {
      match($0, /<span class="text-danger">([^<]+)<\/span>/, arr)
      job = arr[1]
      getline; getline  # Skip next 2 lines to get to <a href>
      if (match($0, /href="([^"]+)"/, url_arr)) {
        if (job ~ /^periodic-ci-/) {
          print job "|'"$timestamp"'|failed|" url_arr[1]
        }
      }
    }
  ' "$html_file" >> "$jobs_file" 2>/dev/null || true

  # Process successful jobs
  awk '
    /<span class="text-success">/ {
      match($0, /<span class="text-success">([^<]+)<\/span>/, arr)
      job = arr[1]
      getline; getline  # Skip next 2 lines to get to <a href>
      if (match($0, /href="([^"]+)"/, url_arr)) {
        if (job ~ /^periodic-ci-/) {
          print job "|'"$timestamp"'|success|" url_arr[1]
        }
      }
    }
  ' "$html_file" >> "$jobs_file" 2>/dev/null || true

  # Process running jobs (may not have URLs)
  awk '
    /<span class="">/ {
      match($0, /<span class="">([^<]+)<\/span>/, arr)
      job = arr[1]
      if (job ~ /^periodic-ci-/) {
        print job "|'"$timestamp"'|running|"
      }
    }
  ' "$html_file" >> "$jobs_file" 2>/dev/null || true

  i=$((i+1))
done <<< "$PAYLOAD_URLS"

# Concatenate all per-iteration job files
cat "$TMPDIR"/jobs_*.txt > "$TMPDIR/payload_jobs.txt" 2>/dev/null || true

# Check if we found any jobs
if [ ! -f "$TMPDIR/payload_jobs.txt" ] || [ ! -s "$TMPDIR/payload_jobs.txt" ]; then
  echo "No payload jobs found in any run"
  echo "NOTE: If payload runs exist but no jobs were found, the HTML structure may have changed."
  exit 0
fi

# Sort all job entries by timestamp (newest first) and job name
sort -t'|' -k2,2r -k1,1 "$TMPDIR/payload_jobs.txt" > "$TMPDIR/payload_jobs_sorted.txt"

# Build consecutive failure counts and URLs
# For each unique job, find its most recent status and count consecutive failures
declare -A job_most_recent_status
declare -A job_consecutive_failures
declare -A job_failure_urls
declare -A job_is_running

# Get unique job names
unique_jobs=$(cut -d'|' -f1 "$TMPDIR/payload_jobs_sorted.txt" | sort -u)

while read -r job; do
  [ -z "$job" ] && continue

  # Get all entries for this job, sorted by timestamp (newest first)
  job_entries=$(awk -F'|' -v j="$job" '$1==j' "$TMPDIR/payload_jobs_sorted.txt")

  # Most recent status is the first line
  most_recent_status=$(echo "$job_entries" | head -1 | cut -d'|' -f3)
  job_most_recent_status["$job"]="$most_recent_status"

  # If most recent is running, mark it
  if [ "$most_recent_status" = "running" ]; then
    job_is_running["$job"]=1
  fi

  # Count consecutive failures and collect URLs (only if currently failed)
  if [ "$most_recent_status" = "failed" ]; then
    consecutive=0
    urls=()
    while IFS='|' read -r j ts status url; do
      if [ "$status" = "failed" ]; then
        consecutive=$((consecutive + 1))
        urls+=("$url")
      else
        break
      fi
    done <<< "$job_entries"
    job_consecutive_failures["$job"]=$consecutive

    # Store URLs as comma-separated string
    job_failure_urls["$job"]=$(IFS=','; echo "${urls[*]}")
  fi
done <<< "$unique_jobs"

# Display currently failed jobs
failed_jobs=()
for job in "${!job_most_recent_status[@]}"; do
  if [ "${job_most_recent_status[$job]}" = "failed" ]; then
    failed_jobs+=("$job")
  fi
done

# Display currently running jobs
running_jobs=()
for job in "${!job_is_running[@]}"; do
  running_jobs+=("$job")
done

NUM_FAILED=${#failed_jobs[@]}
NUM_RUNNING=${#running_jobs[@]}

# JSON output mode
if [ "$JSON_OUTPUT" = true ]; then
  printf '{"failed":['

  first_job=true
  for job in "${failed_jobs[@]}"; do
    consecutive=${job_consecutive_failures[$job]:-0}
    urls_str=${job_failure_urls[$job]:-}

    # Convert comma-separated URLs to JSON array
    printf -v urls_json '['
    if [ -n "$urls_str" ]; then
      IFS=',' read -ra urls_array <<< "$urls_str"
      first_url=true
      for url in "${urls_array[@]}"; do
        if [ "$first_url" = true ]; then
          first_url=false
        else
          printf -v urls_json '%s,' "$urls_json"
        fi
        printf -v urls_json '%s"%s"' "$urls_json" "$url"
      done
    fi
    printf -v urls_json '%s]' "$urls_json"

    if [ "$first_job" = true ]; then
      first_job=false
    else
      printf ','
    fi

    printf '{"name":"%s","consecutive":%d,"urls":%s}' "$job" "$consecutive" "$urls_json"
  done

  printf '],"running":['

  first_running=true
  for job in "${running_jobs[@]}"; do
    if [ "$first_running" = true ]; then
      first_running=false
    else
      printf ','
    fi

    printf '{"name":"%s"}' "$job"
  done

  printf ']}\n'
  exit 0
fi

# Text output mode (original behavior)
if [ "$NUM_FAILED" -gt 0 ]; then
  echo "Failed payload jobs:"
  for job in "${failed_jobs[@]}"; do
    consecutive=${job_consecutive_failures[$job]:-0}
    echo "  ❌ $job"
    if [ "$consecutive" -gt 0 ]; then
      echo "     Consecutive failures: $consecutive"
    fi
  done
  echo ""
fi

if [ "$NUM_RUNNING" -gt 0 ]; then
  echo "⏳ Currently running ($NUM_RUNNING jobs):"
  for job in "${running_jobs[@]}"; do
    echo "  • $job"
  done
  echo ""
fi

# Exit if no failed jobs
if [ "$NUM_FAILED" -eq 0 ]; then
  if [ "$NUM_RUNNING" -gt 0 ]; then
    echo "✅ No failed payload jobs (waiting for $NUM_RUNNING running jobs)"
  else
    echo "✅ No failed payload jobs!"
  fi
  # Cleanup
  rm -f /tmp/payload_${PR_NUMBER}_*.html /tmp/payload_${PR_NUMBER}_*.txt
  exit 0
fi

# Present retest options
echo "What would you like to do?"
echo "  1) Retest selected jobs"
echo "  2) Retest all failed ($NUM_FAILED jobs)"
echo "  3) Just show list (done)"
echo ""
read -p "Choose [1-3]: " choice

case "$choice" in
  1)
    echo ""
    echo "Available jobs:"
    i=1
    for job in "${failed_jobs[@]}"; do
      echo "  $i) $job"
      i=$((i+1))
    done
    echo ""
    read -p "Enter job numbers to retest (space-separated, e.g., '1 3 5'): " job_nums

    # Build comment with selected jobs
    COMMENT=""
    for num in $job_nums; do
      # Validate input is numeric
      if ! [[ "$num" =~ ^[0-9]+$ ]]; then
        echo "Warning: Skipping invalid input '$num' (not a number)" >&2
        continue
      fi

      idx=$((num - 1))
      if [ $idx -ge 0 ] && [ $idx -lt ${#failed_jobs[@]} ]; then
        job="${failed_jobs[$idx]}"
        COMMENT="${COMMENT}/payload-job ${job}"$'\n'
      else
        echo "Warning: Job number $num is out of range (1-${#failed_jobs[@]})" >&2
      fi
    done

    if [ -n "$COMMENT" ]; then
      echo ""
      echo "Posting comment:"
      echo "$COMMENT"
      gh pr comment ${PR_NUMBER} --repo ${REPO} --body "$COMMENT"
      echo "✅ Done!"
    else
      echo "No valid jobs selected"
    fi
    ;;

  2)
    # Retest all failed jobs
    COMMENT=""
    for job in "${failed_jobs[@]}"; do
      COMMENT="${COMMENT}/payload-job ${job}"$'\n'
    done

    echo ""
    echo "Posting comment to retest all $NUM_FAILED jobs:"
    echo "$COMMENT"
    gh pr comment ${PR_NUMBER} --repo ${REPO} --body "$COMMENT"
    echo "✅ Done!"
    ;;

  3)
    # Just show list
    echo "Done."
    ;;

  *)
    echo "Invalid choice"
    rm -f /tmp/payload_${PR_NUMBER}_*.html /tmp/payload_${PR_NUMBER}_*.txt
    exit 1
    ;;
esac

# Cleanup
rm -f /tmp/payload_${PR_NUMBER}_*.html /tmp/payload_${PR_NUMBER}_*.txt
