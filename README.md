# aom-multiplayer

Streaming de **Age of Mythology: Extended Edition** para navegador: o jogo roda em Wine
numa máquina (`asus`), cada jogador recebe vídeo (WebRTC/H264, NVENC) e devolve mouse/teclado
(XTest). O multiplayer é o multiplayer do próprio jogo, entre instâncias isoladas em
network namespaces.

Repositório **sem binários**: só texto (código, scripts, notas de análise). Nada de `.exe`,
`.dll`, `.snap`, screenshots, `__pycache__`, `.venv`.

## Como funciona

```
navegador  ──WebSocket (signalização)──►  webrtc_server.py
           ◄──WebRTC: H264 (NVENC) + áudio (AAC/Opus) + input──►
                                            │
                                            │ captura X11 (:2)
                                            │ input via XTest
                                            │ áudio: monitor do null-audio-sink do slot
                                            ▼
                                    Xorg headless 1280x720
                                            ▲
                          instância do jogo em network namespace (ns1, ns2, ...)
                          cada uma com seu WINEPREFIX, IP próprio na br0,
                          stream de áudio próprio roteado para o seu slot
```

- **Identidade no multiplayer do jogo é o IP** (`BSocksProtocol::getLocalIP`). Por isso cada
  instância precisa de um IP distinto → network namespace + veth na `br0` (`10.77.0.0/24`).
- **Broadcast de descoberta usa `255.255.255.255`**, o que exige rota default dentro do netns
  (`ip route add default dev vethN`); sem isso o `sendto` falha com `ENETUNREACH`
  (winsock status `0xc000023c`).
- Ports do jogo: **2299** (descoberta LAN) e **2300** (sessão do jogo, bidirecional).
- Cada jogador precisa de um `WINEPREFIX` próprio (guarda de instância é por `wineserver`);
  os arquivos do jogo são compartilhados somente-leitura, o estado gravável é isolado por
  overlayfs. Custo por jogador: o prefixo (~581 MB), não os 2.8 GB do jogo.
- Jogo roda como `matt` mesmo dentro do netns: `ip netns exec nsN su matt -c '...'`.

## Áudio por jogador

- Wine usa `winepulse.drv`, que conecta ao PipeWire. O stream de uma instância **só existe
  enquanto ela toca som** (uma instância parada no menu não gera node de stream).
- Um `null-audio-sink` por jogador, definido por drop-in de `context.objects`
  (`scripts/pipewire-slots.conf`). Sinks criados por `pw-cli create-node adapter` ou por
  script Lua do WirePlumber **não** funcionam neste ambiente: a ativação do session item
  falha com `Object activation aborted: proxy destroyed`. Os objetos de `context.objects`
  funcionam.
- O primeiro sink da lista (`aom_default`) é para onde o policy encaminha todo stream, e ele
  **não** é capturado: é o desvio que permite linkar cada instância ao seu slot sem
  contaminar os outros slots.
- Roteamento por instância: `scripts/audio_route.py <pid> <slot>`. Mapeia
  `client.id` → `pipewire.sec.pid` (no objeto Client, o `client.id` é o próprio id do objeto,
  não um prop) e cria o link com `pw-link` — `pw-cli create-link` retorna 0 e não cria nada.
- **Isolamento medido**: stream A linkado a `aom_slot1` e stream B a `aom_slot2`; o monitor de
  slot1 continha só 440 Hz (Goertzel 1447.8, 1000 Hz = 0.1) e o de slot2 só 1000 Hz
  (610.1, 440 Hz = 0.3).
- Codec: **AAC em fMP4 fragmentado** (AAC é universal em MSE; Opus em fMP4 funciona no Chrome
  mas não é universal). Fragmentos de 100 ms via `-frag_duration`, que é AVOption do muxer mp4
  e **não** um movflags — escrever `frag_duration+empty_moov` faz o ffmpeg sair com
  "Conversion failed!". Medido: 57 fragmentos em 6 s, ~1,7 kB cada.
- O cliente tem um `SourceBuffer` de áudio separado (kinds 2 e 3 do protocolo). O áudio vai por
  MSE nos dois caminhos, porque WebCodecs decodifica vídeo e um `AudioDecoder` exigiria
  extrair o AAC do fMP4. `play()` com som é bloqueado pelo navegador até um gesto do usuário
  (botão "ativar som").

## Diretórios

| Pasta | Conteúdo |
|---|---|
| `src/` | serviço: `webrtc_server.py` (aiortc + NVENC), `server.py` (pipeline MSE/Annex-B), `index.html` (cliente), `marker.py` (probe de latência), `debug_answer.py` |
| `tests/` | harness de medição: qualidade, latência, perda, jitter buffer, VP8 vs NVENC |
| `scripts/` | plumbing de netns/bridge/X11 e start das instâncias; `pipewire-slots.conf` (um sink por jogador) e `audio_route.py` (stream da instância → slot) |
| `analysis/` | evidências da análise estática do `AoMX.exe` e do `steam_api.dll` (JSON, texto) |
| `STATUS.md` | relatório do que foi **medido** |

## Configuração do encoder (medida, não chute)

```
preset p4, spatial-aq 1, temporal-aq 1
qmin 18, qmax 32, alvo 17 Mbps, GOP 15
```

A qualidade é limitada pelo **`qmax` (teto de QP)**, não pelo bitrate alvo. Escada medida:
qmax 38 → 34.2 dB, 34 → 36.6 dB, 32 → 37.6 dB, 30 → 38.8 dB.

O navegador precisa **forçar H264** via `RTCRtpReceiver.getCapabilities('video').codecs`
(`getReceiverCapabilities` não existe no Chromium 154 → cai em VP8 → libvpx software →
pixelização). `setCodecPreferences` deve incluir o codec `video/rtx` pareado com o H264
(`parameters.apt`), senão o navegador nunca envia NACK.

## Limites medidos

- NVENC: **8 sessões simultâneas** (a 9ª falha com
  `OpenEncodeSessionEx failed: incompatible client key (21)`).
- Teto prático de CPU: **~6 jogadores**.
- Latência do probe: **133–136 ms** (caminho MSE: 225 ms).
