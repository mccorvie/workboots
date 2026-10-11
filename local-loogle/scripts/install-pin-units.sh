#!/usr/bin/env bash
# Write env files + templated user units for per-pin Loogle, then enable --now each instance.
# usage: install-pin-units.sh [pin:port ...]   (default: both current pins)
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
pins=("$@"); ((${#pins[@]})) || pins=(b2-b5500936b563:8086 b3-11bc13335fb9:8087)
mkdir -p ~/.config/tauceti-loogle ~/.config/systemd/user
for pp in "${pins[@]}"; do
  pin=${pp%%:*}; port=${pp##*:}; root="$HOME/.local/share/tauceti-loogle/$pin"
  cat > ~/.config/tauceti-loogle/$pin.env <<ENV
LOOGLE_PORT=$port
PROJECT_DIR=$root/TauCeti
LOOGLE_BIN=$root/loogle/.lake/build/bin/loogle
INDEX_FILE=$HOME/.local/state/tauceti-loogle/$pin/TauCeti.LoogleAll.loogle-index
MANIFEST=$root/MANIFEST.json
ENV
done
cat > ~/.config/systemd/user/tauceti-loogle@.service <<EOF2
[Unit]
Description=Tau Ceti Loogle service for pin %i
After=network-online.target

[Service]
Type=simple
EnvironmentFile=%h/.config/tauceti-loogle/%i.env
# server.py reads blurb.html and the PNGs from its working directory
WorkingDirectory=%h/.local/share/tauceti-loogle/%i/loogle
Environment=PATH=%h/.elan/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/usr/bin/python3 $here/server.py \\
    --host 127.0.0.1 --port \${LOOGLE_PORT} --workers 2 \\
    --project-dir \${PROJECT_DIR} --loogle-bin \${LOOGLE_BIN} --manifest \${MANIFEST} \\
    -- --module TauCeti.LoogleAll --index-file \${INDEX_FILE} --index-mode read --max-results 50
Restart=on-failure
RestartSec=3
TimeoutStartSec=15min
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=default.target
EOF2
cat > ~/.config/systemd/user/tauceti-loogle-proxy@.service <<'EOF2'
[Unit]
Description=Proxy Tau Ceti Loogle unix socket (pin %i) to loopback HTTP
Requires=tauceti-loogle@%i.service
After=tauceti-loogle@%i.service

[Service]
Type=notify
EnvironmentFile=%h/.config/tauceti-loogle/%i.env
ExecStart=/usr/lib/systemd/systemd-socket-proxyd 127.0.0.1:${LOOGLE_PORT}
NoNewPrivileges=true
PrivateTmp=true
EOF2
cat > ~/.config/systemd/user/tauceti-loogle-proxy@.socket <<'EOF2'
[Unit]
Description=Unix socket for the Tau Ceti Loogle service (pin %i)

[Socket]
ListenStream=%t/tauceti-loogle/%i/loogle.sock
DirectoryMode=0755
SocketMode=0666
RemoveOnStop=yes
Service=tauceti-loogle-proxy@%i.service

[Install]
WantedBy=sockets.target
EOF2
systemctl --user daemon-reload
for pp in "${pins[@]}"; do pin=${pp%%:*}
  systemctl --user enable --now tauceti-loogle@$pin.service tauceti-loogle-proxy@$pin.socket
done
