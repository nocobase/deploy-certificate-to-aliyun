# Deploy Certificate to Aliyun

Automatically renew Let's Encrypt certificates for Aliyun (Alibaba Cloud) CDN domains with GitHub Actions.

## How it works

Every Monday the workflow:

1. Connects to each configured CDN domain over HTTPS and reads the certificate it currently serves.
2. Marks a domain for renewal when its certificate expires within `RENEW_BEFORE_DAYS` (30 by default), does not match the domain, or cannot be read.
3. For each configured certificate that has CDN domains to renew, issues a Let's Encrypt certificate with the configured names (wildcards allowed) using [acme.sh](https://github.com/acmesh-official/acme.sh) and DNS-01 validation through Aliyun DNS.
4. Uploads the certificate to every CDN domain that needs it.
5. Sends a Feishu (Lark) card when certificates were renewed or something failed. Scheduled runs with nothing to renew stay silent; manual runs always report.

Domains that fail are retried on the next weekly run. Each run also re-enables the workflow, so GitHub does not disable the schedule after 60 days without repository activity.

## Setup

### 1. Create an Aliyun RAM user

In the [RAM console](https://ram.console.aliyun.com/), create a user dedicated to this workflow with OpenAPI access only, then create an AccessKey for it. Grant these policies:

- `AliyunDNSFullAccess`: add and remove the TXT records used for DNS validation
- `AliyunCDNFullAccess`: set the HTTPS certificate of CDN domains
- `AliyunYundunCertFullAccess`: store uploaded certificates in the Certificate Management Service

The certificate domains must be hosted on Aliyun DNS under the same Aliyun account.

### 2. Configure secrets

In **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Required | Description |
| :-- | :-- | :-- |
| `ALIYUN_ACCESS_KEY_ID` | Yes | AccessKey ID of the RAM user |
| `ALIYUN_ACCESS_KEY_SECRET` | Yes | AccessKey Secret of the RAM user |
| `FEISHU_WEBHOOK_URL` | No | Feishu custom bot webhook URL. Notifications are skipped when unset |
| `EMAIL` | No | Email for the Let's Encrypt ACME account |

If the Feishu bot has a security setting, use a keyword (e.g. `证书`, which every card title contains) or an IP allowlist; signature verification is not supported.

### 3. Configure variables

In **Settings → Secrets and variables → Actions → Variables**:

| Variable | Required | Description |
| :-- | :-- | :-- |
| `CDN_DOMAINS` | Yes | Certificates to issue and the CDN domains to deploy them to, see below |
| `RENEW_BEFORE_DAYS` | No | Renew when fewer days than this remain. Default `30` |
| `ALIYUN_CERT_REGION` | No | Region of the Certificate Management Service. Default `cn-hangzhou`; use `ap-southeast-1` for international accounts |

`CDN_DOMAINS` has one certificate per line in the form `<certificate names>: <CDN domains>`. Names on both sides are separated by commas, semicolons or spaces, and lines starting with `#` are ignored:

```
*.example.com; a.docs.example.com; b.docs.example.com: cdn.example.com, docs.example.com, a.docs.example.com, b.docs.example.com
example.org; *.example.org: example.org, img.example.org
```

The left side lists every name on the certificate, wildcards included; the right side lists the CDN domains that use it. A wildcard only matches one label: `*.example.com` covers `docs.example.com` but neither `example.com` nor `a.docs.example.com`, so list those explicitly. The workflow rejects CDN domains that their certificate does not cover, and CDN domains listed on more than one line.

### 4. Enable and run the workflow

1. Open the **Actions** tab. Forked repositories have workflows disabled by default; click the button to enable them.
2. Select **Renew Aliyun CDN certificates → Run workflow**. Check **dry_run** first to verify the configuration without issuing anything; the run summary and the Feishu card list each domain with its expiry date.
3. Run it again without **dry_run**. Check **force** to renew every domain regardless of expiry.
4. Check the certificates in the [Aliyun CDN console](https://cdn.console.aliyun.com/). It usually takes a few minutes to take effect across the CDN.

## Notes

- Domain names are configured as variables rather than secrets so they stay readable in logs. Never commit AccessKeys to the repository.
- Each renewal uploads a new certificate named `<cdn-domain>-<UTC timestamp>` to the Certificate Management Service. Old certificates are not deleted automatically.
- Let's Encrypt allows 5 duplicate certificates per week for the same set of domain names. Avoid repeatedly running with **force**.
