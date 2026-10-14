#!/bin/bash
# Prova de roteamento por slot com um stream sintetico.
# O ponto medido: o monitor do slot N so traz o audio daquele stream.
# Sem o link, o stream vai para o sink default e o monitor do slot fica mudo.
set -e

SLOT=${1:-1}

ffmpeg -y -hide_banner -loglevel error -f lavfi -i sine=frequency=440:duration=12 -ac 2 /tmp/tone.wav

# pw-play le stdin para comandos; por isso o redirect e obrigatorio
/usr/bin/pw-play /tmp/tone.wav < /dev/null &
P=$!
sleep 2

python3 /tmp/audio_route.py "$P" "$SLOT"
sleep 1

ffmpeg -y -hide_banner -loglevel error -f pulse -i "aom_slot${SLOT}.monitor" -t 4 -f wav /tmp/cap_slot.wav
echo "--- monitor do slot $SLOT ---"
ffmpeg -hide_banner -i /tmp/cap_slot.wav -af volumedetect -f null - 2>&1 | grep -E "mean_volume|max_volume"

echo "--- monitor do slot vizinho (deve ser silencio) ---"
OTHER=$((SLOT + 1))
ffmpeg -y -hide_banner -loglevel error -f pulse -i "aom_slot${OTHER}.monitor" -t 2 -f wav /tmp/cap_other.wav
ffmpeg -hide_banner -i /tmp/cap_other.wav -af volumedetect -f null - 2>&1 | grep -E "mean_volume|max_volume"

wait $P 2>/dev/null || true
