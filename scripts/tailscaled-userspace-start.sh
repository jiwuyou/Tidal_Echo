#!/bin/sh
set -eu

mkdir -p /var/lib/tailscale /var/run/tailscale
exec /usr/sbin/tailscaled \
  --tun=userspace-networking \
  --state=/var/lib/tailscale/tailscaled.state \
  --socket=/var/run/tailscale/tailscaled.sock
