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

#### `CDN_DOMAINS` format

Each line describes one certificate, split by a colon into two parts:

```
<certificate names>: <CDN domains>
```

- **Left side: the names on the certificate.** One certificate can contain many domain names; a browser accepts it when one of the names matches the domain it visits. This is what you would enter as the domains of a certificate request in tools like Certimate, so such a list can be pasted as is.
- **Right side: the Aliyun CDN domains to deploy it to.** After issuing, the certificate is uploaded to each of these CDN accelerated domains. Every one of them must be covered by a name on the left.

Names on both sides are separated by commas, semicolons or spaces. Lines starting with `#` are ignored.

#### Example

Suppose these domains are served by Aliyun CDN:

- `www.example.com`
- `docs.example.com`
- `v1.docs.example.com`
- `v2.docs.example.com`

One certificate can cover all of them:

```
*.example.com;v1.docs.example.com;v2.docs.example.com: www.example.com, docs.example.com, v1.docs.example.com, v2.docs.example.com
```

The left side puts these names on the certificate:

| Certificate name | Matches |
| :-- | :-- |
| `*.example.com` | Every one-level subdomain, such as `www.example.com` and `docs.example.com` |
| `v1.docs.example.com` | Only itself |
| `v2.docs.example.com` | Only itself |

A wildcard only matches one label: `*.example.com` covers `docs.example.com`, but neither `example.com` nor `v1.docs.example.com`. That is why `v1.docs.example.com` and `v2.docs.example.com` are listed explicitly.

The right side deploys the certificate to the four CDN domains: `www` and `docs` are covered by `*.example.com`, and `v1.docs` and `v2.docs` by their own names.

To manage more certificates, add one line for each, for example another domain that also needs its apex:

```
*.example.com;v1.docs.example.com;v2.docs.example.com: www.example.com, docs.example.com, v1.docs.example.com, v2.docs.example.com
example.org;*.example.org: example.org, img.example.org
```

#### What a run does with it

1. Reads the certificate currently served by each CDN domain on the right and checks how many days it has left.
2. If any of them has fewer than `RENEW_BEFORE_DAYS` left, issues a new certificate with the names on the left.
3. Deploys the new certificate to the CDN domains that need it, then sends the Feishu notification.

The workflow rejects a CDN domain that no name on its line covers, and a CDN domain listed on more than one line.

### 4. Enable and run the workflow

1. Open the **Actions** tab. Forked repositories have workflows disabled by default; click the button to enable them.
2. Select **Renew Aliyun CDN certificates → Run workflow**. Check **dry_run** first to verify the configuration without issuing anything; the run summary and the Feishu card list each domain with its expiry date.
3. Run it again without **dry_run**. Check **force** to renew every domain regardless of expiry.
4. Check the certificates in the [Aliyun CDN console](https://cdn.console.aliyun.com/). It usually takes a few minutes to take effect across the CDN.

## Notes

- Domain names are configured as variables rather than secrets so they stay readable in logs. Never commit AccessKeys to the repository.
- Each renewal uploads a new certificate named `<cdn-domain>-<UTC timestamp>` to the Certificate Management Service. Old certificates are not deleted automatically.
- Let's Encrypt allows 5 duplicate certificates per week for the same set of domain names. Avoid repeatedly running with **force**.
