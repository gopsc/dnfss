#!/bin/bash
set -euo pipefail

run_if_exists() {
	local script="$1"
	echo "---- run ${script} ----"

	if [ -f "$script" ]; then
		bash "$script"
	fi
}

inst_module() {
	local module="$1"
	echo "======== Install ${module} ========"

	local set_file="./${module}/_set.sh"

	if [ -f "$set_file" ]; then
		run_if_exists "$set_file"
		return 0
	else
		echo "Failed" >&2
		return 1
	fi
}

inst_module "libth"
inst_module "libhttp"
inst_module "libcrypt"
inst_module "libsqlite3"
inst_module "gateway"
inst_module "waf"	# waf 在 gateway 之前编译，部署时由 core 先拉起 waf 再拉起 gateway
inst_module "static"
inst_module "dnfs"
inst_module "db"
inst_module "smtp"
inst_module "captcha"
inst_module "core"	# core last
