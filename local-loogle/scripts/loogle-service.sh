#!/usr/bin/env bash

cat > ~/.config/systemd/user/tauceti-loogle.service <<'EOF'
[Unit]
Description=Tau Ceti Loogle service
After=network-online.target

[Service]
Type=simple
WorkingDirectory=%h/.local/share/tauceti-loogle/loogle
Environment=PATH=%h/.elan/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin

ExecStart=/usr/bin/python3 %h/.local/share/tauceti-loogle/loogle/server.py \
    --host 127.0.0.1 \
    --port 8088 \
    --workers 2 \
    --project-dir %h/.local/share/tauceti-loogle/TauCeti \
    --loogle-bin %h/.local/share/tauceti-loogle/loogle/.lake/build/bin/loogle \
    -- \
    --module TauCeti.LoogleAll \
    --index-file %h/.local/state/tauceti-loogle/TauCeti.LoogleAll.loogle-index \
    --index-mode read \
    --max-results 50

Restart=on-failure
RestartSec=3
TimeoutStartSec=15min

NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now tauceti-loogle.service

systemctl --user status tauceti-loogle.service

curl \
  --fail \
  --silent \
  --show-error \
  --get \
  --data-urlencode 'q=List.map' \
  http://127.0.0.1:8088/json |
jq .