export type WsEventType = 'thinking' | 'tool_call' | 'message' | 'project_update' | 'error';

export interface WsEvent {
  type: WsEventType;
  name?: string;
  args?: string;
  content?: string;
  data?: any;
}

type MessageHandler = (event: WsEvent) => void;

class AriaWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private onMessage: MessageHandler | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(url: string) {
    this.url = url;
  }

  connect(onMessage: MessageHandler) {
    this.onMessage = onMessage;
    this._connect();
  }

  private _connect() {
    try {
      this.ws = new WebSocket(this.url);

      this.ws.onopen = () => {
        console.log('Aria WebSocket connected');
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };

      this.ws.onmessage = (event) => {
        try {
          const data: WsEvent = JSON.parse(event.data);
          this.onMessage?.(data);
        } catch (e) {
          console.error('Failed to parse WS message', e);
        }
      };

      this.ws.onclose = () => {
        console.log('Aria WebSocket disconnected, reconnecting in 3s...');
        this.reconnectTimer = setTimeout(() => this._connect(), 3000);
      };

      this.ws.onerror = (err) => {
        console.error('Aria WebSocket error', err);
      };
    } catch (e) {
      console.error('Failed to create WebSocket', e);
      this.reconnectTimer = setTimeout(() => this._connect(), 3000);
    }
  }

  send(content: string) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ content }));
      return true;
    }
    return false;
  }

  get isConnected() {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  disconnect() {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    this.ws?.close();
    this.ws = null;
  }
}

export const wsService = new AriaWebSocket('ws://localhost:8000/ws');
