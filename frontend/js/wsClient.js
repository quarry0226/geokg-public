/**
 * WSClient - WebSocket client for real-time updates.
 * Connects to the backend and receives dynamic scene updates.
 */
class WSClient {
    constructor() {
        this.ws = null;
        this.onUpdate = null;   // callback(event)
        this.onStatusChange = null; // callback(connected: bool)
        this._reconnectDelay = 2000;
        this._reconnectTimer = null;
    }

    connect() {
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws/updates`;

        try {
            this.ws = new WebSocket(url);

            this.ws.onopen = () => {
                console.log('[WS] Connected');
                this._setStatus(true);
                this._reconnectDelay = 2000;
            };

            this.ws.onmessage = (event) => {
                try {
                    const data = JSON.parse(event.data);
                    if (this.onUpdate) {
                        this.onUpdate(data);
                    }
                } catch (err) {
                    console.error('[WS] Parse error:', err);
                }
            };

            this.ws.onclose = () => {
                console.log('[WS] Disconnected');
                this._setStatus(false);
                this._scheduleReconnect();
            };

            this.ws.onerror = (err) => {
                console.error('[WS] Error:', err);
                this._setStatus(false);
            };
        } catch (err) {
            console.error('[WS] Connection failed:', err);
            this._setStatus(false);
            this._scheduleReconnect();
        }
    }

    _setStatus(connected) {
        const el = document.getElementById('connection-status');
        if (el) {
            el.textContent = connected ? 'Connected' : 'Disconnected';
            el.className = `status-badge ${connected ? 'online' : 'offline'}`;
        }
        if (this.onStatusChange) {
            this.onStatusChange(connected);
        }
    }

    _scheduleReconnect() {
        if (this._reconnectTimer) return;
        this._reconnectTimer = setTimeout(() => {
            this._reconnectTimer = null;
            console.log('[WS] Reconnecting...');
            this.connect();
        }, this._reconnectDelay);
        this._reconnectDelay = Math.min(this._reconnectDelay * 1.5, 30000);
    }

    disconnect() {
        if (this._reconnectTimer) {
            clearTimeout(this._reconnectTimer);
            this._reconnectTimer = null;
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
}
