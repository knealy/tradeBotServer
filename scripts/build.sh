#!/bin/bash
# No separate build step: the dashboard SPA is pre-built under static/dashboard/.
set -e
echo "No build step required - SPA is pre-built under static/dashboard/. Use scripts/deploy_to_railway.sh to deploy."
exit 0
