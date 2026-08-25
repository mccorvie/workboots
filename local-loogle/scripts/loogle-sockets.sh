#!/usr/bin/env bash

cat > ~/.config/systemd/user/tauceti-loogle-proxy.socket <<'EOF'
[Unit]
Description=Unix socket for the Tau Ceti Loogle service

[Socket]
ListenStream=%t/tauceti-loogle/loogle.sock
DirectoryMode=0755
SocketMode=0666
RemoveOnStop=yes
Service=tauceti-loogle-proxy.service

[Install]
WantedBy=sockets.target
EOF

cat > ~/.config/systemd/user/tauceti-loogle-proxy.service <<'EOF'
[Unit]
Description=Proxy Tau Ceti Loogle Unix socket to loopback HTTP
Requires=tauceti-loogle.service
After=tauceti-loogle.service

[Service]
Type=notify
ExecStart=/usr/lib/systemd/systemd-socket-proxyd 127.0.0.1:8088
NoNewPrivileges=true
PrivateTmp=true
EOF

systemctl --user daemon-reload
systemctl --user enable --now tauceti-loogle-proxy.socket