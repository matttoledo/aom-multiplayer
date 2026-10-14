# Estado do caminho WebRTC (medido, enquanto você dormia)

## 1. Causa raiz da tela pixelizada — duas, ambas confirmadas por medida

**a) As opções do encoder nunca eram aplicadas.**
No FFmpeg 7 (PyAV 17), `h264_nvenc` **não tem** AVOptions `g`, `max-bitrate`,
`vbv-bufsize`, `qmin`, `qmax`. Passá-las em `codec.options` é silencioso:
`avcodec_open2` devolve-as no dicionário sem aplicar. Prova: `cqp=51` e `cqp=15`
produziam exatamente o mesmo tamanho de frame. Só `bit_rate` valia.
As que realmente aplicam: `preset`, `tune`, `profile`, `level`, `rc`, `coder`,
`delay`, `no-scenecut`, `max_slice_size`, `spatial-aq`, `temporal-aq`.
`gop_size`, `qmin`, `qmax` precisam ir como **campos** do contexto (PyAV expõe).

**b) O `qmin=16` quebrava o controle de taxa.**
Com `qmin=16` o encoder não consegue ficar abaixo do alvo e produz frames
enormes: na média 458 KB/frame em conteúdo ruidoso, e no jogo real access units
de 178–196 pacotes. Isso estoura a capacidade do jitter buffer do aiortc
(**128 pacotes**) → o access unit é truncado → o decoder nunca vê SPS/PPS →
**0 frames decodificados**. Reproduzido deterministicamente em `test_jb_real.py`.

Com `qmin=24` o access unit máximo cai para 84 pacotes e o pipeline decodifica
89/90 frames.

## 2. Configuração final do encoder (medida no jogo real, 120 frames)

| config | PSNR médio | AU máximo | p95 AU | bitrate real |
|---|---|---|---|---|
| qmin default, gop 30 | 34.06 dB | 183 pkt | 41 pkt | 7.6 Mbps |
| **qmin 24, qmax 38, gop 15** | **34.26 dB** | **71 pkt** | **32 pkt** | **7.9 Mbps** |
| qmin 20, qmax 34, gop 30 | 36.38 dB | 104 pkt | 41 pkt | 11.7 Mbps |

Escolhida: `preset p2, tune ll, profile main, rc vbr, coder cabac, delay 0,
no-scenecut 1` + `gop_size 15, qmin 24, qmax 38`.
GOP 15 = resync de 0.5 s em vez de 1 s sob perda, com o mesmo PSNR.
PSNR foi medido sem visão (subamostragem de luma, sem numpy) — é o número objetivo.

## 3. Perda: retransmissão funciona

Medida com `tc qdisc netem` no loopback, cliente headless (aiortc) contra o servidor.

| condição | NACKs | decode failures | p95 intervalo | max stall |
|---|---|---|---|---|
| sem perda | 0 | 0 | 37.3 ms | 51 ms |
| RTT 80 ms + 1% perda (120 s) | 352 | 11 (0.3%) | 81.5 ms | 1547 ms |
| RTT 120 ms + 3% perda | 385 | 8 | 124.8 ms | 520 ms |

- **518 de 529 pacotes perdidos foram recuperados (98%)** no cenário de 1%.
- O histórico de retransmissão do aiortc era **128 pacotes** — menor que um
  keyframe de jogo. Aumentado para **1024** (`RTP_HISTORY_SIZE`). Com isso, em
  5% de perda as falhas de decode caíram de 25 para 0.
- **Bug encontrado no `index.html`:** o `setCodecPreferences` filtrava só o H264 e
  descartava o `video/rtx` pareado (`apt`). Sem o rtx o navegador não pede NACK e
  o servidor não retransmite. Corrigido: H264 + rtx com `apt` apontando para ele.

### Retransmissão confirmada ponta a ponta

O `rtx payload type` no sender é **100** (o diagnóstico anterior mostrava `None`
porque imprimia antes de `receive()`). Com o contador do harness corrigido
(o conjunto de payload types era calculado antes de `__codecs` ser preenchido):

- 180 s, RTT 80 ms, 1% perda: **737 pacotes faltando, 724 recuperados (98,2%)**,
  11 falhas de decode (0,2%), p95 80,9 ms, **p99 113,2 ms**, max 1890 ms.
- 120 s, RTT 80 ms, 2% perda: 1344 faltando, **1297 recuperados (96,5%)**,
  11 falhas, p95 85,4, p99 114,8, max 1713 ms.

O teto do stall é a capacidade do jitter buffer: 512 pacotes ÷ ~34 pacotes/frame
= 15 frames ≈ 500 ms. O `aiortc` nunca entrega um frame com buraco — ele descarta
o frame e o erro propaga até o próximo IDR (0,5 s com GOP 15). O navegador faz
concealment e tende a sofrer menos que o harness.

## 8. Teto de jogadores (medido)

**NVENC: 8 sessões simultâneas.** A 9ª falha com
`OpenEncodeSessionEx failed: incompatible client key (21)`. Com 8 sessões de
720p30 o throughput foi 19,9 Mbps agregado e 30 fps em cada uma, GPU a 34%.

**CPU por instância de jogo** (8 núcleos):

| estado | thread principal | `wined3d_cs` | total |
|---|---|---|---|
| sem limitador de frame | 90–100% | **100%** | ~1,9 núcleo |
| com `enableframeratelimiter 1` + `maxfps 30` | ~75% | ~30% | **~1,1 núcleo** |

O jogo tem um limitador de frame rate de console, e `startup/base.cfg` é um script
de comandos executado na inicialização. Adicionei ao `base.cfg`:

```
enableframeratelimiter 1
maxfps 30
uiFPS 30
```

(backup do original em `/tmp/base.cfg.orig`; `minfps` não foi usado porque força
renderização contínua e custa ~20% de CPU a mais).

**Servidor Python por conexão:** ~16% de um núcleo, 200 MB de RAM.

Teto prático: **~6 jogadores** (CPU), não 8 — o NVENC permitiria 8.
Memória: ~400 MB por instância ociosa, ~860 MB em jogo.

## 9. Estado da máquina

- Os dois servidores estão rodando: `:8080` (MSE) e `:8081` (WebRTC), display `:2`.
- Instância do jogo restaurada no prefixo padrão (`AoMX.exe`, janela 10485761).
  Observação: um `pkill -f AoMX.exe` matou a instância que estava rodando há
  ~4 h; ela foi recriada, mas o estado de jogo anterior se perdeu.
- `base.cfg` alterado (limitador de frame). Se você não quiser isso,
  restaure com `cp /tmp/base.cfg.orig ".../startup/base.cfg"`.

## 4. Latência (probe, servidor desenha marcador magenta e o cliente detecta)

- WebRTC headless, config final, sem perda: **129 ms** (30.2 fps, p95 43.1, max 55.2)
- WebRTC headless, RTT 80 ms + 1% perda: **153 ms**
- MSE (fallback): 225 ms (medido antes, inalterado)

## 5. Caminho MSE estava quebrado

`-vbv-bufsize` **não é opção do CLI** para `h264_nvenc` neste build: o ffmpeg sai com
`Unrecognized option 'vbv-bufsize'`. Trocado por `-bufsize` (campo `rc_buffer_size`).
Verificado após a correção: 450 access units em 15 s (30 fps), 3.7 Mbps, primeiro
fragmento em 336 ms.

## 7. Qualidade: por que estava pixelizado (medido, 13/out)

**A causa não é o bitrate, é o `qmax` (teto de QP).** Experimento decisivo: com alvo
de **2 Mbps** o encoder entregou **8,4 Mbps** — ele *queria* subir o QP para caber no
alvo, mas `qmax=38` impediu. Logo o alvo de bitrate é irrelevante quando o conteúdo é
complexo; o que define a imagem é o QP, e o bitrate real é consequência do conteúdo.

Curva medida no conteúdo real do jogo (120 frames, mesmo material, p4 + AQ):

| qmax | PSNR | bitrate real | max access unit |
|---|---|---|---|
| 38 (antes) | 34.2 dB | 9.5 Mbps | 118 pkt |
| 36 | 35.5 dB | 11.3 Mbps | 115 pkt |
| **34 (agora)** | **36.6 dB** | 14.0 Mbps | 115 pkt |
| 32 | 37.6 dB | 17.1 Mbps | 115 pkt |
| 30 | 38.8 dB | 20.9 Mbps | 115 pkt |
| 26 | 41.7 dB | 30.5 Mbps | 116 pkt |

Coisas que **não** melhoram a qualidade, testadas:
- Subir o bitrate sozinho: alvo 12 Mbps com qmax 38 → só 35.4 dB (satura no QP).
- Baixar fps: 30/24/20/15 fps dão exatamente o mesmo PSNR (34.44 dB), só gastam menos bits.
- Upscale: descartado — o CSS fixa `video` em 1280×720 e o stream é 1280×720, 1:1.
- `deblock=1` vs `deblock=0`: resultado idêntico (opção aceita, no-op neste build).
- `spatial-aq`/`temporal-aq`: aplicam de verdade (+0.3 dB em PSNR; perceptualmente ajudam
  mais, porque redistribuem bits para áreas planas onde o blockiness é mais visível).
  `aq-strength` 1/5/8/12/15: sem efeito mensurável neste conteúdo — fica o default 8.

Conteúdo medido: desvio de luma 54.9 (cena detalhada, texto pequeno de UI). Texto pequeno
em QP 38 vira lama — é exatamente o "pixelizado" percebido.

**Config deployado** (WebRTC e MSE): preset **p4**, `spatial-aq 1`, `temporal-aq 1`,
qmin **18**, qmax **34**, alvo **14 Mbps**, GOP 15. Confirmado no log do servidor.

Regressão de perda com o config novo (netem em `lo`):
- 0%: 1260 frames, 0 falhas, p95 49.5 ms
- 1% por 120 s: 62967 pacotes, 629 NACKs, 927 rtx, **0 access units parciais, 0 falhas de
  decode**, p95 49.3 / p99 53.5 / max 432.9 ms
- 2% por 40 s: 462 NACKs, 943 rtx, 0 falhas

A/B de pacing na mesma content, mesma duração, 0% de perda: p95 49.7 ms (config antigo)
vs 48.9 ms (config novo) — **o AU maior não custa pacing**. O p95 de ~49 ms é o baseline
do harness neste conteúdo, não um custo da qualidade.

Antes/depois em PNG (mesma frame, para você ver): `shots/antes_qmax38.png`,
`shots/depois_qmax34.png`, `shots/original.png`.

## 8. O que falta você confirmar (não tenho visão, nem navegador conectado)
1. Abrir `http://192.168.15.17:8081/?mode=webrtc` e ler:
   - `codec negociado` → deve ser `video/H264`
   - `preferencia de codec` → `h264 forcado + rtx N`
   - `bitrate recebido` → ~7–8 Mbps
   - `LATENCIA MEDIDA POR PROBE`
2. Confirmar que o clique dirige o jogo no modo WebRTC (o `target()` foi corrigido).

## 7. Pendências de arquitetura (não bloqueadas)

- overlayfs por instância + unit systemd (Xorg root, jogo como matt)
- testar o multiplayer entre instâncias (descoberta UDP 2299 + IP distinto por netns)
- decidir se `~/.sudo_asus` é removido no final

## 9. O bug do navegador: H.264 nunca era forçado (corrigido, 13/out)

O `index.html` chamava `RTCRtpTransceiver.getReceiverCapabilities('video')`, que **não
existe no Chromium** → o `try` caía silenciosamente, a preferência nunca era aplicada, e o
Chrome negociava **VP8** → o servidor codificava em **libvpx por software**.

Corrigido para `RTCRtpReceiver.getCapabilities('video').codecs`, com fallback para o
método estático do transceiver se ele existir. Deployado (md5 `9787a40c…`).

**Por que isso explica o "analógico"** — medido no mesmo conteúdo, mesma taxa:

| caminho | packets/frame na MESMA frame repetida | luma que muda entre frames decodificadas |
|---|---|---|
| VP8/libvpx (o que era negociado) | ~39 KB | **66–88%** |
| NVENC H264 (config deployado) | 50–800 B | **0.0–0.7%** |

Frame idêntica de entrada → se o encoder usa P-frames, o pacote colapsa para centenas de
bytes e a saída decodificada não muda. O VP8 gastava 39 KB e mudava 70–88% dos pixels a
cada frame: está codificando **toda frame do zero**, e é daí que vem o "fervimento" numa
tela parada. O NVENC com GOP 15 faz P-frames de verdade.

PSNR no mesmo conteúdo: VP8/libvpx a 9,7 Mbps = **33,1 dB**; NVENC H264 a 14 Mbps =
**36,6 dB**.

Painel corrigido: `kbps` mostrava bps (falta dividir por 1000); `codec negociado` vinha
vazio porque `inbound-rtp` não tem `codecName` — agora resolve `stats.get(r.codecId).mimeType`;
adicionadas as linhas `keyframes decodificados` e `QP medio`. O "offset de relógio" de
−1,79 trilhão ms **não está quebrado**: `client_ts` é `performance.now()` (ms desde o load)
e `server_ts*1000` é epoch ms, então o offset é a origem do relógio por definição — só era
um número feio. Passou a ser exibido em megamilissegundos com essa explicação.

## 10. Transporte: P2P direto por STUN (14/out)

Caminho decidido: navegador → **Cloudflare Access** (login por e-mail, só convidados) →
**Tunnel** → **NPM do Orange Pi** (`npm:80`, sem ModSecurity) → `http://192.168.15.17:8081`.
O **vídeo vai P2P por STUN** entre o navegador do amigo e o asus; a mídia não passa pelo
túnel nem pelo Pi.

Medido no asus, **um único socket UDP**, mesma porta local para os dois destinos:

| destino | IP:porta mapeado |
|---|---|
| `stun.cloudflare.com:3478` | `179.164.111.8:42625` |
| `stun.l.google.com:19302` | `179.164.111.8:42625` |

IP público, sem CGNAT, mapeamento independente do destino e porta preservada: o melhor caso
para P2P (Easy NAT / full-cone). Os dois servidores respondem `Binding-Success` com
`XOR-MAPPED-ADDRESS` no código legado `0x0020` (RFC 3489), não `0x0014` — parser que só
aceita 5389 não enxerga nada. Firewall local não bloqueia: ufw inativo, nenhuma chain de
filtro no OUTPUT.

**Forçar `relay` era erro.** O ICE testa todos os pares em paralelo; um candidato de LAN que
não alcança não atrasa a conexão. Política `ALL` nos dois lados, `iceServers` só com STUN, e
TURN como reserva desligada (só entra se `CF_TURN_KEY_ID` + `CF_TURN_API_TOKEN` existirem, e
sempre junto com o STUN).

Teste local (cliente Python no próprio asus, `getStats()`):

```
connectionState: connected
PAR NOMINADO: local type=host 192.168.15.17:<porta> | remote type=host 192.168.15.17:<porta>
inbound-rtp: packetsReceived 2771-2897, packetsLost 0
candidatos srflx: 4, todos 179.164.111.8 com a MESMA porta local
```

Bug corrigido no caminho: `RTCPeerConnection(servers)` recebia lista; o aiortc só tolera
lista quando ela é vazia (`[] or RTCConfiguration()`), então com STUN configurado o
`addTrack` quebrava. Agora `RTCConfiguration(iceServers=...)`.

Segfault corrigido: o `finally` fechava o container do `x11grab` enquanto o thread do
executor ainda estava dentro de `next(gen)` — use-after-free confirmado com
`PYTHONFAULTHANDLER=1` (crash em `concurrent/futures/thread.py:58`). O servidor morria a cada
cliente; agora sobrevive a sessões sucessivas.

Fora do escopo agora (anotado, não feito): áudio Opus no WebRTC; keyframe a cada 1–2 s e
4–6 Mbps em vez de 17.

Falta do lado do Matheus: Access self-hosted para `game.matheustoledo.dev` (criado em
2026-10-14; o record CNAME `game.matheustoledo.dev → 18d4763d-f0e7-4447-9799-40bc36858295.cfargotunnel.com`
é consequência da rota, não um record manual), rota do túnel → `http://security_modsecurity:8080`
— o WAF, não o NPM direto: o modsecurity tem `BACKEND=http://infrastructure_npm` e `PORT=8080`,
e está na `public_network` junto com o túnel, então resolve. Reserva de DHCP no roteador Vivo
(MAC `7c:10:c9:ac:4d:a7` → `192.168.15.17`), e o `game.conf` no orange-juice-box (PR #33,
deploy manual depois do merge).

Medido no WAF vivo em 2026-10-14 (paranoia 2, bloqueando, ANOMALY_INBOUND=5): `GET /`,
`GET /?t=<token>` com caracteres `-`/`_` e o upgrade de WebSocket com `Origin` → 200, nenhuma
regra do CRS casou. Depois do `101` os frames do WebSocket não são inspecionados, então o SDP
não passa pelo CRS. O `proxy_read_timeout` do WAF é 36000 s, e o keepalive de 20 s do servidor
renova o timeout em qualquer hop.
