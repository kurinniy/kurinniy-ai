#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  bash scripts/deploy_stage.sh [feature-branch] [--service SERVICE] [--no-redeploy]

Examples:
  bash scripts/deploy_stage.sh
  bash scripts/deploy_stage.sh codex/my-feature
  bash scripts/deploy_stage.sh codex/my-feature --service kurinniy-ai
  bash scripts/deploy_stage.sh codex/my-feature --no-redeploy

Behavior:
  1. Checks that the git worktree is clean.
  2. Fetches latest refs from origin.
  3. Fast-forward merges the feature branch into stage.
  4. Pushes stage to origin/stage.
  5. Redeploys the staging Railway service from source unless --no-redeploy is set.
EOF
}

feature_branch=""
service_name="kurinniy-ai"
redeploy="yes"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --service)
      if [[ $# -lt 2 ]]; then
        echo "Missing value for --service" >&2
        exit 1
      fi
      service_name="$2"
      shift 2
      ;;
    --no-redeploy)
      redeploy="no"
      shift
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 1
      ;;
    *)
      if [[ -n "$feature_branch" ]]; then
        echo "Feature branch is already set to '$feature_branch'" >&2
        exit 1
      fi
      feature_branch="$1"
      shift
      ;;
  esac
done

if [[ -z "$feature_branch" ]]; then
  feature_branch="$(git branch --show-current)"
fi

if [[ "$feature_branch" == "stage" ]]; then
  echo "Feature branch must not be 'stage'." >&2
  exit 1
fi

if [[ -n "$(git status --short)" ]]; then
  echo "Working tree is not clean. Commit or stash changes before deploying to stage." >&2
  exit 1
fi

original_branch="$(git branch --show-current)"

git fetch origin

if ! git show-ref --verify --quiet "refs/heads/$feature_branch"; then
  echo "Local branch '$feature_branch' does not exist." >&2
  exit 1
fi

git switch stage
git merge --ff-only "$feature_branch"
git push origin stage

local_stage_commit="$(git rev-parse stage)"
remote_stage_commit="$(git rev-parse origin/stage)"
echo "stage local : $local_stage_commit"
echo "stage remote: $remote_stage_commit"

if [[ "$local_stage_commit" != "$remote_stage_commit" ]]; then
  echo "origin/stage is not aligned with local stage after push." >&2
  exit 1
fi

if [[ "$redeploy" == "yes" ]]; then
  if ! command -v railway >/dev/null 2>&1; then
    echo "Railway CLI is not installed. Stage code is pushed, but redeploy was skipped." >&2
    exit 1
  fi
  railway redeploy -s "$service_name" --from-source -y
fi

if [[ "$original_branch" != "stage" ]]; then
  git switch "$original_branch"
fi

