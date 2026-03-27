#!/bin/bash
export NVM_DIR="/home/ubuntu/.nvm"
[ -s "$NVM_DIR/nvm.sh" ] && \. "$NVM_DIR/nvm.sh"

set -a
source /home/ubuntu/trading_scan_alerts/.env
set +a

PHASE=$1
LOG=/home/ubuntu/scanner.log
SCAN_DIR=/home/ubuntu/trading_scan_alerts

if [ -z "$PHASE" ]; then
    echo "$(date): ERROR - run_build_list.sh requires phase argument" >> $LOG
    exit 1
fi

# Check if today is a NYSE trading day
TRADING_CHECK=$(python3.11 $SCAN_DIR/scripts/is_trading_day.py 2>&1)
if [ $? -ne 0 ]; then
    echo "$(date): [$PHASE] Skipping - $TRADING_CHECK" >> $LOG
    exit 0
fi

cd $SCAN_DIR
echo "$(date): Building [$PHASE] scan list..." >> $LOG
python3.11 scan_reversal_alert.py --build-list "$PHASE" >> $LOG 2>&1
BUILD_EXIT=$?
echo "$(date): [$PHASE] scan list build complete (exit $BUILD_EXIT)" >> $LOG

# Archive the scan list snapshot for historical tracking
if [ $BUILD_EXIT -eq 0 ]; then
    python3.11 $SCAN_DIR/scripts/archive_scan_list.py "$PHASE" >> $LOG 2>&1
fi

exit $BUILD_EXIT
