#!/bin/zsh
# Hermes lane scheduler - kept alive by com.rsg.hermes.scheduler LaunchAgent
cd /Users/lamarcoates/Documents/GitHub/rsg-hermes || exit 1
exec .venv/bin/python -m hermes.scheduler >> \
  /Users/lamarcoates/Library/Logs/rsg-hermes-scheduler.log 2>&1
