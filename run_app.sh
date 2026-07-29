#!/bin/bash

# Define the filename to look for
FILE_NAME="spp_support_bundle_Automation_macOS.py"

echo "=========================================="
echo " Searching for $FILE_NAME..."
echo "=========================================="

# Check Downloads folder first
if [ -f "$HOME/Downloads/$FILE_NAME" ]; then
    TARGET_PATH="$HOME/Downloads/$FILE_NAME"
# Check Desktop folder next
elif [ -f "$HOME/Desktop/$FILE_NAME" ]; then
    TARGET_PATH="$HOME/Desktop/$FILE_NAME"
# Check current directory
elif [ -f "./$FILE_NAME" ]; then
    TARGET_PATH="./$FILE_NAME"
else
    echo "❌ Error: Could not find $FILE_NAME on Desktop, Downloads, or Current Directory."
    exit 1
fi

echo "✅ Found file at: $TARGET_PATH"
echo "🚀 Running Python script..."
echo "------------------------------------------"

# Execute the Python script
python3 "$TARGET_PATH"