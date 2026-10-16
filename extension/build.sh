#!/bin/sh
# Package the extension for the Chrome Web Store: only the files it runs.
set -e
cd "$(dirname "$0")"
rm -f recruited-extension.zip
zip -q -r recruited-extension.zip manifest.json content.js content.css popup.html icons
echo "wrote $(pwd)/recruited-extension.zip"
