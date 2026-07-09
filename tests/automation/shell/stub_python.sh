#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="${STUB_PROJECT_ROOT:?STUB_PROJECT_ROOT is required}"
COMMAND_LOG="${STUB_COMMAND_LOG:?STUB_COMMAND_LOG is required}"
SCENARIO="${STUB_SCENARIO:?STUB_SCENARIO is required}"

printf '%s\n' "$*" >> "$COMMAND_LOG"

create_france_partition() {
  local year="$1"
  mkdir -p "$PROJECT_ROOT/data/partitions/france"
  printf 'name\nfixture\n' > "$PROJECT_ROOT/data/partitions/france/france_${year}.csv"
}

create_insee_product() {
  local year="$1"
  local product_dir="$PROJECT_ROOT/data/products/insee/$year"
  mkdir -p "$product_dir"
  printf 'department_code\n01\n' > "$product_dir/france_departments_${year}.csv"
  printf '{}\n' > "$product_dir/manifest_${year}.json"
}

case "$*" in
  "-m data_pipeline partition --acquire-next")
    case "$SCENARIO" in
      no_new_guide)
        ;;
      unexpected_year_jump)
        create_france_partition 2028
        ;;
      *)
        create_france_partition 2027
        ;;
    esac
    exit 0
    ;;

  "-m insee_pipeline build --year 2024")
    case "$SCENARIO" in
      insee_build_failure)
        exit 2
        ;;
      *)
        exit 0
        ;;
    esac
    ;;

  "-m insee_pipeline product --year 2024")
    case "$SCENARIO" in
      insee_product_failure)
        mkdir -p "$PROJECT_ROOT/data/products/insee/2024"
        printf 'department_code\n01\n' > "$PROJECT_ROOT/data/products/insee/2024/france_departments_2024.csv"
        exit 2
        ;;
      *)
        create_insee_product 2024
        exit 0
        ;;
    esac
    ;;

  "-m data_pipeline departments --year 2027 --insee-year 2024")
    if [[ "$SCENARIO" == "stage2_france_failure" ]]; then
      exit 2
    fi
    exit 0
    ;;

  "-m data_pipeline departments --year 2027 --insee-year 2023")
    exit 0
    ;;

  "-m data_pipeline monaco --year 2027")
    exit 0
    ;;

  "-m data_pipeline arrondissements --year 2027")
    exit 0
    ;;

  "-m data_pipeline changes --previous-year 2026 --current-year 2027")
    exit 0
    ;;

  *)
    printf 'Unexpected stub command for %s: %s\n' "$SCENARIO" "$*" >&2
    exit 99
    ;;
esac
