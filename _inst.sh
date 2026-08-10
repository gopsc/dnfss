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
inst_module "libmsg"
inst_module "liblogc"
inst_module "gateway"
inst_module "waf"	# waf 在 gateway 之前编译，部署时由 core 先拉起 waf 再拉起 gateway
inst_module "static"
inst_module "fsys"
inst_module "dnfs"
inst_module "dnfs2"
inst_module "db"
inst_module "db2"
inst_module "smtp"
inst_module "smtp3"
inst_module "captcha"
inst_module "captcha2"
inst_module "confs"
inst_module "ai_review"	# 需联网拉 chromadb onnx 模型（_fetch_onnx_model.sh 走 ModelScope 镜像）
inst_module "aibox"	# 需 ~/.cert/deepseek.key（见 _setenv.sh）
inst_module "redirect80"	# 80 端口重定向，依赖 ../pymsg
inst_module "core"	# core last
