#!/bin/bash

# ── Defaults ───────────────────────────────────────────────────────────────────
NUM_COMMITS=20
FULL_DIFF=false
SPLIT_MB=10        # Split output into multiple files if exceeding this size (MB)
# ───────────────────────────────────────────────────────────────────────────────

# Help text
show_help() {
  cat << EOF
Usage: get-git.sh [OPTIONS]

Exports the last N git commits to a txt file on your Desktop.
Output file: ~/Desktop/<repo-name>-commits-yyyy-mm-dd.txt
Full diff:   ~/Desktop/<repo-name>-commits-yyyy-mm-dd-full.txt
Split files: ~/Desktop/<repo-name>-commits-yyyy-mm-dd-full-part1.txt, part2, ...

Options:
  --commits=N   Number of commits to export (default: 20)
  --full        Include full file diffs for each commit
  --split=N     Split output at N MB when using --full (default: 10)
  --help, -?    Show this help message

Examples:
  ./get-git.sh                        Export last 20 commits (default)
  ./get-git.sh --commits=50           Export last 50 commits
  ./get-git.sh --full                 Export last 20 commits with full diffs
  ./get-git.sh --commits=10 --full    Export last 10 commits with full diffs
  ./get-git.sh --full --split=5       Split into 5 MB chunks if large

Notes:
  - Must be run from within a git repository
  - Project name is auto-detected from the repo folder name
  - Running multiple times on the same day overwrites the previous file
  - --full output can be very large on active repos!
  - A size warning and confirmation prompt is shown before --full export begins
EOF
}

# Parse arguments
for arg in "$@"; do
  case $arg in
    --help|-\?)
      show_help
      exit 0
      ;;
    --commits=*)
      NUM_COMMITS="${arg#*=}"
      ;;
    --full)
      FULL_DIFF=true
      ;;
    --split=*)
      SPLIT_MB="${arg#*=}"
      ;;
    *)
      echo "⚠️  Unknown argument: $arg"
      echo "Run './get-git.sh --help' for usage."
      exit 1
      ;;
  esac
done

# Check we're in a git repo
if ! git rev-parse --is-inside-work-tree &>/dev/null; then
  echo "❌ Error: Not inside a git repository. Run this from your project root."
  exit 1
fi

# Auto-detect project name from the repo folder name
PROJECT=$(basename "$(git rev-parse --show-toplevel)")
DATE=$(date '+%Y-%m-%d')

# ── Pre-flight size warning for --full mode ────────────────────────────────────
if [ "$FULL_DIFF" = true ]; then
  echo ""
  echo "🔍 Estimating diff size for the last $NUM_COMMITS commits..."

  # Estimate size by generating the diff to /dev/null and measuring
  ESTIMATED_BYTES=$(git log -"$NUM_COMMITS" --pretty=format:"%H" | \
    while read -r HASH; do
      git show --stat --patch "$HASH"
    done | wc -c)

  ESTIMATED_KB=$((ESTIMATED_BYTES / 1024))
  ESTIMATED_MB=$((ESTIMATED_KB / 1024))

  if [ "$ESTIMATED_MB" -ge 1 ]; then
    SIZE_DISPLAY="${ESTIMATED_MB} MB"
  else
    SIZE_DISPLAY="${ESTIMATED_KB} KB"
  fi

  echo ""
  echo "┌─────────────────────────────────────────────────────┐"
  echo "│  ⚠️   FULL DIFF MODE — PRE-FLIGHT CHECK              │"
  echo "├─────────────────────────────────────────────────────┤"
  printf  "│  Project  : %-39s│\n" "$PROJECT"
  printf  "│  Commits  : %-39s│\n" "$NUM_COMMITS"
  printf  "│  Est. size: %-39s│\n" "$SIZE_DISPLAY"
  if [ "$ESTIMATED_MB" -ge "$SPLIT_MB" ]; then
  printf  "│  Splitting: every %-33s│\n" "${SPLIT_MB} MB"
  else
  printf  "│  Splitting: %-39s│\n" "not needed"
  fi
  echo "└─────────────────────────────────────────────────────┘"
  echo ""

  if [ "$ESTIMATED_MB" -ge 50 ]; then
    echo "  🔴 This is a LARGE export (${SIZE_DISPLAY}). Proceed with caution."
  elif [ "$ESTIMATED_MB" -ge 10 ]; then
    echo "  🟡 This is a moderately large export (${SIZE_DISPLAY})."
  else
    echo "  🟢 Size looks reasonable (${SIZE_DISPLAY})."
  fi

  echo ""
  read -r -p "  Continue? [y/N]: " CONFIRM
  echo ""
  if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
    echo "  Aborted. No files were written."
    exit 0
  fi
fi

# ── Build output path ──────────────────────────────────────────────────────────
if [ "$FULL_DIFF" = true ]; then
  BASE_OUTPUT="$HOME/Desktop/${PROJECT}-commits-${DATE}-full"
else
  BASE_OUTPUT="$HOME/Desktop/${PROJECT}-commits-${DATE}"
  OUTPUT="${BASE_OUTPUT}.txt"
fi

# ── Helper: write a commit block to a given file ───────────────────────────────
write_commit() {
  local HASH=$1
  local OUT=$2

  {
    echo "────────────────────────────────────────────────────────"
    git log -1 --pretty=format:"Commit  : %H
Author  : %an <%ae>
Date    : %ad
Subject : %s

%b" --date=format:'%Y-%m-%d %H:%M:%S' "$HASH"
    echo ""
    echo "  Files changed:"
    git diff-tree --no-commit-id -r --name-status "$HASH" | \
      sed 's/^A/  [ADDED]   /; s/^M/  [MODIFIED]/; s/^D/  [DELETED] /; s/^R/  [RENAMED] /'
    echo ""

    if [ "$FULL_DIFF" = true ]; then
      echo "  Full diff:"
      echo "  ··········································································"
      git show --stat --patch "$HASH" | tail -n +6 | sed 's/^/  /'
      echo ""
    fi
  } >> "$OUT"
}

# ── Write header to a file ─────────────────────────────────────────────────────
write_header() {
  local OUT=$1
  local PART_LABEL=$2
  {
    echo "======================================================"
    echo " Git Commit Export"
    echo " Project: $PROJECT"
    echo " Repo   : $(git rev-parse --show-toplevel)"
    echo " Branch : $(git rev-parse --abbrev-ref HEAD)"
    echo " Date   : $(date '+%Y-%m-%d %H:%M:%S')"
    echo " Commits: $NUM_COMMITS"
    echo " Mode   : $([ "$FULL_DIFF" = true ] && echo 'Full diff' || echo 'Summary only')"
    [ -n "$PART_LABEL" ] && echo " Part   : $PART_LABEL"
    echo "======================================================"
    echo ""
  } > "$OUT"
}

# ── Write footer to a file ─────────────────────────────────────────────────────
write_footer() {
  local OUT=$1
  {
    echo ""
    echo "======================================================"
    echo " End of export"
    echo "======================================================"
  } >> "$OUT"
}

# ── Main export ────────────────────────────────────────────────────────────────
SPLIT_BYTES=$((SPLIT_MB * 1024 * 1024))
PART=1
PART_FILES=()

if [ "$FULL_DIFF" = true ]; then
  OUTPUT="${BASE_OUTPUT}-part${PART}.txt"
fi

write_header "$OUTPUT" "$([ "$FULL_DIFF" = true ] && echo "Part $PART")"
PART_FILES+=("$OUTPUT")

git log -"$NUM_COMMITS" --pretty=format:"%H" | while read -r HASH; do
  write_commit "$HASH" "$OUTPUT"

  # Check size and split if needed (full mode only)
  if [ "$FULL_DIFF" = true ]; then
    CURRENT_SIZE=$(wc -c < "$OUTPUT")
    if [ "$CURRENT_SIZE" -ge "$SPLIT_BYTES" ]; then
      write_footer "$OUTPUT"
      PART=$((PART + 1))
      OUTPUT="${BASE_OUTPUT}-part${PART}.txt"
      write_header "$OUTPUT" "Part $PART"
      PART_FILES+=("$OUTPUT")
    fi
  fi
done

write_footer "$OUTPUT"

# ── Summary ────────────────────────────────────────────────────────────────────
echo "✅ Done! Exported $NUM_COMMITS commits from '$PROJECT'."
echo ""
if [ "$FULL_DIFF" = true ] && [ "${#PART_FILES[@]}" -gt 1 ]; then
  echo "   📄 Split into ${#PART_FILES[@]} files:"
  for f in "${PART_FILES[@]}"; do
    SIZE=$(du -h "$f" | cut -f1)
    echo "      $SIZE  →  $f"
  done
else
  SIZE=$(du -h "$OUTPUT" | cut -f1)
  echo "   📄 $SIZE  →  $OUTPUT"
fi
echo ""
