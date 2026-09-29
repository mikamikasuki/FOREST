import { useEffect, useRef, useState } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { RefreshCw, Square, PlugZap } from "lucide-react";
import { IconButton, useUI } from "../ui";
import "@xterm/xterm/css/xterm.css";
export default function LiveTerminal({ projectId }: { projectId: string }) {
  const root = useRef<HTMLDivElement>(null);
  const socket = useRef<WebSocket | null>(null);
  const [session, setSession] = useState(0);
  const [connected, setConnected] = useState(false);
  const { t } = useUI();
  useEffect(() => {
    if (!root.current) return;
    const terminal = new Terminal({
      fontFamily: '"SFMono-Regular", Consolas, monospace',
      fontSize: 12,
      cursorBlink: true,
      theme: {
        background: "#17221d",
        foreground: "#deeadf",
        cursor: "#c1dba8",
      },
      convertEol: true,
    });
    const fit = new FitAddon();
    terminal.loadAddon(fit);
    terminal.open(root.current);
    fit.fit();
    const ws = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/projects/${projectId}/terminal`,
    );
    socket.current = ws;
    ws.onopen = () => {
      setConnected(true);
      terminal.focus();
    };
    ws.onmessage = (e) =>
      terminal.write(typeof e.data === "string" ? e.data : "");
    ws.onerror = () =>
      terminal.writeln("\r\nTerminal connection failed. Check server access.");
    ws.onclose = () => {
      setConnected(false);
      terminal.writeln("\r\n[Disconnected]");
    };
    const sub = terminal.onData((value) => {
      if (ws.readyState === WebSocket.OPEN) ws.send(value);
    });
    const observer = new ResizeObserver(() => fit.fit());
    observer.observe(root.current);
    return () => {
      sub.dispose();
      observer.disconnect();
      ws.close();
      terminal.dispose();
    };
  }, [projectId, session]);
  return (
    <div className="terminal-wrap">
      <div className="terminal-toolbar">
        <span className={`connection ${connected ? "online" : ""}`}>
          <span />
          {connected
            ? t("项目终端已连接", "Project terminal connected")
            : t("终端未连接", "Terminal disconnected")}
        </span>
        <div>
          <IconButton
            label={t("停止当前命令", "Interrupt command")}
            disabled={!connected}
            onClick={() => socket.current?.send("\x03")}
          >
            <Square size={13} />
          </IconButton>
          <IconButton
            label={t("断开连接", "Disconnect")}
            disabled={!connected}
            onClick={() => socket.current?.close()}
          >
            <PlugZap size={14} />
          </IconButton>
          <IconButton
            label={t("重新连接", "Reconnect")}
            onClick={() => setSession((s) => s + 1)}
          >
            <RefreshCw size={14} />
          </IconButton>
        </div>
      </div>
      <div className="terminal-instance" ref={root} />
    </div>
  );
}
