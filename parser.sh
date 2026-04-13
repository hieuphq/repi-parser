#!/usr/bin/env bash
# parser.sh — Bật/tắt repi-parser service
# Usage: ./parser.sh start | stop | restart | status

SERVICE="repi-parser"

case "${1:-}" in
  start)
    echo "▶  Starting $SERVICE..."
    sudo systemctl start $SERVICE
    sleep 1
    systemctl is-active --quiet $SERVICE && echo "✓  Running" || echo "✗  Failed to start"
    ;;
  stop)
    echo "■  Stopping $SERVICE..."
    sudo systemctl stop $SERVICE
    echo "✓  Stopped"
    ;;
  restart)
    echo "↺  Restarting $SERVICE..."
    sudo systemctl restart $SERVICE
    sleep 1
    systemctl is-active --quiet $SERVICE && echo "✓  Running" || echo "✗  Failed to start"
    ;;
  status)
    systemctl status $SERVICE --no-pager
    ;;
  log)
    journalctl -u $SERVICE -f
    ;;
  *)
    echo "Usage: $0 {start|stop|restart|status|log}"
    exit 1
    ;;
esac
