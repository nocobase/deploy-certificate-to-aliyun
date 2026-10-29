"""Check the HTTPS certificates served by Aliyun CDN domains, and renew the ones close to expiry.

For every configured certificate that has a CDN domain needing renewal, a Let's Encrypt certificate with the configured names (wildcards allowed) is issued with acme.sh (DNS-01 via Aliyun DNS), then uploaded to each of its CDN domains.

Results are written to `GITHUB_OUTPUT` (`renewed`, `failed`, `rows`) for the notification steps, and to `GITHUB_STEP_SUMMARY`.
"""

import datetime
import json
import os
import re
import socket
import ssl
import subprocess
import sys
from pathlib import Path

from cryptography import x509

ACME_SH = os.path.expanduser(os.getenv("ACME_SH") or "~/.acme.sh/acme.sh")
CERT_DIR = Path(os.path.expanduser("~/certs"))


def env_flag(name):
    return (os.getenv(name) or "").lower() == "true"


def split_names(text):
    return [d.lower() for d in re.split(r"[\s,;]+", text.strip()) if d]


def parse_config(text):
    """Parse `CDN_DOMAINS`, one certificate per line: `<certificate names>: <CDN domains>`.

    Names on both sides are separated by commas, semicolons or spaces, e.g. `*.example.com; a.docs.example.com: cdn.example.com, a.docs.example.com`.
    Returns a list of (names, cdn_domains).
    """
    config = []
    seen = set()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"CDN_DOMAINS line {lineno}: expected `<certificate names>: <CDN domains>`, got {raw!r}")
        left, right = line.split(":", 1)
        names, cdns = list(dict.fromkeys(split_names(left))), list(dict.fromkeys(split_names(right)))
        if not names or not cdns:
            raise ValueError(f"CDN_DOMAINS line {lineno}: missing certificate names or CDN domains")
        for name in names:
            if not re.fullmatch(r"(\*\.)?([a-z0-9-]+\.)+[a-z0-9-]+", name):
                raise ValueError(f"CDN_DOMAINS line {lineno}: invalid certificate name {name!r}")
        for cdn in cdns:
            # A wildcard only matches one label: `*.example.com` covers `docs.example.com` but not `a.docs.example.com`.
            if not any(covers(name, cdn) for name in names):
                raise ValueError(f"CDN_DOMAINS line {lineno}: {cdn} is not covered by any of {', '.join(names)}")
            if cdn in seen:
                raise ValueError(f"CDN_DOMAINS line {lineno}: {cdn} is listed more than once")
            seen.add(cdn)
        config.append((names, cdns))
    if not config:
        raise ValueError("CDN_DOMAINS is empty")
    return config


def covers(name, host):
    """Whether a certificate name matches host, following TLS wildcard rules."""
    if name.startswith("*."):
        suffix = name[1:]
        return host.endswith(suffix) and "." not in host[: -len(suffix)]
    return host == name


def live_cert(host):
    """Return (not_valid_after, names) of the certificate currently served on host:443."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, 443), timeout=15) as sock:
        with ctx.wrap_socket(sock, server_hostname=host) as tls:
            der = tls.getpeercert(binary_form=True)
    cert = x509.load_der_x509_certificate(der)
    try:
        names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    except x509.ExtensionNotFound:
        names = []
    return cert.not_valid_after_utc, [n.lower() for n in names]


def issue(names):
    out = CERT_DIR / names[0].replace("*", "_")
    out.mkdir(parents=True, exist_ok=True)
    key_file, fullchain_file = out / "privkey.pem", out / "fullchain.pem"
    cmd = [
        ACME_SH, "--issue", "--server", "letsencrypt", "--dns", "dns_ali",
        *(arg for name in names for arg in ("-d", name)), "--keylength", "2048",
        "--key-file", str(key_file), "--fullchain-file", str(fullchain_file),
    ]
    env = dict(os.environ, Ali_Key=os.environ["ALIYUN_ACCESS_KEY_ID"], Ali_Secret=os.environ["ALIYUN_ACCESS_KEY_SECRET"])
    print(f"::group::acme.sh issue {', '.join(names)}", flush=True)
    try:
        subprocess.run(cmd, check=True, env=env)
    finally:
        print("::endgroup::", flush=True)
    cert = x509.load_pem_x509_certificate(fullchain_file.read_bytes())
    return fullchain_file.read_text(), key_file.read_text(), cert.not_valid_after_utc


def deploy(client, cdn, fullchain, key):
    from aliyunsdkcdn.request.v20180510.SetCdnDomainSSLCertificateRequest import SetCdnDomainSSLCertificateRequest

    request = SetCdnDomainSSLCertificateRequest()
    request.set_DomainName(cdn)
    # The name must be unique in the certificate service, so include the time in case of several runs per day.
    request.set_CertName(f"{cdn}-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d%H%M%S}")
    request.set_CertType("upload")
    request.set_SSLProtocol("on")
    request.set_SSLPub(fullchain)
    request.set_SSLPri(key)
    request.set_CertRegion(os.getenv("ALIYUN_CERT_REGION") or "cn-hangzhou")
    response = client.do_action_with_exception(request)
    print(f"{cdn}: {response.decode()}")


def fmt_date(dt):
    return dt.strftime("%Y-%m-%d") if dt else "-"


def write_outputs(rows):
    renewed = sum(1 for r in rows if r["status"] == "renewed")
    failed = sum(1 for r in rows if r["status"] == "failed")
    table = [{"domain": r["domain"], "expires": r["expires"], "result": r["result"]} for r in rows]
    if path := os.getenv("GITHUB_OUTPUT"):
        with open(path, "a") as f:
            f.write(f"renewed={renewed}\nfailed={failed}\nrows={json.dumps(table, ensure_ascii=False)}\n")
    if path := os.getenv("GITHUB_STEP_SUMMARY"):
        with open(path, "a") as f:
            f.write("| CDN domain | Expires | Result |\n|---|---|---|\n")
            f.writelines(f"| {r['domain']} | {r['expires']} | {r['result']} |\n" for r in table)
    return failed


def main():
    config = parse_config(os.getenv("CDN_DOMAINS") or "")
    renew_before = int(os.getenv("RENEW_BEFORE_DAYS") or 30)
    force, dry_run = env_flag("FORCE"), env_flag("DRY_RUN")
    now = datetime.datetime.now(datetime.timezone.utc)

    rows = {}
    pending = {}
    for index, (names, cdns) in enumerate(config):
        for cdn in cdns:
            row = rows[cdn] = {"domain": cdn, "expires": "-", "status": "ok", "result": ""}
            try:
                expires, names = live_cert(cdn)
                days = (expires - now).days
                row["expires"] = fmt_date(expires)
                if not any(covers(n, cdn) for n in names):
                    reason = "当前证书不匹配该域名"
                elif days < renew_before:
                    reason = f"剩 {days} 天"
                else:
                    reason = None
            except (OSError, ValueError) as e:
                print(f"{cdn}: cannot read the live certificate: {e}")
                days, reason = None, "无法读取当前证书"
            left = f", {days} days left" if days is not None else ""
            print(f"{cdn}: expires {row['expires']}{left}" + (f", needs renewal ({reason})" if reason else ""))
            if reason or force:
                pending.setdefault(index, []).append(cdn)
                row["result"] = reason or "强制续期"
            else:
                row["result"] = f"无需续期（剩 {days} 天）"

    if dry_run:
        for cdns in pending.values():
            for cdn in cdns:
                rows[cdn]["result"] = f"需续期：{rows[cdn]['result']}（dry run，未执行）"
        return write_outputs(rows.values())

    client = None
    if pending:
        from aliyunsdkcore.client import AcsClient

        for name in ("ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET"):
            if not os.getenv(name):
                raise ValueError(f"{name} is not set")

        client = AcsClient(os.environ["ALIYUN_ACCESS_KEY_ID"], os.environ["ALIYUN_ACCESS_KEY_SECRET"], "cn-hangzhou")

    for index, cdns in pending.items():
        names = config[index][0]
        try:
            fullchain, key, expires = issue(names)
        except Exception as e:
            print(f"::error::Failed to issue the certificate for {', '.join(names)}: {e}")
            for cdn in cdns:
                rows[cdn].update(status="failed", result=f"签发失败（{names[0]}）")
            continue
        for cdn in cdns:
            try:
                deploy(client, cdn, fullchain, key)
                rows[cdn].update(status="renewed", expires=fmt_date(expires), result="已续期")
            except Exception as e:
                print(f"::error::Failed to deploy the certificate to {cdn}: {e}")
                rows[cdn].update(status="failed", result="部署到 CDN 失败")

    return write_outputs(rows.values())


if __name__ == "__main__":
    try:
        sys.exit(1 if main() else 0)
    except ValueError as e:
        print(f"::error::{e}")
        sys.exit(1)
