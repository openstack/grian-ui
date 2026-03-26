#!/bin/bash
CHART_JS_VERSION=4.4.1
HTMX_VERSION=2.0.4
rm -f *.js
wget "https://unpkg.com/chart.js@${CHART_JS_VERSION}/dist/chart.umd.js" -O chart.umd.js
wget "https://unpkg.com/htmx.org@${HTMX_VERSION}/dist/htmx.js" -O htmx.js
