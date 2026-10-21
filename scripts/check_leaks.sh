#!/bin/sh
# 推送前泄露检查。任何一项失败都不要推送。
# 用法：sh scripts/check_leaks.sh
#
# 本脚本刻意不包含任何具体人名或邮箱字面量——那本身就是泄露。
# 需要追加本地敏感词时，写入仓库根目录的 .leak-deny（已 gitignore，每行一个），
# 脚本会自动并入检测模式。
set -u

fail=0
note() { printf '%s\n' "$*"; }

deny_file=$(dirname "$0")/../.leak-deny
extra=''
if [ -f "$deny_file" ]; then
  extra=$(grep -vE '^[[:space:]]*(#|$)' "$deny_file" | paste -sd'|' -)
fi

# 1) 提交元数据不得含私人邮箱
if git log --format='%ae%n%ce' 2>/dev/null | grep -qiE 'gmail\.com|outlook\.com|qq\.com|163\.com|126\.com|hotmail\.com'; then
  note 'FAIL 提交身份含私人邮箱；请改用 noreply 地址'
  fail=1
fi

# 2) 文件内容不得含本机绝对路径或个人标识
patterns='/Users/[a-z][a-z0-9_-]*|gmail\.com|outlook\.com'
if [ -n "$extra" ]; then
  patterns="$patterns|$extra"
fi
hits=$(git grep -niE "$patterns" -- . 2>/dev/null | grep -vE '^uv\.lock:' || true)
if [ -n "$hits" ]; then
  printf '%s\n' "$hits"
  note 'FAIL 上述文件含个人标识或本机路径'
  fail=1
fi

# 3) 不得跟踪密钥与运行数据
if git ls-files | grep -qE '(^|/)\.data/|\.(pem|key|p12|p8|cer)$|(^|/)\.env$|(^|/)\.leak-deny$'; then
  note 'FAIL 跟踪了密钥、运行数据或本地敏感词清单'
  fail=1
fi

# 4) 厂商绑定实现不得入库
if git ls-files | grep -qE '(^|/)(native|native_guard|dsh|cursor|providers|access|live|live_settings)\.py$'; then
  note 'FAIL 厂商绑定实现不得进入公开仓库'
  fail=1
fi

# 5) 真实密钥形态（测试用假值带 fake- 前缀，豁免）
if git grep -nE 'sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' -- . 2>/dev/null | grep -vq 'fake-'; then
  note 'FAIL 发现疑似真实密钥'
  fail=1
fi

if [ "$fail" -eq 0 ]; then
  note 'OK 未发现泄露'
fi
exit "$fail"
