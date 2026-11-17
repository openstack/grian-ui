#!/bin/bash
# Download the third-party JavaScript libraries used by the grian-ui
# templates. The files are not tracked in git (see .gitignore).
#
# Can be run from any directory; files are always written next to this
# script. Downloads go to temporary files first so a failed download
# never leaves the vendor directory without the libraries.
set -euo pipefail

CHART_JS_VERSION=4.4.1
HTMX_VERSION=2.0.4

VENDOR_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

fetch() {
    local url=$1 dest=$2
    local tmp
    tmp=$(mktemp "${VENDOR_DIR}/.${dest}.XXXXXX")
    if ! curl --fail --silent --show-error --location --retry 3 \
            -o "${tmp}" "${url}"; then
        rm -f "${tmp}"
        echo "Failed to download ${url}" >&2
        return 1
    fi
    chmod 0644 "${tmp}"
    mv -f "${tmp}" "${VENDOR_DIR}/${dest}"
}

fetch "https://unpkg.com/chart.js@${CHART_JS_VERSION}/dist/chart.umd.js" chart.umd.js
fetch "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.js" htmx.js
