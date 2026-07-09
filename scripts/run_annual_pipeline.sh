#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON="${PYTHON:-$PROJECT_ROOT/.venv/bin/python}"

LOG_DIR="$PROJECT_ROOT/tmp/logs"
mkdir -p "$LOG_DIR"
LOG_BASENAME="annual_pipeline_$(date +%Y%m%d_%H%M%S).log"
LOG_PATH="$LOG_DIR/$LOG_BASENAME"
LOG_RELATIVE="tmp/logs/$LOG_BASENAME"

latest_france_year() {
  local latest=""
  local path basename year
  shopt -s nullglob
  for path in "$PROJECT_ROOT"/data/partitions/france/france_*.csv; do
    basename="$(basename "$path")"
    if [[ "$basename" =~ ^france_([0-9]{4})\.csv$ ]]; then
      year="${BASH_REMATCH[1]}"
      if [[ -z "$latest" || "$year" -gt "$latest" ]]; then
        latest="$year"
      fi
    fi
  done
  shopt -u nullglob

  if [[ -z "$latest" ]]; then
    echo "No accepted France partition found under data/partitions/france." >&2
    return 1
  fi
  echo "$latest"
}

latest_insee_product_year() {
  local latest=""
  local dir basename year csv manifest
  shopt -s nullglob
  for dir in "$PROJECT_ROOT"/data/products/insee/*; do
    [[ -d "$dir" ]] || continue
    basename="$(basename "$dir")"
    if [[ "$basename" =~ ^([0-9]{4})$ ]]; then
      year="${BASH_REMATCH[1]}"
      csv="$dir/france_departments_${year}.csv"
      manifest="$dir/manifest_${year}.json"
      if [[ -f "$csv" && -f "$manifest" ]]; then
        if [[ -z "$latest" || "$year" -gt "$latest" ]]; then
          latest="$year"
        fi
      fi
    fi
  done
  shopt -u nullglob

  if [[ -z "$latest" ]]; then
    echo "No accepted INSEE product found under data/products/insee." >&2
    return 1
  fi
  echo "$latest"
}

run_cli() {
  PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" "$@"
}

main() {
  cd "$PROJECT_ROOT"

  if [[ "$PYTHON" == */* ]]; then
    if [[ ! -x "$PYTHON" ]]; then
      echo "Python executable not found or not executable: $PYTHON" >&2
      return 2
    fi
  else
    if ! command -v "$PYTHON" >/dev/null 2>&1; then
      echo "Python executable not found on PATH: $PYTHON" >&2
      return 2
    fi
  fi

  echo "Annual Michelin pipeline started."
  echo "Log: $LOG_RELATIVE"

  before_year="$(latest_france_year)"
  echo "Latest accepted France partition before Stage 1: $before_year"

  run_cli -m data_pipeline partition --acquire-next

  after_year="$(latest_france_year)"
  echo "Latest accepted France partition after Stage 1: $after_year"

  if [[ "$after_year" -eq "$before_year" ]]; then
    echo "No new Michelin guide was published. Downstream stages were not run."
    echo
    echo "Annual Michelin pipeline completed: no new guide."
    echo "Latest Michelin year: $after_year"
    echo "Stage 2 France: skipped"
    echo "Stage 2 Monaco: skipped"
    echo "Stage 3: skipped"
    echo "Guide changes: skipped"
    echo "Log: $LOG_RELATIVE"
    return 0
  fi

  expected_year=$((before_year + 1))
  if [[ "$after_year" -ne "$expected_year" ]]; then
    echo "Stage 1 published an unexpected France year jump: before=$before_year after=$after_year expected=$expected_year" >&2
    return 2
  fi

  new_guide_year="$after_year"
  previous_guide_year=$((new_guide_year - 1))

  latest_insee_year="$(latest_insee_product_year)"
  attempted_insee_year=$((latest_insee_year + 1))
  insee_year_used="$attempted_insee_year"
  fallback_used="no"

  echo "Attempting INSEE product year: $attempted_insee_year"
  if run_cli -m insee_pipeline build --year "$attempted_insee_year" \
    && run_cli -m insee_pipeline product --year "$attempted_insee_year"; then
    insee_year_used="$attempted_insee_year"
  else
    echo "Warning: INSEE $attempted_insee_year failed; falling back to accepted INSEE $latest_insee_year." >&2
    current_insee_year="$(latest_insee_product_year)"
    if [[ "$current_insee_year" -ne "$latest_insee_year" ]]; then
      echo "INSEE fallback refused: accepted INSEE product year changed from $latest_insee_year to $current_insee_year after the failed attempt." >&2
      return 2
    fi
    insee_year_used="$latest_insee_year"
    fallback_used="yes"
  fi

  echo "Selected INSEE product year: $insee_year_used"

  run_cli -m data_pipeline departments --year "$new_guide_year" --insee-year "$insee_year_used"
  run_cli -m data_pipeline monaco --year "$new_guide_year"
  run_cli -m data_pipeline arrondissements --year "$new_guide_year"
  run_cli -m data_pipeline changes --previous-year "$previous_guide_year" --current-year "$new_guide_year"

  echo
  echo "Annual Michelin pipeline completed."
  echo "Previous Michelin year: $previous_guide_year"
  echo "New Michelin year: $new_guide_year"
  echo "Attempted INSEE year: $attempted_insee_year"
  echo "INSEE year used: $insee_year_used"
  echo "INSEE fallback used: $fallback_used"
  echo "Stage 2 France: complete"
  echo "Stage 2 Monaco: complete"
  echo "Stage 3: complete"
  echo "Guide changes $previous_guide_year -> $new_guide_year: complete"
  echo "Log: $LOG_RELATIVE"
}

main "$@" 2>&1 | tee -a "$LOG_PATH"
exit "${PIPESTATUS[0]}"
