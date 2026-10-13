#!/bin/bash
cd /home/matt/aomstream
SUDO="sudo -S -p '' --"
PW="$HOME/.sudo_asus"

run() {
  local pct="$1"
  if [ "$pct" = "0" ]; then
    $SUDO tc qdisc replace dev lo root pfifo < "$PW" >/dev/null
  else
    $SUDO tc qdisc replace dev lo root netem loss "$pct" < "$PW" >/dev/null
  fi
  echo "### perda $pct%"
  .venv/bin/python3 test_loss.py 20 2>&1 | tail -7
}

for p in 0 1 2 5; do
  run "$p"
done

$SUDO tc qdisc replace dev lo root pfifo < "$PW" >/dev/null
echo "qdisc restaurado"
