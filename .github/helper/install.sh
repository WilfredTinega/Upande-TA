#!/bin/bash
# Mimics a Frappe Cloud deploy: init a Frappe v16 bench, pull the exact
# dependency apps (ERPNext + HRMS on version-16), install this app, and migrate.
# Any failure here (bad frappe-dependencies, broken after_migrate hook, an
# import that doesn't exist on the target version, etc.) fails CI the same way
# a real deploy would.
set -e

cd ~ || exit

FRAPPE_BRANCH="version-16"

echo "::group::Install Bench"
pip install frappe-bench
echo "::endgroup::"

echo "::group::Init Bench & Install Frappe ($FRAPPE_BRANCH)"
bench init \
  --frappe-branch "$FRAPPE_BRANCH" \
  --skip-redis-config-generation \
  --skip-assets \
  --python "$(which python)" \
  frappe-bench
cd ~/frappe-bench || exit
echo "::endgroup::"

echo "::group::Point Bench at CI services"
bench set-config -g db_host 127.0.0.1
bench set-config -g redis_cache "redis://127.0.0.1:13000"
bench set-config -g redis_queue "redis://127.0.0.1:11000"
bench set-config -g redis_socketio "redis://127.0.0.1:13000"
echo "::endgroup::"

echo "::group::Fetch dependency apps (must match Frappe $FRAPPE_BRANCH)"
bench get-app erpnext --branch "$FRAPPE_BRANCH" --resolve-deps
bench get-app hrms --branch "$FRAPPE_BRANCH"
bench get-app upande_ta "${GITHUB_WORKSPACE}"
echo "::endgroup::"

echo "::group::Create fresh site (a brand-new bench)"
bench new-site test_site \
  --db-root-password root \
  --admin-password admin \
  --no-mariadb-socket
echo "::endgroup::"

echo "::group::Install apps in dependency order (the deploy step)"
bench --site test_site install-app erpnext hrms upande_ta
echo "::endgroup::"

echo "::group::Migrate (exercises after_migrate hooks + patches)"
bench --site test_site migrate
echo "::endgroup::"

echo "Install + migrate complete — deploy simulation passed."
