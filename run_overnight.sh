#!/bin/bash
# Overnight runner: 2 full games back to back
# Usage: nohup bash run_overnight.sh &

set -e
cd "$(dirname "$0")"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
LOGFILE="logs/overnight_${TIMESTAMP}.log"
mkdir -p logs saves

echo "=== OVERNIGHT RUN: 2 games x 111 turns ===" | tee "$LOGFILE"
echo "Started: $(date)" | tee -a "$LOGFILE"
echo "" | tee -a "$LOGFILE"

# Game 1
echo "=== GAME 1 START: $(date) ===" | tee -a "$LOGFILE"
python run_game.py --backend mlx --situation-engine --verbose --save-dir "saves/overnight_g1_${TIMESTAMP}" \
    --log-dir "logs" \
    2>&1 | tee -a "$LOGFILE"
echo "=== GAME 1 END: $(date) ===" | tee -a "$LOGFILE"
echo "" | tee -a "$LOGFILE"

# Game 2
echo "=== GAME 2 START: $(date) ===" | tee -a "$LOGFILE"
python run_game.py --backend mlx --situation-engine --verbose --save-dir "saves/overnight_g2_${TIMESTAMP}" \
    --log-dir "logs" \
    2>&1 | tee -a "$LOGFILE"
echo "=== GAME 2 END: $(date) ===" | tee -a "$LOGFILE"
echo "" | tee -a "$LOGFILE"

echo "=== OVERNIGHT RUN COMPLETE: $(date) ===" | tee -a "$LOGFILE"
