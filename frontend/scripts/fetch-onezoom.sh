#!/usr/bin/env bash
# Put OneZoom's tree viewer (OZentry.js) where public/oz-viewer/index.html loads it.
#
# OZentry.js is © OneZoom CIO under the OneZoom Non-Profit Source-Available Licence v1:
# non-commercial use for science and science education only, no sponsorable trees, and
# modifications that are made public must be shared back with OneZoom. Read
# https://github.com/OneZoom/OZtree/blob/main/license.txt before deploying.
#
# The viewer also needs its English UI strings (OZstrings), saved as js_strings.json.
#
#   fetch-onezoom.sh              copy the bundle onezoom.org currently serves
#   fetch-onezoom.sh PATH         use a bundle built from source: in a checkout of
#                                 github.com/OneZoom/OZtree, `npm ci && npx webpack --mode production`
#                                 writes OZprivate/rawJS/OZTreeModule/dist/OZentry.js
set -euo pipefail

dest="$(cd "$(dirname "$0")/.." && pwd)/public/oz-viewer/vendor"
mkdir -p "$dest"
site=https://www.onezoom.org
ua="apweb-app (OneZoom integration prototype)"
curl -fsS -A "$ua" -o "$dest/js_strings.json" "$site/treeviewer/js_strings.json?lang=en"

if [[ $# -ge 1 ]]; then
  cp "$1" "$dest/OZentry.js"
  echo "copied $1 -> $dest/OZentry.js"
  exit 0
fi

path=$(curl -fsS -A "$ua" "$site/life/" | grep -o '/OZtree/static/OZTreeModule/dist/OZentry\.[0-9a-f]*\.js' | head -1)
if [[ -z "$path" ]]; then
  echo "could not find OZentry.js on $site/life/" >&2
  exit 1
fi
curl -fsS -A "$ua" -o "$dest/OZentry.js" "$site$path"
echo "$site$path" > "$dest/SOURCE"
echo "fetched $site$path -> $dest/OZentry.js"
