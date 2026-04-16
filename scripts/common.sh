#!/bin/bash

# common.sh - Shared utility functions for e2e-retest

# Count consecutive failures from prow history
# Args: job_name, html_file
# Output: consecutive|total_fail|total_pass|total_abort
count_consecutive_failures() {
  local job_name="$1"
  local html_file="$2"

  # Escape regex special characters in job name for safe grep pattern matching
  local escaped_job_name=$(printf '%s\n' "$job_name" | sed 's/[[\.*^$/]/\\&/g')

  local runs=$(grep -A 10 ">${escaped_job_name}<" "$html_file" | \
    grep -oE "run-(success|failure|aborted|pending)" | \
    head -10)

  local consecutive=0
  local total_fail=0
  local total_pass=0
  local total_abort=0
  local found_non_failure=0

  while IFS= read -r run; do
    [ -z "$run" ] && continue
    case "$run" in
      run-failure)
        total_fail=$((total_fail + 1))
        if [ "$found_non_failure" -eq 0 ]; then
          consecutive=$((consecutive + 1))
        fi
        ;;
      run-success)
        total_pass=$((total_pass + 1))
        found_non_failure=1
        ;;
      run-aborted)
        total_abort=$((total_abort + 1))
        found_non_failure=1
        ;;
      run-pending)
        ;;
    esac
  done <<< "$runs"

  echo "${consecutive}|${total_fail}|${total_pass}|${total_abort}"
}

# Extract consecutive failure URLs from prow history
# Args: job_name, html_file
# Output: JSON array of URLs for consecutive failures
get_consecutive_failure_urls() {
  local job_name="$1"
  local html_file="$2"

  # Escape regex special characters in job name for safe grep pattern matching
  local escaped_job_name=$(printf '%s\n' "$job_name" | sed 's/[[\.*^$/]/\\&/g')

  # Extract the table row for this specific job (stop at </tr>)
  # This prevents bleeding into the next job's row
  local job_row=$(grep -A 50 ">${escaped_job_name}<" "$html_file" | sed -n '1,/<\/tr>/p')

  # Extract all <td> elements with their class and href
  # HTML structure: <td class="...run-failure..."><a href="/view/...">...</a></td>
  local urls=()
  local found_non_failure=0

  # Parse each table cell in this row only
  while IFS= read -r line; do
    # Skip if we've reached the end of the row
    if echo "$line" | grep -q '</tr>'; then
      break
    fi

    # Check if this is a td with run-failure class
    if echo "$line" | grep -q 'class=".*run-failure'; then
      if [ "$found_non_failure" -eq 0 ]; then
        # Extract href from the <a> tag in the next line or same line
        local url=$(echo "$line" | grep -oE 'href="[^"]*"' | sed 's/href="//;s/"//' | head -1)
        if [ -n "$url" ]; then
          # Make absolute URL if relative
          if [[ "$url" == /view/* ]]; then
            url="https://prow.ci.openshift.org${url}"
          fi
          urls+=("$url")
        fi
      fi
    elif echo "$line" | grep -qE 'class=".*run-(success|aborted)'; then
      found_non_failure=1
    fi
  done <<< "$job_row"

  # Output as JSON array
  printf '['
  for i in "${!urls[@]}"; do
    [ $i -gt 0 ] && printf ','
    printf '"%s"' "${urls[$i]}"
  done
  printf ']'
}
