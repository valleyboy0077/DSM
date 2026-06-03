#!/bin/bash
cd /home/sarah/dsm-scaffold/frontend
npx vite build 2>&1
echo "BUILD_EXIT: $?"
