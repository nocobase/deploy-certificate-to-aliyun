#!/usr/bin/env bash
# Send a Feishu interactive card notification. Adapted from nocobase/nocobase scripts/feishu-notify.sh.
#
# Required environment variables:
#   WEBHOOK_URL   - Feishu bot webhook URL
#   TITLE         - Card title
#   STATUS        - "success" or "failure"
#   WORKFLOW_URL  - Link to the GitHub Actions run
#
# Optional environment variables:
#   STATUS_TEXT_SUCCESS - Custom success text (defaults to "成功")
#   STATUS_TEXT_FAILURE - Custom failure text (defaults to "失败")
#   CONTENT       - Card body content in Markdown
#   REPOSITORY    - owner/repo; appended to the footer when set
#   TABLE_JSON    - `[{"domain":…,"expires":…,"result":…}]`; renders a table under CONTENT
#
# The card uses schema 2.0, whose `table` element takes structured rows (card 1.0 `lark_md` has no table syntax). 2.0 has no `action` element, so the button lives in a `column_set` with `behaviors`.

set -euo pipefail

: "${WEBHOOK_URL:?WEBHOOK_URL is required}"
: "${TITLE:?TITLE is required}"
: "${STATUS:?STATUS must be 'success' or 'failure'}"
: "${WORKFLOW_URL:?WORKFLOW_URL is required}"

BUILD_TIME="$(TZ="Asia/Shanghai" date +"%Y-%m-%d %H:%M:%S")"

if [[ "$STATUS" == "success" ]]; then
  HEADER_TEMPLATE="green"
  STATUS_TEXT="${STATUS_TEXT_SUCCESS:-成功}"
  STATUS_EMOJI="✅"
else
  HEADER_TEMPLATE="red"
  STATUS_TEXT="${STATUS_TEXT_FAILURE:-失败}"
  STATUS_EMOJI="❌"
fi

# Keep the colon outside `**`: card 2.0 markdown follows CommonMark, where `**状态：**✅` does not close the bold and shows literal asterisks. Use real newlines, not `\n`, or jq escapes them into a visible backslash-n.
FOOTER="**状态**：${STATUS_EMOJI} ${STATUS_TEXT}
**时间**：${BUILD_TIME}"
[[ -n "${REPOSITORY:-}" ]] && FOOTER="${FOOTER}
**仓库**：${REPOSITORY}"

TABLE_JSON="${TABLE_JSON:-}"
if [[ -n "$TABLE_JSON" ]] && ! jq -e 'type == "array" and length > 0' <<< "$TABLE_JSON" > /dev/null 2>&1; then
  TABLE_JSON=""
fi

PAYLOAD=$(jq -n \
  --arg title "$TITLE" \
  --arg template "$HEADER_TEMPLATE" \
  --arg content "${CONTENT:-}" \
  --arg footer "$FOOTER" \
  --arg url "$WORKFLOW_URL" \
  --argjson rows "${TABLE_JSON:-null}" \
  '
  {
    msg_type: "interactive",
    card: {
      schema: "2.0",
      header: {
        title: { tag: "plain_text", content: $title },
        template: $template
      },
      body: {
        elements: (
          (if $content == "" then [] else [
            { tag: "markdown", content: $content }
          ] end)
          + (if $rows == null then [] else [
            {
              tag: "table",
              page_size: 20,
              row_height: "low",
              header_style: {
                background_style: "grey",
                bold: true,
                text_size: "normal"
              },
              columns: [
                { name: "domain", display_name: "CDN 域名", data_type: "text" },
                { name: "expires", display_name: "证书到期", data_type: "text" },
                { name: "result", display_name: "结果", data_type: "text" }
              ],
              rows: $rows
            }
          ] end)
          + [
            { tag: "hr" },
            { tag: "markdown", content: $footer },
            {
              tag: "column_set",
              columns: [
                {
                  tag: "column",
                  width: "auto",
                  elements: [
                    {
                      tag: "button",
                      text: { tag: "plain_text", content: "查看运行详情" },
                      type: "primary",
                      width: "default",
                      behaviors: [ { type: "open_url", default_url: $url } ]
                    }
                  ]
                }
              ]
            }
          ]
        )
      }
    }
  }')

RESPONSE_BODY=$(mktemp)
HTTP_CODE=$(curl -s -o "$RESPONSE_BODY" -w "%{http_code}" -X POST "$WEBHOOK_URL" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD")

# Feishu answers a malformed card with HTTP 200 and a non-zero `code`, so check both.
RESPONSE_CODE=$(jq -r '.code // 0' < "$RESPONSE_BODY" 2>/dev/null || echo 0)
if [[ "$HTTP_CODE" -ge 200 && "$HTTP_CODE" -lt 300 && "$RESPONSE_CODE" == "0" ]]; then
  echo "Feishu notification sent successfully (HTTP ${HTTP_CODE})"
else
  echo "Failed to send Feishu notification (HTTP ${HTTP_CODE})" >&2
  echo "Response: $(cat "$RESPONSE_BODY")" >&2
  rm -f "$RESPONSE_BODY"
  exit 1
fi
rm -f "$RESPONSE_BODY"
