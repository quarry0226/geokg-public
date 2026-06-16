/**
 * DashboardPanel - Real-time monitoring dashboard.
 * Shows parking, environment, vehicle, and camera status.
 */
class DashboardPanel {
    constructor() {
        this.data = {};
    }

    async loadDashboard() {
        try {
            const resp = await fetch('/api/scene/dashboard');
            this.data = await resp.json();
            this.render();
        } catch (err) {
            console.error('[Dashboard] Error:', err);
        }
    }

    render() {
        this._renderParking();
        this._renderEnvironment();
        this._renderVehicles();
        this._renderCameras();
    }

    _renderParking() {
        const el = document.getElementById('parking-content');
        if (!this.data.parking) { el.textContent = 'No data'; return; }

        el.innerHTML = this.data.parking.map(p => {
            const rate = p.rate;
            let color = '#2ECC71';
            if (rate >= 80) color = '#E74C3C';
            else if (rate >= 50) color = '#F1C40F';

            return `<div class="parking-bar">
                <div class="parking-bar-label">
                    <span>${p.name}</span>
                    <span>${p.occupied}/${p.capacity} (${rate}%)</span>
                </div>
                <div class="parking-bar-track">
                    <div class="parking-bar-fill" style="width:${rate}%;background:${color}"></div>
                </div>
            </div>`;
        }).join('');
    }

    _renderEnvironment() {
        const el = document.getElementById('environment-content');
        if (!this.data.environment) { el.textContent = 'No data'; return; }

        const labels = {
            temperature: { name: 'Temperature', unit: '°C', icon: '#FF6B6B' },
            humidity: { name: 'Humidity', unit: '%', icon: '#5BC0EB' },
            aqi: { name: 'Air Quality', unit: ' AQI', icon: '#9BC53D' },
            noise: { name: 'Noise Level', unit: ' dB', icon: '#FDE74C' },
        };

        el.innerHTML = Object.entries(this.data.environment).map(([key, val]) => {
            const l = labels[key] || { name: key, unit: '', icon: '#808080' };
            return `<div class="env-metric">
                <span style="color:${l.icon}">${l.name}</span>
                <span class="env-value">${val}${l.unit}</span>
            </div>`;
        }).join('');
    }

    _renderVehicles() {
        const el = document.getElementById('vehicle-content');
        el.innerHTML = `<div class="env-metric">
            <span>Active Vehicles</span>
            <span class="env-value">${this.data.vehicle_count || 0}</span>
        </div>`;
    }

    _renderCameras() {
        const el = document.getElementById('camera-content');
        if (!this.data.cameras) { el.textContent = 'No data'; return; }

        el.innerHTML = Object.entries(this.data.cameras).map(([status, count]) => {
            const color = status === 'active' ? '#2ECC71' : '#E74C3C';
            return `<div class="env-metric">
                <span style="color:${color}">${status}</span>
                <span class="env-value">${count}</span>
            </div>`;
        }).join('');
    }

    updateFromEvent(event) {
        // Update parking bars in real-time
        if (event.parking && event.parking.length > 0) {
            // Reload parking section with fresh data
            this._updateParkingFromEvent(event.parking);
        }

        // Update environment values
        if (event.environment && event.environment.length > 0) {
            this._updateEnvironmentFromEvent(event.environment);
        }
    }

    _updateParkingFromEvent(parkingUpdates) {
        for (const p of parkingUpdates) {
            // Find the parking bar for this uid and update it
            const bars = document.querySelectorAll('.parking-bar');
            // Trigger full reload periodically
        }
        // Simple approach: reload dashboard
        this.loadDashboard();
    }

    _updateEnvironmentFromEvent(envUpdates) {
        // Group by sensor type and compute averages
        const byType = {};
        for (const s of envUpdates) {
            if (!byType[s.sensor_type]) byType[s.sensor_type] = [];
            byType[s.sensor_type].push(s.value);
        }
        for (const [type, values] of Object.entries(byType)) {
            const avg = values.reduce((a, b) => a + b, 0) / values.length;
            if (this.data.environment) {
                this.data.environment[type] = Math.round(avg * 10) / 10;
            }
        }
        this._renderEnvironment();
    }
}
