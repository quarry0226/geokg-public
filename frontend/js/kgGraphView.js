/**
 * KGGraphView - Force-directed graph visualization of the Knowledge Graph.
 * Renders nodes and relationships as a network diagram on a canvas overlay.
 */
class KGGraphView {
    constructor(containerId) {
        this.container = document.getElementById(containerId);
        this.canvas = null;
        this.ctx = null;
        this.nodes = [];
        this.links = [];
        this.nodeMap = {};
        this.width = 0;
        this.height = 0;
        this.visible = false;
        this.hoveredNode = null;
        this.selectedNode = null;
        this.onNodeClick = null;  // callback(uid)
        this._dragNode = null;
        this._offsetX = 0;
        this._offsetY = 0;
        this._animFrame = null;
    }

    init() {
        // Create canvas overlay
        this.canvas = document.createElement('canvas');
        this.canvas.id = 'kg-graph-canvas';
        this.canvas.style.cssText = `
            position: absolute; top: 0; left: 0; right: 0; bottom: 0;
            width: 100%; height: 100%; z-index: 50;
            background: rgba(10,14,23,0.92);
            display: none; cursor: default;
        `;
        this.container.style.position = 'relative';
        this.container.appendChild(this.canvas);
        this.ctx = this.canvas.getContext('2d');

        // Events
        this.canvas.addEventListener('mousemove', (e) => this._onMouseMove(e));
        this.canvas.addEventListener('mousedown', (e) => this._onMouseDown(e));
        this.canvas.addEventListener('mouseup', (e) => this._onMouseUp(e));
        this.canvas.addEventListener('click', (e) => this._onClick(e));

        window.addEventListener('resize', () => {
            if (this.visible) this._resize();
        });
    }

    async loadGraph() {
        try {
            const resp = await fetch('/api/geokg/graph?limit=200');
            const data = await resp.json();
            this.nodes = data.nodes.map((n, i) => ({
                ...n,
                x: 0, y: 0,
                vx: 0, vy: 0,
            }));
            this.links = data.links;
            this.nodeMap = {};
            this.nodes.forEach(n => this.nodeMap[n.id] = n);
            this._initPositions();
        } catch (err) {
            console.error('[KGGraphView] Error loading graph:', err);
        }
    }

    toggle() {
        this.visible = !this.visible;
        this.canvas.style.display = this.visible ? 'block' : 'none';
        if (this.visible) {
            this._resize();
            this.loadGraph().then(() => this._startSimulation());
        } else {
            this._stopSimulation();
        }
    }

    _resize() {
        const rect = this.container.getBoundingClientRect();
        this.width = rect.width;
        this.height = rect.height;
        this.canvas.width = this.width * devicePixelRatio;
        this.canvas.height = this.height * devicePixelRatio;
        this.ctx.scale(devicePixelRatio, devicePixelRatio);
    }

    _initPositions() {
        // Group by label, place in circles
        const groups = {};
        this.nodes.forEach(n => {
            if (!groups[n.label]) groups[n.label] = [];
            groups[n.label].push(n);
        });
        const labels = Object.keys(groups);
        const cx = this.width / 2;
        const cy = this.height / 2;
        const groupRadius = Math.min(cx, cy) * 0.6;

        labels.forEach((label, gi) => {
            const angle = (gi / labels.length) * Math.PI * 2;
            const gx = cx + Math.cos(angle) * groupRadius;
            const gy = cy + Math.sin(angle) * groupRadius;
            const nodes = groups[label];
            const r = Math.min(60, nodes.length * 8);
            nodes.forEach((n, ni) => {
                const a = (ni / nodes.length) * Math.PI * 2;
                n.x = gx + Math.cos(a) * r + (Math.random() - 0.5) * 20;
                n.y = gy + Math.sin(a) * r + (Math.random() - 0.5) * 20;
            });
        });
    }

    _startSimulation() {
        let iterations = 0;
        const maxIter = 200;

        const tick = () => {
            if (!this.visible || iterations > maxIter) {
                this._render();
                return;
            }
            this._simulateStep(iterations / maxIter);
            this._render();
            iterations++;
            this._animFrame = requestAnimationFrame(tick);
        };
        tick();
    }

    _stopSimulation() {
        if (this._animFrame) cancelAnimationFrame(this._animFrame);
    }

    _simulateStep(progress) {
        const alpha = 0.3 * (1 - progress);
        if (alpha < 0.001) return;

        // Repulsion (between all nodes)
        for (let i = 0; i < this.nodes.length; i++) {
            for (let j = i + 1; j < this.nodes.length; j++) {
                const a = this.nodes[i];
                const b = this.nodes[j];
                let dx = b.x - a.x;
                let dy = b.y - a.y;
                let dist = Math.sqrt(dx * dx + dy * dy) || 1;
                const force = 800 / (dist * dist);
                const fx = (dx / dist) * force * alpha;
                const fy = (dy / dist) * force * alpha;
                a.vx -= fx; a.vy -= fy;
                b.vx += fx; b.vy += fy;
            }
        }

        // Attraction (along links)
        for (const link of this.links) {
            const a = this.nodeMap[link.source];
            const b = this.nodeMap[link.target];
            if (!a || !b) continue;
            let dx = b.x - a.x;
            let dy = b.y - a.y;
            let dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const force = (dist - 80) * 0.01 * alpha;
            const fx = (dx / dist) * force;
            const fy = (dy / dist) * force;
            a.vx += fx; a.vy += fy;
            b.vx -= fx; b.vy -= fy;
        }

        // Center gravity
        for (const n of this.nodes) {
            n.vx += (this.width / 2 - n.x) * 0.001 * alpha;
            n.vy += (this.height / 2 - n.y) * 0.001 * alpha;
            n.vx *= 0.9;
            n.vy *= 0.9;
            if (n !== this._dragNode) {
                n.x += n.vx;
                n.y += n.vy;
            }
            // Bounds
            n.x = Math.max(20, Math.min(this.width - 20, n.x));
            n.y = Math.max(20, Math.min(this.height - 20, n.y));
        }
    }

    _render() {
        const ctx = this.ctx;
        ctx.clearRect(0, 0, this.width, this.height);

        // Draw links
        for (const link of this.links) {
            const a = this.nodeMap[link.source];
            const b = this.nodeMap[link.target];
            if (!a || !b) continue;

            const relColors = {
                'CONTAINS': '#4fc3f7', 'ADJACENT_TO': '#ff9800',
                'CONNECTED_TO': '#9c27b0', 'MONITORS': '#e91e63',
                'HAS_STATE': '#00bcd4', 'SERVES': '#8bc34a',
                'ON_STREET': '#26c6da', 'FRONTS_ROAD': '#42a5f5',
                'SAME_DONG': '#ffa726', 'SAME_USAGE': '#ab47bc',
                'NEAR': '#66bb6a', 'ON_ROAD': '#29b6f6', 'ALONG': '#ffee58',
                'ON_PARCEL': '#e8d44d',
                'NEAR_BUILDING': '#78909c',
                'NEAREST_SHELTER': '#ef5350',
                'ACCESSIBLE_BY_TRANSIT': '#29b6f6',
                'NEAR_PARK': '#66bb6a',
                'NEAR_FACILITY': '#ffa726',
                'ALONG_ROAD': '#ab47bc',
                'COLOCATED': '#26c6da',
            };
            ctx.strokeStyle = relColors[link.type] || '#666';
            ctx.globalAlpha = 0.55;
            ctx.lineWidth = 1.2;
            ctx.beginPath();
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();

            // Arrow
            const angle = Math.atan2(b.y - a.y, b.x - a.x);
            const midX = (a.x + b.x) / 2;
            const midY = (a.y + b.y) / 2;
            ctx.globalAlpha = 0.65;
            ctx.beginPath();
            ctx.moveTo(midX + Math.cos(angle) * 7, midY + Math.sin(angle) * 7);
            ctx.lineTo(midX + Math.cos(angle + 2.5) * 6, midY + Math.sin(angle + 2.5) * 6);
            ctx.lineTo(midX + Math.cos(angle - 2.5) * 6, midY + Math.sin(angle - 2.5) * 6);
            ctx.closePath();
            ctx.fillStyle = relColors[link.type] || '#444';
            ctx.fill();

            // Label on hover
            if (this.hoveredNode && (this.hoveredNode.id === link.source || this.hoveredNode.id === link.target)) {
                ctx.globalAlpha = 0.8;
                ctx.font = '9px sans-serif';
                ctx.fillStyle = relColors[link.type] || '#aaa';
                ctx.fillText(link.type, midX + 4, midY - 4);
            }
        }
        ctx.globalAlpha = 1;

        // Draw nodes
        const labelColors = {
            Building: '#1565c0', Road: '#546e7a', Vehicle: '#e65100',
            ParkingLot: '#2e7d32', Sensor: '#6a1b9a', Camera: '#c62828',
            Tree: '#33691e', Facility: '#4e342e', Zone: '#37474f',
            TrafficState: '#00838f', EnvironmentState: '#558b2f',
            OccupancyState: '#6d4c41', EquipmentState: '#455a64',
            ParkingSpace: '#43a047',
            Parcel: '#e8d44d',
            ThingsAddr: '#78909c',
        };

        for (const n of this.nodes) {
            const r = n === this.hoveredNode ? 8 : n === this.selectedNode ? 7 : 5;
            const color = labelColors[n.label] || '#808080';

            // Glow for selected/hovered
            if (n === this.hoveredNode || n === this.selectedNode) {
                ctx.shadowColor = color;
                ctx.shadowBlur = 12;
            }

            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
            ctx.fill();

            ctx.shadowBlur = 0;

            // Outline
            ctx.strokeStyle = '#fff';
            ctx.lineWidth = n === this.hoveredNode ? 2 : 0.5;
            ctx.stroke();

            // Label (show for hovered node or if zoomed in)
            if (n === this.hoveredNode || n === this.selectedNode) {
                const name = n.name || n.iot_type_name || n.plate || n.sensor_type || n.uid;
                ctx.font = 'bold 11px sans-serif';
                ctx.fillStyle = '#fff';
                ctx.textAlign = 'center';
                ctx.fillText(name, n.x, n.y - r - 4);
                ctx.font = '9px sans-serif';
                ctx.fillStyle = '#aaa';
                ctx.fillText(n.label, n.x, n.y - r - 16);
            }
        }

        // Legend
        this._drawLegend(ctx, labelColors);
    }

    _drawLegend(ctx, labelColors) {
        const present = new Set(this.nodes.map(n => n.label));
        const items = Object.entries(labelColors).filter(([l]) => present.has(l));
        const x = 15, startY = 15;

        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.fillRect(x - 5, startY - 5, 120, items.length * 18 + 25);

        ctx.font = 'bold 11px sans-serif';
        ctx.fillStyle = '#4fc3f7';
        ctx.textAlign = 'left';
        ctx.fillText('GeoKG Nodes', x, startY + 10);

        items.forEach(([label, color], i) => {
            const y = startY + 25 + i * 18;
            ctx.fillStyle = color;
            ctx.beginPath();
            ctx.arc(x + 6, y, 5, 0, Math.PI * 2);
            ctx.fill();
            ctx.fillStyle = '#ccc';
            ctx.font = '10px sans-serif';
            ctx.fillText(label, x + 16, y + 3);
        });

        // Relationship legend - show only types present in data
        const allRelColors = {
            'CONTAINS': '#4fc3f7', 'MONITORS': '#e91e63',
            'ADJACENT_TO': '#ff9800', 'HAS_STATE': '#00bcd4',
            'CONNECTED_TO': '#9c27b0', 'SERVES': '#8bc34a',
            'ON_STREET': '#26c6da', 'FRONTS_ROAD': '#42a5f5',
            'SAME_DONG': '#ffa726', 'SAME_USAGE': '#ab47bc',
            'NEAR': '#66bb6a', 'ON_ROAD': '#29b6f6', 'ALONG': '#ffee58',
            'ON_PARCEL': '#e8d44d', 'NEAR_BUILDING': '#78909c',
            'NEAREST_SHELTER': '#ef5350', 'ACCESSIBLE_BY_TRANSIT': '#29b6f6',
            'NEAR_PARK': '#66bb6a', 'NEAR_FACILITY': '#ffa726',
            'ALONG_ROAD': '#ab47bc', 'COLOCATED': '#26c6da',
        };
        const presentRelTypes = new Set(this.links.map(l => l.type));
        const relItems = Object.entries(allRelColors).filter(([t]) => presentRelTypes.has(t));
        const rx = this.width - 130;
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.fillRect(rx - 5, startY - 5, 130, relItems.length * 18 + 25);
        ctx.font = 'bold 11px sans-serif';
        ctx.fillStyle = '#4fc3f7';
        ctx.fillText('Relationships', rx, startY + 10);

        relItems.forEach(([type, color], i) => {
            const y = startY + 25 + i * 18;
            ctx.strokeStyle = color;
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.moveTo(rx, y);
            ctx.lineTo(rx + 12, y);
            ctx.stroke();
            ctx.fillStyle = '#ccc';
            ctx.font = '10px sans-serif';
            ctx.fillText(type, rx + 18, y + 3);
        });

        // Stats
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.fillRect(this.width / 2 - 80, this.height - 35, 160, 25);
        ctx.font = '11px sans-serif';
        ctx.fillStyle = '#4fc3f7';
        ctx.textAlign = 'center';
        ctx.fillText(
            `${this.nodes.length} nodes \u2022 ${this.links.length} relationships`,
            this.width / 2, this.height - 18
        );
        ctx.textAlign = 'left';
    }

    _findNode(mx, my) {
        for (const n of this.nodes) {
            const dx = n.x - mx;
            const dy = n.y - my;
            if (dx * dx + dy * dy < 100) return n;
        }
        return null;
    }

    _onMouseMove(e) {
        const rect = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;

        if (this._dragNode) {
            this._dragNode.x = mx;
            this._dragNode.y = my;
            this._render();
            return;
        }

        const prev = this.hoveredNode;
        this.hoveredNode = this._findNode(mx, my);
        if (this.hoveredNode !== prev) {
            this.canvas.style.cursor = this.hoveredNode ? 'pointer' : 'default';
            this._render();
        }
    }

    _onMouseDown(e) {
        const rect = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        this._dragNode = this._findNode(mx, my);
    }

    _onMouseUp() {
        this._dragNode = null;
    }

    _onClick(e) {
        const rect = this.canvas.getBoundingClientRect();
        const mx = e.clientX - rect.left;
        const my = e.clientY - rect.top;
        const node = this._findNode(mx, my);
        if (node) {
            this.selectedNode = node;
            this._render();
            if (this.onNodeClick) this.onNodeClick(node.id);
        }
    }
}
