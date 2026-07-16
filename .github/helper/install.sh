#!/bin/bash
# Mirrors the ERPNext/HRMS CI install flow: clone Frappe, init a bench,
# create the test DB, pull the dependency apps (payments + erpnext + hrms) and
# this app, then reinstall + install-app. Reproduces a real deploy.
#
# No credentials are committed anywhere:
#   * the MariaDB root account uses an EMPTY password
#     (MARIADB_ALLOW_EMPTY_ROOT_PASSWORD is set on the service in ci.yml),
#   * the site DB-user and admin passwords are generated at runtime, and
#   * site_config.json is written here at runtime, not stored in the repo.

set -e

cd ~ || exit

sudo apt update
sudo apt remove -y mysql-server mysql-client || true
sudo apt install -y libcups2-dev redis-server mariadb-client libmariadb-dev pkg-config

pip install frappe-bench

frappeuser=${FRAPPE_USER:-"frappe"}
frappebranch=${FRAPPE_BRANCH:-"version-16"}
erpnextbranch=${ERPNEXT_BRANCH:-"version-16"}
hrmsbranch=${HRMS_BRANCH:-"version-16"}
paymentsbranch=${PAYMENTS_BRANCH:-"version-16"}

# Runtime-generated credentials — never committed to the repo.
DB_NAME="test_frappe"
DB_USER="test_frappe"
DB_PASSWORD="$(openssl rand -hex 16)"
ADMIN_PASSWORD="$(openssl rand -hex 16)"

git clone "https://github.com/${frappeuser}/frappe" --branch "${frappebranch}" --depth 1
bench init --skip-assets --frappe-path ~/frappe --python "$(which python)" frappe-bench

mkdir ~/frappe-bench/sites/test_site

# MariaDB root has an empty password (set on the service container in ci.yml),
# so no password is passed on the command line.
mysql_root=(mariadb --host 127.0.0.1 --port 3306 -u root)
"${mysql_root[@]}" -e "SET GLOBAL character_set_server = 'utf8mb4'"
"${mysql_root[@]}" -e "SET GLOBAL collation_server = 'utf8mb4_unicode_ci'"
"${mysql_root[@]}" -e "CREATE USER '${DB_USER}'@'localhost' IDENTIFIED BY '${DB_PASSWORD}'"
"${mysql_root[@]}" -e "CREATE DATABASE ${DB_NAME}"
"${mysql_root[@]}" -e "GRANT ALL PRIVILEGES ON \`${DB_NAME}\`.* TO '${DB_USER}'@'localhost'"
"${mysql_root[@]}" -e "FLUSH PRIVILEGES"

# site_config.json is generated at runtime — no credentials live in the repo.
cat > ~/frappe-bench/sites/test_site/site_config.json <<EOF
{
    "db_host": "127.0.0.1",
    "db_port": 3306,
    "db_name": "${DB_NAME}",
    "db_password": "${DB_PASSWORD}",
    "use_mysqlclient": 1,
    "admin_password": "${ADMIN_PASSWORD}",
    "root_login": "root",
    "root_password": "",
    "host_name": "http://test_site:8000",
    "install_apps": ["payments", "erpnext", "hrms"],
    "throttle_user_limit": 100
}
EOF

install_wkhtmltopdf() {
    wget -O /tmp/wkhtmltox.tar.xz https://github.com/frappe/wkhtmltopdf/raw/master/wkhtmltox-0.12.3_linux-generic-amd64.tar.xz
    tar -xf /tmp/wkhtmltox.tar.xz -C /tmp
    sudo mv /tmp/wkhtmltox/bin/wkhtmltopdf /usr/local/bin/wkhtmltopdf
    sudo chmod o+x /usr/local/bin/wkhtmltopdf
}
install_wkhtmltopdf &

cd ~/frappe-bench || exit

sed -i 's/watch:/# watch:/g' Procfile
sed -i 's/schedule:/# schedule:/g' Procfile
sed -i 's/socketio:/# socketio:/g' Procfile
sed -i 's/redis_socketio:/# redis_socketio:/g' Procfile

bench get-app "https://github.com/${frappeuser}/payments" --branch "$paymentsbranch"
bench get-app "https://github.com/${frappeuser}/erpnext" --branch "$erpnextbranch" --resolve-deps
bench get-app "https://github.com/${frappeuser}/hrms" --branch "$hrmsbranch"
bench get-app upande_ta "${GITHUB_WORKSPACE}"
bench setup requirements --dev

bench start &>> ~/frappe-bench/bench_start.log &
CI=Yes bench build --app frappe &
bench --site test_site reinstall --yes

bench --verbose --site test_site install-app upande_ta
