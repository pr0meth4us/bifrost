#!/usr/bin/env bash
# Grants the Cloud Build service account what it needs to deploy Cloud Run,
# then prints the one step that cannot be scripted.
#
# Connecting a GitHub repo to Cloud Build is a browser OAuth flow — it needs a
# human to authorize the Google Cloud Build app against the repo. Everything
# either side of that is here.
set -euo pipefail

PROJECT="${PROJECT:-bifrost-prod-2026}"
REGION="${REGION:-asia-southeast1}"
SERVICE="${SERVICE:-bifrost}"
REPO_OWNER="${REPO_OWNER:-pr0meth4us}"
REPO_NAME="${REPO_NAME:-bifrost}"
BRANCH="${BRANCH:-^main$}"

NUM="$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')"
BUILD_SA="${NUM}-compute@developer.gserviceaccount.com"

echo "==> Granting ${BUILD_SA} deploy rights"
for role in roles/run.admin roles/iam.serviceAccountUser roles/artifactregistry.writer roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${BUILD_SA}" --role="$role" \
    --condition=None >/dev/null
  echo "    $role"
done

cat <<EOF

==> Now connect the repo (browser, one time)

  https://console.cloud.google.com/cloud-build/repositories/2nd-gen?project=${PROJECT}

  "Create host connection" -> GitHub -> authorize -> link ${REPO_OWNER}/${REPO_NAME}

==> Then create the trigger

  gcloud builds triggers create github \\
    --project=${PROJECT} --region=${REGION} \\
    --name=${SERVICE}-deploy \\
    --repo-owner=${REPO_OWNER} --repo-name=${REPO_NAME} \\
    --branch-pattern='${BRANCH}' \\
    --build-config=cloudbuild.yaml

==> Verify without waiting for a push

  gcloud builds triggers run ${SERVICE}-deploy --project=${PROJECT} --region=${REGION} --branch=main
  gcloud builds list --project=${PROJECT} --region=${REGION} --limit=3

Note: the trigger watches ${BRANCH}. Your work is currently on
security/public-url-and-otp-logging, so nothing deploys until that merges.
EOF
