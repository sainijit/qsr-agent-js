#!/usr/bin/env bash
# Render /opt/zuvo/.env from AWS SSM Parameter Store (SecureString) using the EC2 instance role.
# Never prints values. Writes atomically with mode 600.
#
#   deploy/aws/render-env.sh [--path /zuvo/prod/] [--out /opt/zuvo/.env] [--region ap-south-1]
#
# Parameter names map 1:1 to variable names: /zuvo/prod/JWT_SECRET -> JWT_SECRET.
# Non-secret settings (APP_ENV, PUBLIC_DOMAIN, ...) may live in the same path as String parameters.
set -euo pipefail
SSM_PATH="/zuvo/prod/"; OUT="/opt/zuvo/.env"; REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-}}"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --path) SSM_PATH="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --region) REGION="$2"; shift 2 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[[ "$SSM_PATH" == */ ]] || SSM_PATH="$SSM_PATH/"
command -v aws >/dev/null || { echo "aws CLI not installed" >&2; exit 2; }
REGION_ARG=(); [[ -n "$REGION" ]] && REGION_ARG=(--region "$REGION")

umask 077
TMP="$(mktemp "$(dirname "$OUT")/.env.XXXXXX")"
trap 'rm -f "$TMP" "$TMP.json"' EXIT

aws ssm get-parameters-by-path "${REGION_ARG[@]}" --path "$SSM_PATH" --recursive --with-decryption \
  --output json > "$TMP.json"

python3 - "$TMP.json" "$TMP" "$SSM_PATH" <<'PY'
import json, re, sys
src, dst, prefix = sys.argv[1:4]
params = json.load(open(src)).get("Parameters", [])
names = []
with open(dst, "w") as f:
    f.write("# Rendered from SSM Parameter Store by deploy/aws/render-env.sh. Do not edit by hand.\n")
    for p in sorted(params, key=lambda p: p["Name"]):
        name = p["Name"][len(prefix):]
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name):
            print(f"skip: parameter name not a valid variable: {name}", file=sys.stderr)
            continue
        value = p["Value"]
        if "\n" in value or ("'" in value and ("$" in value or '"' in value or "\\" in value)):
            sys.exit(f"refusing: {name} contains characters that cannot be quoted safely for docker compose; regenerate it (openssl rand -hex 32)")
        # single quotes = literal in compose env files (no $ interpolation)
        f.write(f"{name}='{value}'\n" if "'" not in value else f'{name}="{value}"\n')
        names.append(name)
print(f"rendered {len(names)} variables: {', '.join(names)}")
PY
mv -f "$TMP" "$OUT"
chmod 600 "$OUT"
echo "wrote $OUT (mode 600). Next: python3 scripts/preflight.py --env-file $OUT --compose"
