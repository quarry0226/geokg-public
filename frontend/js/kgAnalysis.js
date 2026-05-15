/**
 * KGAnalysis - Interactive Knowledge Graph analysis panel.
 * Demonstrates what KG can DO: path finding, impact analysis, anomaly trace, etc.
 * All entity references use GSID as the primary identifier.
 */
class KGAnalysis {
    constructor(cesiumManager) {
        this.cm = cesiumManager;
        this.modal = document.getElementById('kg-analysis-modal');
        this.highlightEntities = []; // track highlighted entities for cleanup
        this._pickTarget = null;     // 'from' | 'to' | null — active pick mode
    }

    /** Format entity display as "Name (GSID)" or just GSID if no name. */
    _entityLabel(n) {
        const gsid = n.gsid || '';
        const name = n.name || '';
        if (name && gsid) return `${name} <span class="entity-gsid-inline">(${gsid})</span>`;
        if (gsid) return `<span class="entity-gsid-inline">${gsid}</span>`;
        return name || n.uid || '?';
    }

    /** Short GSID display for compact contexts. */
    _gsidShort(n) {
        return n.gsid || n.uid || '?';
    }

    init() {
        // Modal open/close
        document.getElementById('btn-kg-analysis').addEventListener('click', () => this.open());
        document.getElementById('modal-close').addEventListener('click', () => this.close());

        // Draggable modal header
        this._initDraggable();

        // Tab switching
        this.modal.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                this.modal.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                this.modal.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
                btn.classList.add('active');
                document.getElementById(btn.dataset.tab).classList.add('active');
            });
        });

        // Action buttons
        document.getElementById('btn-find-path').addEventListener('click', () => this.findPath());
        document.getElementById('btn-impact').addEventListener('click', () => this.impactAnalysis());
        document.getElementById('btn-nearby').addEventListener('click', () => this.findNearby());
        document.getElementById('btn-anomaly').addEventListener('click', () => this.detectAnomalies());
        document.getElementById('btn-coverage').addEventListener('click', () => this.coverageAnalysis());
        document.getElementById('btn-road-impact').addEventListener('click', () => this.roadImpact());
        document.getElementById('btn-safety').addEventListener('click', () => this.safetyProfile());
        document.getElementById('btn-analytics').addEventListener('click', () => this.runAnalytics());
        document.getElementById('btn-cypher').addEventListener('click', () => this.runCypher());
        document.getElementById('btn-road-profile-load').addEventListener('click', () => this.loadRoadProfile());
        document.getElementById('btn-road-profile-map').addEventListener('click', () => this.showRoadOnMap());
        document.getElementById('btn-rp-geocode').addEventListener('click', () => this.roadGeocode());

        // Allow Enter key in text inputs
        ['path-from', 'path-to', 'impact-uid', 'nearby-uid', 'safety-uid'].forEach(id => {
            document.getElementById(id).addEventListener('keydown', (e) => {
                if (e.key === 'Enter') e.target.closest('.tab-panel').querySelector('.btn-accent').click();
            });
        });
        document.getElementById('cypher-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && e.ctrlKey) document.getElementById('btn-cypher').click();
        });

        // Analytics mode switching
        document.getElementById('analytics-mode').addEventListener('change', (e) => {
            const isDong = e.target.value === 'dong';
            document.getElementById('analytics-dong-controls').style.display = isDong ? '' : 'none';
            document.getElementById('analytics-conn-controls').style.display = isDong ? 'none' : '';
        });
        document.getElementById('analytics-conn-type').addEventListener('change', (e) => {
            document.getElementById('analytics-max-deg-wrap').style.display = e.target.value === 'isolated' ? '' : 'none';
        });

        // Enter key for geocode inputs
        ['rp-geocode-road', 'rp-geocode-num'].forEach(id => {
            document.getElementById(id).addEventListener('keydown', (e) => {
                if (e.key === 'Enter') document.getElementById('btn-rp-geocode').click();
            });
        });

        // Pick-from-map buttons
        document.getElementById('btn-pick-from').addEventListener('click', () => this._togglePick('from'));
        document.getElementById('btn-pick-to').addEventListener('click', () => this._togglePick('to'));

        // Resizable modal
        this._initResizable();

        // Load road list and dong list for dropdowns
        this._loadRoads();
        this._loadDongList();
        this._loadRoadProfiles();
    }

    async _loadRoads() {
        try {
            const resp = await fetch('/api/kg/roads');
            if (!resp.ok) return;
            const data = await resp.json();
            const sel = document.getElementById('road-select');
            for (const r of data.roads) {
                const opt = document.createElement('option');
                opt.value = r.uid;
                opt.textContent = r.name || r.uid;
                sel.appendChild(opt);
            }
        } catch (e) { /* ignore */ }
    }

    async _loadDongList() {
        try {
            const resp = await fetch('/api/kg/cypher', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query: "MATCH (b:Building) WHERE b.admin_dong_name IS NOT NULL RETURN DISTINCT b.admin_dong_name AS dong ORDER BY dong" }),
            });
            if (!resp.ok) return;
            const data = await resp.json();
            const sel = document.getElementById('coverage-dong');
            for (const r of data.results) {
                const opt = document.createElement('option');
                opt.value = r.dong;
                opt.textContent = r.dong;
                sel.appendChild(opt);
            }
        } catch (e) { /* ignore */ }
    }

    open() {
        this.modal.classList.remove('hidden');
        // Reset position to center when opened
        const content = this.modal.querySelector('.modal-content');
        content.style.transform = '';
        this._dragOffset = { x: 0, y: 0 };
    }
    close() { this.modal.classList.add('hidden'); }

    _initDraggable() {
        const header = this.modal.querySelector('.modal-header');
        const content = this.modal.querySelector('.modal-content');
        let isDragging = false;
        let startX, startY;
        this._dragOffset = { x: 0, y: 0 };

        header.addEventListener('mousedown', (e) => {
            // Don't drag if clicking close button
            if (e.target.closest('.btn-icon')) return;
            isDragging = true;
            startX = e.clientX - this._dragOffset.x;
            startY = e.clientY - this._dragOffset.y;
            e.preventDefault();
        });

        document.addEventListener('mousemove', (e) => {
            if (!isDragging) return;
            this._dragOffset.x = e.clientX - startX;
            this._dragOffset.y = e.clientY - startY;
            content.style.transform = `translate(${this._dragOffset.x}px, ${this._dragOffset.y}px)`;
        });

        document.addEventListener('mouseup', () => {
            isDragging = false;
        });
    }

    _initResizable() {
        const handle = this.modal.querySelector('.modal-resize-handle');
        if (!handle) return;
        const content = this.modal.querySelector('.modal-content');
        let isResizing = false, startY, startHeight;

        handle.addEventListener('mousedown', (e) => {
            isResizing = true;
            startY = e.clientY;
            startHeight = content.offsetHeight;
            e.preventDefault();
            e.stopPropagation();
        });

        document.addEventListener('mousemove', (e) => {
            if (!isResizing) return;
            const newH = Math.max(300, Math.min(window.innerHeight * 0.95, startHeight + (e.clientY - startY)));
            content.style.maxHeight = newH + 'px';
            content.style.height = newH + 'px';
        });

        document.addEventListener('mouseup', () => {
            isResizing = false;
        });
    }

    // ── Path Finder ──────────────────────────────────────────
    async findPath() {
        const from = document.getElementById('path-from').value.trim();
        const to = document.getElementById('path-to').value.trim();
        const mode = document.getElementById('path-mode').value;
        const resultDiv = document.getElementById('path-result');
        if (!from || !to) { resultDiv.innerHTML = '<p class="error">Please enter both entity GSIDs.</p>'; return; }

        resultDiv.innerHTML = '<p class="loading">Searching...</p>';
        try {
            const resp = await fetch(`/api/kg/path?from_uid=${encodeURIComponent(from)}&to_uid=${encodeURIComponent(to)}&mode=${mode}`);
            if (!resp.ok) { const e = await resp.json(); throw new Error(e.detail || resp.statusText); }
            const data = await resp.json();

            const modeLabel = data.mode === 'road_network' ? '🛣️ 도로 네트워크' :
                             data.mode === 'spatial' ? '🌐 Spatial Path' : '🔗 All Relations';

            let html;
            if (data.mode === 'road_network' && data.road_path) {
                html = this._renderRoadNetworkDisplay(data, from, to, modeLabel);
            } else {
                html = this._renderKGPathDisplay(data, from, to, modeLabel);
            }
            resultDiv.innerHTML = html;

            // Click node to fly to it
            resultDiv.querySelectorAll('.path-node').forEach(el => {
                el.addEventListener('click', () => this.cm.highlightEntity(el.dataset.uid));
            });
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    /** Render KG-based path display (spatial / all modes). */
    _renderKGPathDisplay(data, from, to, modeLabel) {
        let html = `<div class="path-result">
            <div class="path-summary">${data.hops} hop${data.hops > 1 ? 's' : ''} <small style="color:#78909c;margin-left:8px;">${modeLabel}</small></div>
            <div class="path-chain">`;
        for (let i = 0; i < data.nodes.length; i++) {
            const n = data.nodes[i];
            html += `<span class="path-node label-${n.label}" data-uid="${n.uid}" title="${n.gsid || n.uid}">${this._entityLabel(n)}</span>`;
            if (i < data.relationships.length) {
                const r = data.relationships[i];
                html += `<span class="path-arrow">&xrarr; <small>${r.type}</small> &xrarr;</span>`;
            }
        }
        html += `</div>
            <button class="btn btn-sm" onclick="kgAnalysis.highlightPath('${from}','${to}')">Highlight on Map</button>
        </div>`;
        return html;
    }

    /** Render physical road network path display with dashed connectors. */
    _renderRoadNetworkDisplay(data, from, to, modeLabel) {
        const roadHops = data.road_path.length > 1 ? data.road_path.length - 1 : 0;
        let html = `<div class="path-result">
            <div class="path-summary">${modeLabel}
                <small style="color:#78909c;margin-left:8px;">도로 ${roadHops}구간</small></div>
            <div class="path-chain">`;

        // Start entity + dashed connector to road
        const fe = data.from_entity;
        html += `<span class="path-node label-${fe.label}" data-uid="${fe.uid}" title="${fe.gsid || fe.uid}">${this._entityLabel(fe)}</span>`;
        if (data.from_road) {
            html += `<span class="path-arrow path-dashed">┄┄ 수직접속 ┄┄→</span>`;
        }

        // Road network chain
        for (let i = 0; i < data.road_path.length; i++) {
            const n = data.road_path[i];
            html += `<span class="path-node label-${n.label}" data-uid="${n.uid}" title="${n.gsid || n.uid}">${this._entityLabel(n)}</span>`;
            if (i < data.relationships.length) {
                const r = data.relationships[i];
                html += `<span class="path-arrow">&xrarr; <small>${r.type}</small> &xrarr;</span>`;
            }
        }

        // Dashed connector + end entity
        const te = data.to_entity;
        if (data.to_road) {
            html += `<span class="path-arrow path-dashed">┄┄ 수직접속 ┄┄→</span>`;
        }
        html += `<span class="path-node label-${te.label}" data-uid="${te.uid}" title="${te.gsid || te.uid}">${this._entityLabel(te)}</span>`;

        html += `</div>
            <button class="btn btn-sm" onclick="kgAnalysis.highlightPath('${from}','${to}')">Highlight on Map</button>
        </div>`;
        return html;
    }

    highlightPath(from, to, mode) {
        this.clearHighlights();
        const modeParam = mode || document.getElementById('path-mode')?.value || 'road_network';
        fetch(`/api/kg/path?from_uid=${from}&to_uid=${to}&mode=${modeParam}`)
            .then(r => r.json())
            .then(data => {
                if (data.mode === 'road_network' && data.road_path) {
                    this._highlightRoadNetwork(data);
                } else {
                    this._highlightKGPath(data);
                }
            });
    }

    /** Highlight KG-based path (spatial / all modes). */
    _highlightKGPath(data) {
        const nodes = data.nodes;
        const routePositions = [];

        for (let i = 0; i < nodes.length; i++) {
            const n = nodes[i];
            const prevNode = i > 0 ? nodes[i - 1] : null;
            const nextNode = i < nodes.length - 1 ? nodes[i + 1] : null;

            if (n.label === 'Road') {
                const coords = this.cm.roadPolylines?.[n.uid];
                if (coords && coords.length >= 2) {
                    const startRef = prevNode ? this._getRefPosition(prevNode) : null;
                    const endRef = nextNode ? this._getRefPosition(nextNode) : null;
                    const trimmed = this._trimRoadSegment(coords, startRef, endRef);
                    for (const c of trimmed) routePositions.push(Cesium.Cartesian3.fromDegrees(c[0], c[1], 3));
                } else {
                    const pos = this.cm.entityPositions[n.uid];
                    if (pos) routePositions.push(pos);
                }
            } else {
                const prevHasRoad = prevNode?.label === 'Road' && this.cm.roadPolylines?.[prevNode.uid]?.length >= 2;
                const nextHasRoad = nextNode?.label === 'Road' && this.cm.roadPolylines?.[nextNode.uid]?.length >= 2;
                if (!prevHasRoad && !nextHasRoad) {
                    const pos = this.cm.entityPositions[n.uid];
                    if (pos) routePositions.push(pos);
                }
            }
        }

        this._drawRoutePolyline(routePositions);
        this._addEndpointMarkers(nodes[0], nodes[nodes.length - 1]);
        this._flyToRoute(routePositions);
    }

    /** Highlight physical road network path with dashed perpendicular connectors. */
    _highlightRoadNetwork(data) {
        const { from_entity, to_entity, from_road, to_road, road_path } = data;
        const allPositions = []; // for camera bounding

        // ── 1. Build solid road route positions ──
        const routePositions = [];
        for (let i = 0; i < road_path.length; i++) {
            const n = road_path[i];
            const prevNode = i > 0 ? road_path[i - 1] : null;
            const nextNode = i < road_path.length - 1 ? road_path[i + 1] : null;

            if (n.label === 'Road') {
                const coords = this.cm.roadPolylines?.[n.uid];
                if (coords && coords.length >= 2) {
                    // For first road: trim start toward start entity position
                    let startRef = prevNode ? this._getRefPosition(prevNode) : null;
                    if (!startRef && i === 0 && from_road) {
                        startRef = this._getEntityRef(from_entity.uid);
                    }
                    // For last road: trim end toward end entity position
                    let endRef = nextNode ? this._getRefPosition(nextNode) : null;
                    if (!endRef && i === road_path.length - 1 && to_road) {
                        endRef = this._getEntityRef(to_entity.uid);
                    }
                    const trimmed = this._trimRoadSegment(coords, startRef, endRef);
                    for (const c of trimmed) routePositions.push(Cesium.Cartesian3.fromDegrees(c[0], c[1], 3));
                } else {
                    const pos = this.cm.entityPositions[n.uid];
                    if (pos) routePositions.push(pos);
                }
            } else {
                // RoadIntersection: skip if adjacent roads handle it via trim
                const prevIsRoad = prevNode?.label === 'Road' && this.cm.roadPolylines?.[prevNode.uid]?.length >= 2;
                const nextIsRoad = nextNode?.label === 'Road' && this.cm.roadPolylines?.[nextNode.uid]?.length >= 2;
                if (!prevIsRoad && !nextIsRoad) {
                    const pos = this.cm.entityPositions[n.uid];
                    if (pos) routePositions.push(pos);
                }
            }
        }

        // Draw solid yellow route
        this._drawRoutePolyline(routePositions);
        allPositions.push(...routePositions);

        // ── 2. Draw dashed perpendicular connectors ──
        for (const { entityUid, roadUid, isStart } of [
            { entityUid: from_entity.uid, roadUid: from_road, isStart: true },
            { entityUid: to_entity.uid, roadUid: to_road, isStart: false },
        ]) {
            if (!roadUid) continue; // entity is already on the road network
            const entityRef = this._getEntityRef(entityUid);
            const roadCoords = this.cm.roadPolylines?.[roadUid];
            if (!entityRef || !roadCoords || roadCoords.length < 2) continue;

            const foot = this._projectPointOntoRoad(roadCoords, entityRef);
            if (!foot) continue;

            const entityGround = Cesium.Cartesian3.fromDegrees(entityRef[0], entityRef[1], 3);
            const footPos = Cesium.Cartesian3.fromDegrees(foot[0], foot[1], 3);

            const color = isStart
                ? Cesium.Color.fromCssColorString('#4CAF50').withAlpha(0.9)
                : Cesium.Color.fromCssColorString('#F44336').withAlpha(0.9);

            const dashLine = this.cm.viewer.entities.add({
                polyline: {
                    positions: [entityGround, footPos],
                    width: 5,
                    material: new Cesium.PolylineDashMaterialProperty({
                        color: color,
                        dashLength: 12,
                    }),
                    clampToGround: true,
                },
                _isHighlight: true,
            });
            this.highlightEntities.push(dashLine);
            allPositions.push(entityGround);
        }

        // ── 3. Add endpoint markers + building highlights ──
        this._addEndpointMarkers(from_entity, to_entity);

        // ── 4. Fly camera ──
        this._flyToRoute(allPositions.length > 1 ? allPositions : routePositions);
    }

    // ── Shared helpers for both modes ──

    _drawRoutePolyline(positions) {
        if (positions.length < 2) return;
        const routeLine = this.cm.viewer.entities.add({
            polyline: {
                positions,
                width: 8,
                material: new Cesium.PolylineGlowMaterialProperty({
                    glowPower: 0.25,
                    color: Cesium.Color.fromCssColorString('#FFD600'),
                }),
                clampToGround: true,
            },
            _isHighlight: true,
        });
        this.highlightEntities.push(routeLine);
    }

    _addEndpointMarkers(startNode, endNode) {
        for (const n of [startNode, endNode]) {
            const pos = this.cm.entityPositions[n.uid];
            if (!pos) continue;
            const isStart = n === startNode;
            const marker = this.cm.viewer.entities.add({
                position: pos,
                billboard: {
                    image: this._createMarkerCanvas(isStart ? '출발' : '도착', isStart ? '#4CAF50' : '#F44336'),
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                    scale: 0.7,
                    disableDepthTestDistance: Number.POSITIVE_INFINITY,
                },
                _isHighlight: true,
            });
            this.highlightEntities.push(marker);

            // Highlight building with color change
            const buildingEntity = this.cm.entities[n.uid];
            if (buildingEntity && buildingEntity.polygon) {
                const origMaterial = buildingEntity.polygon.material;
                this._savedMaterials = this._savedMaterials || [];
                this._savedMaterials.push({ entity: buildingEntity, material: origMaterial });
                const hlColor = isStart
                    ? Cesium.Color.fromCssColorString('#4CAF50').withAlpha(0.95)
                    : Cesium.Color.fromCssColorString('#F44336').withAlpha(0.95);
                buildingEntity.polygon.material = hlColor;
                buildingEntity.polygon.outlineColor = Cesium.Color.WHITE;
            }
        }
    }

    _flyToRoute(positions) {
        if (positions.length < 2) return;
        this.cm.viewer.camera.flyToBoundingSphere(
            Cesium.BoundingSphere.fromPoints(positions),
            { duration: 1.5, offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-50), 0) }
        );
    }

    /** Get lon/lat for an entity uid (from entityPositions). */
    _getEntityRef(uid) {
        const pos = this.cm.entityPositions[uid];
        if (!pos) return null;
        const carto = Cesium.Cartographic.fromCartesian(pos);
        return [Cesium.Math.toDegrees(carto.longitude), Cesium.Math.toDegrees(carto.latitude)];
    }

    /** Project a [lon,lat] point onto a road polyline, returning the foot [lon,lat]. */
    _projectPointOntoRoad(coords, ref) {
        if (!ref || !coords || coords.length < 2) return null;
        let minD = Infinity, bestPoint = null;
        for (let i = 0; i < coords.length - 1; i++) {
            const ax = coords[i][0], ay = coords[i][1];
            const bx = coords[i + 1][0], by = coords[i + 1][1];
            const dx = bx - ax, dy = by - ay;
            const lenSq = dx * dx + dy * dy;
            if (lenSq < 1e-14) continue;
            let t = ((ref[0] - ax) * dx + (ref[1] - ay) * dy) / lenSq;
            t = Math.max(0, Math.min(1, t));
            const px = ax + t * dx, py = ay + t * dy;
            const d = (px - ref[0]) ** 2 + (py - ref[1]) ** 2;
            if (d < minD) { minD = d; bestPoint = [px, py]; }
        }
        return bestPoint;
    }

    /** Get lon/lat reference for a node (for trimming road segments) */
    _getRefPosition(node) {
        const pos = this.cm.entityPositions[node.uid];
        if (!pos) return null;
        const carto = Cesium.Cartographic.fromCartesian(pos);
        return [Cesium.Math.toDegrees(carto.longitude), Cesium.Math.toDegrees(carto.latitude)];
    }

    /** Trim road polyline between two reference points using perpendicular projection
     *  for smooth building↔road connection (no zigzag). */
    _trimRoadSegment(coords, startRef, endRef) {
        if (!startRef && !endRef) return coords;

        // Project a point onto the nearest position on any road segment
        const projectOntoRoad = (ref) => {
            if (!ref) return null;
            let minD = Infinity, bestSeg = 0, bestPoint = coords[0];
            for (let i = 0; i < coords.length - 1; i++) {
                const ax = coords[i][0], ay = coords[i][1];
                const bx = coords[i + 1][0], by = coords[i + 1][1];
                const dx = bx - ax, dy = by - ay;
                const lenSq = dx * dx + dy * dy;
                if (lenSq < 1e-14) continue;
                let t = ((ref[0] - ax) * dx + (ref[1] - ay) * dy) / lenSq;
                t = Math.max(0, Math.min(1, t));
                const px = ax + t * dx, py = ay + t * dy;
                const d = (px - ref[0]) ** 2 + (py - ref[1]) ** 2;
                if (d < minD) { minD = d; bestSeg = i; bestPoint = [px, py]; }
            }
            return { seg: bestSeg, point: bestPoint };
        };

        const sp = startRef ? projectOntoRoad(startRef) : null;
        const ep = endRef ? projectOntoRoad(endRef) : null;

        // First vertex after start projection / last vertex before end projection
        const fromV = sp ? sp.seg + 1 : 0;
        const toV = ep ? ep.seg : coords.length - 1;

        const result = [];
        if (fromV <= toV) {
            // Forward direction along polyline
            if (sp) result.push(sp.point);
            for (let i = fromV; i <= toV; i++) result.push(coords[i]);
            if (ep) result.push(ep.point);
        } else {
            // Reverse direction (start is after end along polyline)
            if (sp) result.push(sp.point);
            for (let i = fromV - 1; i >= toV + 1; i--) result.push(coords[i]);
            if (ep) result.push(ep.point);
        }

        return result.length >= 2 ? result : coords;
    }

    /** Create a simple start/end marker canvas */
    _createMarkerCanvas(text, bgColor) {
        const canvas = document.createElement('canvas');
        canvas.width = 80; canvas.height = 40;
        const ctx = canvas.getContext('2d');
        // Pin shape
        ctx.fillStyle = bgColor;
        ctx.beginPath();
        ctx.roundRect(0, 0, 80, 30, 6);
        ctx.fill();
        // Triangle
        ctx.beginPath();
        ctx.moveTo(30, 30); ctx.lineTo(40, 40); ctx.lineTo(50, 30);
        ctx.fill();
        // Text
        ctx.fillStyle = '#fff';
        ctx.font = 'bold 14px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(text, 40, 21);
        return canvas;
    }

    // ── Map entity picker for Path Finder ────────────────────
    _togglePick(target) {
        const fromBtn = document.getElementById('btn-pick-from');
        const toBtn = document.getElementById('btn-pick-to');

        if (this._pickTarget === target) {
            // Cancel pick mode
            this._pickTarget = null;
            fromBtn.classList.remove('picking');
            toBtn.classList.remove('picking');
            this.cm.viewer.canvas.style.cursor = '';
            return;
        }

        this._pickTarget = target;
        fromBtn.classList.toggle('picking', target === 'from');
        toBtn.classList.toggle('picking', target === 'to');
        this.cm.viewer.canvas.style.cursor = 'crosshair';
    }

    /** Called from app.js onEntityClick when pick mode is active.
     *  Returns true if pick was consumed (so normal click logic can be skipped). */
    onEntityPick(uid, label) {
        if (!this._pickTarget) return false;

        // Look up the entity's GSID from the uid→gsid map, or fall back to uid
        const gsid = this.cm.uidToGsid[uid] || uid;

        const inputId = this._pickTarget === 'from' ? 'path-from' : 'path-to';
        document.getElementById(inputId).value = gsid;

        // Flash the input to confirm
        const input = document.getElementById(inputId);
        input.style.transition = 'border-color 0.3s';
        input.style.borderColor = '#4fc3f7';
        setTimeout(() => { input.style.borderColor = ''; }, 800);

        // Exit pick mode
        const fromBtn = document.getElementById('btn-pick-from');
        const toBtn = document.getElementById('btn-pick-to');
        this._pickTarget = null;
        fromBtn.classList.remove('picking');
        toBtn.classList.remove('picking');
        this.cm.viewer.canvas.style.cursor = '';
        return true;
    }

    clearHighlights() {
        for (const e of this.highlightEntities) {
            this.cm.viewer.entities.remove(e);
        }
        this.highlightEntities = [];
        // Restore original building materials
        if (this._savedMaterials) {
            for (const s of this._savedMaterials) {
                if (s.entity.polygon) {
                    s.entity.polygon.material = s.material;
                    s.entity.polygon.outlineColor = Cesium.Color.fromCssColorString('#222').withAlpha(0.8);
                }
            }
            this._savedMaterials = [];
        }
    }

    // ── Impact Analysis (with typed filtering) ───────────────
    async impactAnalysis() {
        const uid = document.getElementById('impact-uid').value.trim();
        const depth = document.getElementById('impact-depth').value;
        const impactType = document.getElementById('impact-type').value;
        const resultDiv = document.getElementById('impact-result');
        if (!uid) { resultDiv.innerHTML = '<p class="error">엔티티 GSID를 입력하세요.</p>'; return; }

        resultDiv.innerHTML = '<p class="loading">영향 분석 중...</p>';
        try {
            const resp = await fetch(`/api/kg/impact?uid=${encodeURIComponent(uid)}&depth=${depth}&impact_type=${impactType}`);
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            const data = await resp.json();

            const typeLabels = { all: '전체', infrastructure: '🏗️ 인프라', safety: '🛡️ 안전', administrative: '🏛️ 행정' };
            let html = `<div class="impact-summary">
                <strong>${data.total_affected}</strong>개 엔티티 영향 <small style="color:#78909c">${typeLabels[data.impact_type] || '전체'} 모드</small>
            </div>`;

            // Entity type breakdown
            if (data.by_label && Object.keys(data.by_label).length > 0) {
                html += `<div class="impact-type-breakdown">`;
                for (const [lbl, cnt] of Object.entries(data.by_label)) {
                    html += `<span class="label-badge label-${lbl}">${lbl}: ${cnt}</span> `;
                }
                html += `</div>`;
            }

            // Render concentric layers
            for (const [dist, entities] of Object.entries(data.layers)) {
                const colors = ['#FFD700', '#FF8C00', '#FF4500', '#DC143C', '#8B0000'];
                const color = colors[Math.min(parseInt(dist) - 1, colors.length - 1)];
                html += `<div class="impact-layer">
                    <div class="layer-header" style="border-left:4px solid ${color}">
                        Hop ${dist} (${entities.length}개)
                    </div>
                    <div class="layer-entities">`;
                for (const e of entities.slice(0, 50)) {
                    html += `<span class="impact-entity label-${e.label}" data-uid="${e.uid}" title="${e.gsid || e.uid}">
                        ${this._entityLabel(e)} <small>${e.label}</small>
                        <small class="via">${e.via.join(', ')}</small>
                    </span>`;
                }
                if (entities.length > 50) html += `<small class="more-hint">+${entities.length - 50}개 더...</small>`;
                html += `</div></div>`;
            }

            html += `<button class="btn btn-sm" onclick="kgAnalysis.highlightImpact('${uid}', ${depth}, '${impactType}')">지도에 표시</button>`;
            resultDiv.innerHTML = html;

            resultDiv.querySelectorAll('.impact-entity').forEach(el => {
                el.addEventListener('click', () => this.cm.highlightEntity(el.dataset.uid));
            });
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    highlightImpact(uid, depth, impactType) {
        this.clearHighlights();
        const typeParam = impactType ? `&impact_type=${impactType}` : '';
        fetch(`/api/kg/impact?uid=${uid}&depth=${depth}${typeParam}`)
            .then(r => r.json())
            .then(data => {
                const colors = [Cesium.Color.YELLOW, Cesium.Color.ORANGE, Cesium.Color.RED, Cesium.Color.DARKRED];
                // Highlight source
                const srcPos = this.cm.entityPositions[uid];
                if (srcPos) {
                    this.highlightEntities.push(this.cm.viewer.entities.add({
                        position: srcPos,
                        ellipse: { semiMinorAxis: 35, semiMajorAxis: 35, height: 1,
                            material: Cesium.Color.WHITE.withAlpha(0.5), outline: true,
                            outlineColor: Cesium.Color.WHITE, outlineWidth: 3 },
                        _isHighlight: true,
                    }));
                }
                for (const [dist, entities] of Object.entries(data.layers)) {
                    const color = colors[Math.min(parseInt(dist) - 1, colors.length - 1)];
                    for (const e of entities) {
                        const pos = this.cm.entityPositions[e.uid];
                        if (!pos) continue;
                        this.highlightEntities.push(this.cm.viewer.entities.add({
                            position: pos,
                            ellipse: { semiMinorAxis: 20, semiMajorAxis: 20, height: 1,
                                material: color.withAlpha(0.3), outline: true,
                                outlineColor: color, outlineWidth: 2 },
                            _isHighlight: true,
                        }));
                        // Line from source
                        if (srcPos) {
                            this.highlightEntities.push(this.cm.viewer.entities.add({
                                polyline: { positions: [srcPos, pos], width: 2,
                                    material: color.withAlpha(0.5) },
                                _isHighlight: true,
                            }));
                        }
                    }
                }
            });
    }

    // ── Nearby Search ────────────────────────────────────────
    async findNearby() {
        const uid = document.getElementById('nearby-uid').value.trim();
        const radius = document.getElementById('nearby-radius').value;
        const resultDiv = document.getElementById('nearby-result');
        if (!uid) { resultDiv.innerHTML = '<p class="error">Please enter an entity GSID.</p>'; return; }

        resultDiv.innerHTML = '<p class="loading">Searching nearby...</p>';
        try {
            const resp = await fetch(`/api/kg/nearby?uid=${encodeURIComponent(uid)}&radius_m=${radius}`);
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            const data = await resp.json();

            let html = `<div class="nearby-summary">${data.results.length} entities within ${radius}m of <code>${uid}</code></div>`;
            html += `<table class="result-table"><tr><th>Entity (GSID)</th><th>Type</th><th>Distance</th></tr>`;
            for (const r of data.results) {
                const displayName = r.name ? `${r.name}` : '';
                const gsidDisplay = r.gsid ? `<span class="entity-gsid-inline">${r.gsid}</span>` : '';
                html += `<tr class="clickable-row" data-uid="${r.uid}">
                    <td>${displayName}${gsidDisplay}</td>
                    <td><span class="label-badge label-${r.label}">${r.label}</span></td>
                    <td>${Math.round(r.dist_m)}m</td>
                </tr>`;
            }
            html += `</table>`;
            resultDiv.innerHTML = html;

            resultDiv.querySelectorAll('.clickable-row').forEach(el => {
                el.addEventListener('click', () => this.cm.highlightEntity(el.dataset.uid));
            });
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    // ── Anomaly Trace ────────────────────────────────────────
    async detectAnomalies() {
        const resultDiv = document.getElementById('anomaly-result');
        const temp = document.getElementById('thresh-temp').value;
        const noise = document.getElementById('thresh-noise').value;
        const aqi = document.getElementById('thresh-aqi').value;
        const humidity = document.getElementById('thresh-humidity').value;

        resultDiv.innerHTML = '<p class="loading">Detecting anomalies...</p>';
        try {
            const params = new URLSearchParams();
            if (temp) params.set('temp', temp);
            if (noise) params.set('noise', noise);
            if (aqi) params.set('aqi', aqi);
            if (humidity) params.set('humidity', humidity);
            const resp = await fetch(`/api/kg/anomaly?${params}`);
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            const data = await resp.json();

            if (!data.anomalies || data.anomalies.length === 0) {
                resultDiv.innerHTML = '<p class="success">No anomalies detected. All sensor readings are within normal range.</p>';
                return;
            }

            let html = `<div class="anomaly-summary">${data.anomalies.length} anomalous sensor(s) detected</div>`;
            for (const a of data.anomalies) {
                const sensorDisplay = a.sensor_gsid || a.sensor_uid;
                html += `<div class="anomaly-card">
                    <div class="anomaly-header">
                        <span class="anomaly-sensor" data-uid="${a.sensor_uid}"><span class="entity-gsid-inline">${sensorDisplay}</span></span>
                        <span class="anomaly-value">${a.sensor_type}: <strong>${a.sensor_value}${a.unit}</strong></span>
                    </div>`;

                const zones = (a.monitored_zones || []).filter(z => z.uid);
                if (zones.length > 0) {
                    html += `<div class="anomaly-chain">Monitors &rarr; `;
                    for (const z of zones) {
                        html += `<span class="label-badge label-${z.label}" data-uid="${z.uid}">${z.name || z.gsid || z.uid}</span> `;
                    }
                    html += `</div>`;
                }

                const affected = (a.affected_entities || []).filter(e => e.uid);
                if (affected.length > 0) {
                    html += `<div class="anomaly-affected">Affected entities: `;
                    for (const e of affected.slice(0, 10)) {
                        const label = e.name ? `${e.name} (${e.gsid || e.uid})` : (e.gsid || e.uid);
                        html += `<span class="label-badge label-${e.label}" data-uid="${e.uid}">${label}</span> `;
                    }
                    if (affected.length > 10) html += `<small>+${affected.length - 10} more</small>`;
                    html += `</div>`;
                }
                const idx = data.anomalies.indexOf(a);
                html += `<div style="margin-top:6px;">
                    <button class="btn btn-xs anomaly-focus-btn" data-sensor="${a.sensor_uid}">📍 Focus</button>
                    <button class="btn btn-xs anomaly-viz-btn" data-idx="${idx}" style="margin-left:4px;">🗺️ Show Impact</button>
                </div>`;
                html += `</div>`;
            }
            html += `<button class="btn btn-sm" id="btn-visualize-anomaly">🗺️ Visualize on Map</button>`;
            resultDiv.innerHTML = html;

            // Store anomaly data for visualization
            this._lastAnomalyData = data;

            resultDiv.querySelectorAll('[data-uid]').forEach(el => {
                el.style.cursor = 'pointer';
                el.addEventListener('click', () => this.cm.highlightEntity(el.dataset.uid));
            });

            document.getElementById('btn-visualize-anomaly').addEventListener('click', () => {
                this.visualizeAnomalies(data);
            });

            // Individual sensor focus buttons
            resultDiv.querySelectorAll('.anomaly-focus-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const sensorUid = btn.dataset.sensor;
                    this.cm.flyToEntityClose(sensorUid);
                });
            });

            // Individual sensor visualize buttons
            resultDiv.querySelectorAll('.anomaly-viz-btn').forEach(btn => {
                btn.addEventListener('click', () => {
                    const idx = parseInt(btn.dataset.idx);
                    const singleAnomaly = { anomalies: [data.anomalies[idx]] };
                    this.visualizeAnomalies(singleAnomaly);
                });
            });

            // Auto-visualize on map
            this.visualizeAnomalies(data);
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    /** Visualize anomaly results on the 3D map with impact zones. */
    visualizeAnomalies(data) {
        this.clearHighlights();

        // Sensor type → color mapping
        const sensorColors = {
            aqi: { main: Cesium.Color.RED, glow: Cesium.Color.RED.withAlpha(0.15) },
            noise: { main: Cesium.Color.ORANGE, glow: Cesium.Color.ORANGE.withAlpha(0.12) },
            temperature: { main: Cesium.Color.YELLOW, glow: Cesium.Color.YELLOW.withAlpha(0.1) },
            humidity: { main: Cesium.Color.CYAN, glow: Cesium.Color.CYAN.withAlpha(0.1) },
        };

        let firstSensorPos = null;

        for (const a of (data.anomalies || [])) {
            const sensorPos = this.cm.entityPositions[a.sensor_uid];
            if (!sensorPos) continue;
            if (!firstSensorPos) firstSensorPos = sensorPos;

            const colors = sensorColors[a.sensor_type] || { main: Cesium.Color.WHITE, glow: Cesium.Color.WHITE.withAlpha(0.1) };
            const mainColor = colors.main;

            // 1. Sensor: pulsing ring highlight
            this.highlightEntities.push(this.cm.viewer.entities.add({
                position: sensorPos,
                ellipse: {
                    semiMinorAxis: 15,
                    semiMajorAxis: 15,
                    height: 2,
                    material: mainColor.withAlpha(0.6),
                    outline: true,
                    outlineColor: mainColor,
                    outlineWidth: 3,
                },
                label: {
                    text: `⚠ ${a.sensor_type}: ${a.sensor_value}${a.unit || ''}`,
                    font: 'bold 13px sans-serif',
                    fillColor: mainColor,
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    outlineWidth: 2,
                    outlineColor: Cesium.Color.BLACK,
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                    pixelOffset: new Cesium.Cartesian2(0, -20),
                    disableDepthTestDistance: Number.POSITIVE_INFINITY,
                },
                _isHighlight: true,
            }));

            // Collect all affected UIDs (zones + children)
            const affectedUids = [];
            for (const z of (a.monitored_zones || [])) {
                if (z.uid) affectedUids.push(z.uid);
            }
            for (const e of (a.affected_entities || [])) {
                if (e.uid) affectedUids.push(e.uid);
            }

            // 2. Impact zone: translucent dome around sensor (radius covers affected area)
            let maxDist = 50; // default 50m radius
            for (const uid of affectedUids) {
                const pos = this.cm.entityPositions[uid];
                if (!pos) continue;
                const dist = Cesium.Cartesian3.distance(sensorPos, pos);
                if (dist > maxDist) maxDist = dist;
            }

            this.highlightEntities.push(this.cm.viewer.entities.add({
                position: sensorPos,
                ellipse: {
                    semiMinorAxis: maxDist + 20,
                    semiMajorAxis: maxDist + 20,
                    height: 0.5,
                    material: colors.glow,
                    outline: true,
                    outlineColor: mainColor.withAlpha(0.4),
                    outlineWidth: 2,
                },
                _isHighlight: true,
            }));

            // 3. Affected entities: highlight + connection line from sensor
            for (const uid of affectedUids) {
                const pos = this.cm.entityPositions[uid];
                if (!pos) continue;

                // Highlight ring on affected entity
                this.highlightEntities.push(this.cm.viewer.entities.add({
                    position: pos,
                    ellipse: {
                        semiMinorAxis: 12,
                        semiMajorAxis: 12,
                        height: 1.5,
                        material: mainColor.withAlpha(0.25),
                        outline: true,
                        outlineColor: mainColor.withAlpha(0.7),
                        outlineWidth: 2,
                    },
                    _isHighlight: true,
                }));

                // Connection line from sensor to affected entity
                this.highlightEntities.push(this.cm.viewer.entities.add({
                    polyline: {
                        positions: [sensorPos, pos],
                        width: 2.5,
                        material: new Cesium.PolylineGlowMaterialProperty({
                            glowPower: 0.2,
                            color: mainColor.withAlpha(0.6),
                        }),
                    },
                    _isHighlight: true,
                }));
            }
        }

        // Fly to the first sensor to see the visualization
        if (firstSensorPos) {
            const sphere = new Cesium.BoundingSphere(firstSensorPos, 0);
            this.cm.viewer.camera.flyToBoundingSphere(sphere, {
                offset: new Cesium.HeadingPitchRange(
                    Cesium.Math.toRadians(0),
                    Cesium.Math.toRadians(-50),
                    400
                ),
                duration: 1.2,
            });
        }
    }

    // ── Analytics (mode dispatch) ─────────────────────────────
    async runAnalytics() {
        const mode = document.getElementById('analytics-mode').value;
        if (mode === 'dong') return this._analyticsDong();
        if (mode === 'connectivity') return this._analyticsConnectivity();
    }

    // ── Dong Infrastructure Comparison ────────────────────────
    async _analyticsDong() {
        const metric = document.getElementById('analytics-metric').value;
        const resultDiv = document.getElementById('analytics-result');
        resultDiv.innerHTML = '<p class="loading">행정동 인프라 비교 분석 중...</p>';

        try {
            const resp = await fetch(`/api/kg/analytics/dong_comparison?metric=${metric}`);
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            const data = await resp.json();

            const metricNames = {
                overall: '전체 종합', shelter: '🛡️ 대피시설', transit: '🚌 대중교통',
                park: '🌳 공원/녹지', monitoring: '📡 모니터링', road: '🛣️ 도로 접근',
            };
            const metricKey = {
                overall: 'overall_score', shelter: 'shelter_pct', transit: 'transit_pct',
                park: 'park_pct', monitoring: 'monitoring_pct', road: 'road_pct',
            }[metric] || 'overall_score';

            // Summary cards
            const avg = data.city_avg;
            let html = `<div class="analytics-summary-cards">
                <div class="analytics-card"><div class="analytics-card-value">${data.total_buildings.toLocaleString()}</div><div class="analytics-card-label">총 건축물</div></div>
                <div class="analytics-card"><div class="analytics-card-value" style="color:${this._scoreColor(avg.shelter_pct)}">${avg.shelter_pct}%</div><div class="analytics-card-label">대피시설 평균</div></div>
                <div class="analytics-card"><div class="analytics-card-value" style="color:${this._scoreColor(avg.transit_pct)}">${avg.transit_pct}%</div><div class="analytics-card-label">대중교통 평균</div></div>
                <div class="analytics-card"><div class="analytics-card-value" style="color:${this._scoreColor(avg.park_pct)}">${avg.park_pct}%</div><div class="analytics-card-label">공원 평균</div></div>
                <div class="analytics-card"><div class="analytics-card-value" style="color:${this._scoreColor(avg.monitoring_pct)}">${avg.monitoring_pct}%</div><div class="analytics-card-label">모니터링 평균</div></div>
                <div class="analytics-card"><div class="analytics-card-value" style="color:${this._scoreColor(avg.road_pct)}">${avg.road_pct}%</div><div class="analytics-card-label">도로 접근 평균</div></div>
            </div>`;

            // Bar chart (sorted low → high)
            html += `<h4>${metricNames[metric]} — 행정동별 (낮은 순)</h4>`;
            const maxVal = Math.max(...data.dongs.map(d => d[metricKey]), 1);
            for (const d of data.dongs) {
                const val = d[metricKey];
                const pct = (val / 100 * 100).toFixed(0);
                const color = this._scoreColor(val);
                html += `<div class="analytics-bar-row" data-lon="${d.center_lon}" data-lat="${d.center_lat}">
                    <span class="analytics-bar-dong">${d.dong}</span>
                    <div class="analytics-bar-track"><div class="analytics-bar-fill" style="width:${pct}%;background:${color}"></div></div>
                    <span class="analytics-bar-value" style="color:${color}">${val}%</span>
                    <span style="font-size:10px;color:#607d8b;min-width:50px">(${d.total.toLocaleString()})</span>
                </div>`;
            }

            this._lastDongData = data;
            html += `<button class="btn btn-sm" onclick="kgAnalysis.highlightDongComparison()">지도에 표시</button>`;
            resultDiv.innerHTML = html;

            // Clickable rows → fly to dong
            resultDiv.querySelectorAll('.analytics-bar-row').forEach(el => {
                el.addEventListener('click', () => {
                    const lon = parseFloat(el.dataset.lon);
                    const lat = parseFloat(el.dataset.lat);
                    if (lon && lat) {
                        this.cm.viewer.camera.flyTo({
                            destination: Cesium.Cartesian3.fromDegrees(lon, lat, 1500),
                            duration: 1.2,
                        });
                    }
                });
            });
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    _scoreColor(val) {
        if (val >= 70) return '#66bb6a';
        if (val >= 40) return '#ff9800';
        return '#ef5350';
    }

    highlightDongComparison() {
        this.clearHighlights();
        const data = this._lastDongData;
        if (!data) return;

        const metric = document.getElementById('analytics-metric').value;
        const metricKey = {
            overall: 'overall_score', shelter: 'shelter_pct', transit: 'transit_pct',
            park: 'park_pct', monitoring: 'monitoring_pct', road: 'road_pct',
        }[metric] || 'overall_score';

        let firstPos = null;
        const maxTotal = Math.max(...data.dongs.map(d => d.total), 1);

        for (const d of data.dongs) {
            if (!d.center_lon || !d.center_lat) continue;
            const pos = Cesium.Cartesian3.fromDegrees(d.center_lon, d.center_lat, 2);
            if (!firstPos) firstPos = pos;

            const val = d[metricKey];
            const color = val >= 70 ? Cesium.Color.fromCssColorString('#66bb6a')
                        : val >= 40 ? Cesium.Color.fromCssColorString('#ff9800')
                        : Cesium.Color.fromCssColorString('#ef5350');
            const radius = 80 + (d.total / maxTotal) * 300;

            this.highlightEntities.push(this.cm.viewer.entities.add({
                position: pos,
                ellipse: {
                    semiMinorAxis: radius, semiMajorAxis: radius, height: 1,
                    material: color.withAlpha(0.25),
                    outline: true, outlineColor: color.withAlpha(0.8), outlineWidth: 2,
                },
                label: {
                    text: `${d.dong}\n${val}%`,
                    font: 'bold 13px sans-serif',
                    fillColor: Cesium.Color.WHITE,
                    outlineColor: Cesium.Color.BLACK,
                    outlineWidth: 2,
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    verticalOrigin: Cesium.VerticalOrigin.CENTER,
                    showBackground: true,
                    backgroundColor: color.withAlpha(0.7),
                },
                _isHighlight: true,
            }));
        }

        if (firstPos) {
            this.cm.viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(
                    data.dongs.reduce((s, d) => s + (d.center_lon || 0), 0) / data.dongs.length,
                    data.dongs.reduce((s, d) => s + (d.center_lat || 0), 0) / data.dongs.length,
                    8000
                ),
                duration: 1.5,
            });
        }
    }

    // ── KG Connectivity Analysis ──────────────────────────────
    async _analyticsConnectivity() {
        const connType = document.getElementById('analytics-conn-type').value;
        const resultDiv = document.getElementById('analytics-result');
        resultDiv.innerHTML = '<p class="loading">KG 연결성 분석 중...</p>';

        try {
            const params = new URLSearchParams({ analysis: connType });
            if (connType === 'isolated') {
                params.set('max_degree', document.getElementById('analytics-max-degree').value);
            }
            const resp = await fetch(`/api/kg/analytics/connectivity?${params}`);
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            const data = await resp.json();

            if (connType === 'isolated') this._renderIsolated(data, resultDiv);
            else if (connType === 'density') this._renderDensity(data, resultDiv);
            else if (connType === 'patterns') this._renderPatterns(data, resultDiv);

        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    _renderIsolated(data, resultDiv) {
        let html = `<div class="impact-summary">
            차수 ≤ ${data.max_degree}인 노드: <strong style="color:#ef5350">${data.total_isolated.toLocaleString()}</strong>개
        </div>`;

        // By label breakdown
        html += `<div class="impact-type-breakdown">`;
        for (const [lbl, cnt] of Object.entries(data.by_label)) {
            html += `<span class="label-badge label-${lbl}">${lbl}: ${cnt.toLocaleString()}</span> `;
        }
        html += `</div>`;

        // By dong breakdown (top 10)
        const dongEntries = Object.entries(data.by_dong).sort((a, b) => b[1] - a[1]).slice(0, 10);
        if (dongEntries.length > 0) {
            const maxCnt = dongEntries[0][1];
            html += `<h4>행정동별 고립 노드 (상위 10)</h4>`;
            for (const [dong, cnt] of dongEntries) {
                const pct = (cnt / maxCnt * 100).toFixed(0);
                html += `<div class="analytics-bar-row">
                    <span class="analytics-bar-dong">${dong}</span>
                    <div class="analytics-bar-track"><div class="analytics-bar-fill" style="width:${pct}%;background:#ef5350"></div></div>
                    <span class="analytics-bar-value" style="color:#ef5350">${cnt.toLocaleString()}</span>
                </div>`;
            }
        }

        // Entity list
        if (data.entities.length > 0) {
            html += `<h4>고립 엔티티 목록 (${data.entities.length}개)</h4>`;
            html += `<table class="result-table"><tr><th>엔티티</th><th>유형</th><th>차수</th><th>행정동</th></tr>`;
            for (const e of data.entities.slice(0, 50)) {
                html += `<tr class="clickable-row" data-uid="${e.uid}">
                    <td>${e.name || e.gsid || e.uid}</td>
                    <td><span class="label-badge label-${e.label}">${e.label}</span></td>
                    <td>${e.degree}</td>
                    <td>${e.dong || ''}</td>
                </tr>`;
            }
            html += `</table>`;
            if (data.entities.length > 50) html += `<small>+${data.entities.length - 50}개 더...</small>`;
        }

        this._lastIsolatedData = data;
        html += `<button class="btn btn-sm" onclick="kgAnalysis.highlightIsolated()">지도에 표시</button>`;
        resultDiv.innerHTML = html;

        resultDiv.querySelectorAll('.clickable-row').forEach(el => {
            el.addEventListener('click', () => this.cm.highlightEntity(el.dataset.uid));
        });
    }

    highlightIsolated() {
        this.clearHighlights();
        const data = this._lastIsolatedData;
        if (!data || !data.entities) return;

        let firstPos = null;
        for (const e of data.entities.slice(0, 300)) {
            if (!e.lon || !e.lat) continue;
            const pos = Cesium.Cartesian3.fromDegrees(e.lon, e.lat, 2);
            if (!firstPos) firstPos = pos;
            this.highlightEntities.push(this.cm.viewer.entities.add({
                position: pos,
                ellipse: {
                    semiMinorAxis: 10, semiMajorAxis: 10, height: 1,
                    material: Cesium.Color.RED.withAlpha(0.35),
                    outline: true, outlineColor: Cesium.Color.RED.withAlpha(0.8), outlineWidth: 2,
                },
                _isHighlight: true,
            }));
        }
        if (firstPos) {
            this.cm.viewer.camera.flyToBoundingSphere(
                new Cesium.BoundingSphere(firstPos, 0),
                { offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-50), 800), duration: 1.2 }
            );
        }
    }

    _renderDensity(data, resultDiv) {
        let html = `<h4>행정동별 관계 밀도 (낮은 순)</h4>`;
        const maxDeg = Math.max(...data.dongs.map(d => d.avg_degree), 1);

        html += `<table class="result-table"><tr><th>행정동</th><th>건물</th><th>평균 차수</th><th>중앙값</th><th>최대</th><th>저연결(≤2)</th><th></th></tr>`;
        for (const d of data.dongs) {
            const color = this._scoreColor(d.avg_degree / maxDeg * 100);
            const barPct = (d.avg_degree / maxDeg * 100).toFixed(0);
            html += `<tr class="clickable-row" data-lon="${d.center_lon}" data-lat="${d.center_lat}" style="cursor:pointer">
                <td>${d.dong}</td>
                <td>${d.building_count.toLocaleString()}</td>
                <td style="font-weight:bold;color:${color}">${d.avg_degree}</td>
                <td>${d.median_degree}</td>
                <td>${d.max_degree}</td>
                <td style="color:#ef5350">${d.low_conn_count.toLocaleString()} (${d.low_conn_pct}%)</td>
                <td style="width:100px"><div class="analytics-bar-track"><div class="analytics-bar-fill" style="width:${barPct}%;background:${color}"></div></div></td>
            </tr>`;
        }
        html += `</table>`;

        this._lastDensityData = data;
        html += `<button class="btn btn-sm" onclick="kgAnalysis.highlightDensity()">지도에 표시</button>`;
        resultDiv.innerHTML = html;

        resultDiv.querySelectorAll('.clickable-row').forEach(el => {
            el.addEventListener('click', () => {
                const lon = parseFloat(el.dataset.lon);
                const lat = parseFloat(el.dataset.lat);
                if (lon && lat) {
                    this.cm.viewer.camera.flyTo({ destination: Cesium.Cartesian3.fromDegrees(lon, lat, 1500), duration: 1.2 });
                }
            });
        });
    }

    highlightDensity() {
        this.clearHighlights();
        const data = this._lastDensityData;
        if (!data) return;

        const maxDeg = Math.max(...data.dongs.map(d => d.avg_degree), 1);
        const maxTotal = Math.max(...data.dongs.map(d => d.building_count), 1);

        for (const d of data.dongs) {
            if (!d.center_lon || !d.center_lat) continue;
            const pos = Cesium.Cartesian3.fromDegrees(d.center_lon, d.center_lat, 2);
            const ratio = d.avg_degree / maxDeg;
            const color = ratio >= 0.7 ? Cesium.Color.fromCssColorString('#66bb6a')
                        : ratio >= 0.4 ? Cesium.Color.fromCssColorString('#ff9800')
                        : Cesium.Color.fromCssColorString('#ef5350');
            const radius = 80 + (d.building_count / maxTotal) * 300;

            this.highlightEntities.push(this.cm.viewer.entities.add({
                position: pos,
                ellipse: {
                    semiMinorAxis: radius, semiMajorAxis: radius, height: 1,
                    material: color.withAlpha(0.2),
                    outline: true, outlineColor: color.withAlpha(0.8), outlineWidth: 2,
                },
                label: {
                    text: `${d.dong}\navg ${d.avg_degree}`,
                    font: 'bold 12px sans-serif',
                    fillColor: Cesium.Color.WHITE,
                    outlineColor: Cesium.Color.BLACK,
                    outlineWidth: 2,
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    verticalOrigin: Cesium.VerticalOrigin.CENTER,
                    showBackground: true,
                    backgroundColor: color.withAlpha(0.7),
                },
                _isHighlight: true,
            }));
        }

        this.cm.viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(
                data.dongs.reduce((s, d) => s + (d.center_lon || 0), 0) / data.dongs.length,
                data.dongs.reduce((s, d) => s + (d.center_lat || 0), 0) / data.dongs.length,
                8000
            ),
            duration: 1.5,
        });
    }

    _renderPatterns(data, resultDiv) {
        // Build label → label aggregation (summing across rel types)
        const labelPair = {};
        for (const r of data.matrix) {
            const key = `${r.src}→${r.tgt}`;
            labelPair[key] = (labelPair[key] || 0) + r.cnt;
        }

        // Build heatmap
        const labels = data.labels;
        let html = `<h4>엔티티 유형 간 연결 패턴</h4>`;
        html += `<div class="analytics-matrix"><table><tr><th></th>`;
        for (const tgt of labels) html += `<th>${tgt}</th>`;
        html += `</tr>`;

        const maxCnt = Math.max(...Object.values(labelPair), 1);
        for (const src of labels) {
            html += `<tr><th>${src}</th>`;
            for (const tgt of labels) {
                const cnt = labelPair[`${src}→${tgt}`] || 0;
                if (cnt === 0) {
                    html += `<td style="color:#1e3a5f">-</td>`;
                } else {
                    const intensity = Math.min(cnt / maxCnt, 1);
                    const alpha = (0.15 + intensity * 0.7).toFixed(2);
                    html += `<td style="background:rgba(79,195,247,${alpha});color:#fff;font-weight:${intensity > 0.3 ? 'bold' : 'normal'}">${cnt >= 1000 ? (cnt / 1000).toFixed(1) + 'K' : cnt}</td>`;
                }
            }
            html += `</tr>`;
        }
        html += `</table></div>`;

        // Detailed top relationships
        html += `<h4>상위 관계 유형 (건수 순)</h4>`;
        html += `<table class="result-table"><tr><th>소스</th><th>관계</th><th>타겟</th><th>건수</th></tr>`;
        for (const r of data.matrix.slice(0, 20)) {
            html += `<tr>
                <td><span class="label-badge label-${r.src}">${r.src}</span></td>
                <td style="color:#ff9800;font-weight:600">${r.rel}</td>
                <td><span class="label-badge label-${r.tgt}">${r.tgt}</span></td>
                <td>${r.cnt.toLocaleString()}</td>
            </tr>`;
        }
        html += `</table>`;

        resultDiv.innerHTML = html;
    }

    // ── Coverage Dead Zone Analysis ──────────────────────────
    async coverageAnalysis() {
        const facilityType = document.getElementById('coverage-type').value;
        const dong = document.getElementById('coverage-dong').value;
        const resultDiv = document.getElementById('coverage-result');

        resultDiv.innerHTML = '<p class="loading">사각지대 분석 중...</p>';
        try {
            const params = new URLSearchParams({ facility_type: facilityType });
            if (dong) params.set('admin_dong', dong);
            const resp = await fetch(`/api/kg/coverage?${params}`);
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            const data = await resp.json();

            const typeNames = { shelter: '대피시설', transit: '대중교통', park: '공원', all: '전체(3가지 모두 없음)' };
            const typeName = typeNames[data.facility_type] || data.facility_type;

            let html = `<div class="coverage-summary">`;
            if (data.summary.gap_ratio !== null) {
                html += `전체 <strong>${data.summary.total.toLocaleString()}</strong>개 건축물 중
                    <strong style="color:#ef5350">${data.summary.gap_count.toLocaleString()}</strong>개
                    (<strong style="color:#ef5350">${data.summary.gap_ratio}%</strong>) ${typeName} 사각지대`;
            } else {
                html += `<strong style="color:#ef5350">${data.buildings.length}</strong>개 건축물이 ${typeName}에 해당`;
            }
            html += `</div>`;

            // Dong-level breakdown table
            if (data.by_dong.length > 0) {
                const gapKey = { shelter: 'no_shelter', transit: 'no_transit', park: 'no_park', all: 'no_shelter' }[data.facility_type] || 'no_shelter';
                html += `<table class="result-table"><tr><th>행정동</th><th>건물</th><th>사각지대</th><th>비율</th><th></th></tr>`;
                for (const d of data.by_dong) {
                    const gapCount = d[gapKey];
                    const pct = d.gap_ratio;
                    const barColor = pct > 20 ? '#ef5350' : pct > 10 ? '#ff9800' : '#66bb6a';
                    html += `<tr>
                        <td>${d.dong}</td><td>${d.total.toLocaleString()}</td>
                        <td style="color:${barColor};font-weight:bold">${gapCount.toLocaleString()}</td>
                        <td>${pct}%</td>
                        <td><div class="coverage-bar-track"><div class="coverage-bar-fill" style="width:${Math.min(pct, 100)}%;background:${barColor}"></div></div></td>
                    </tr>`;
                }
                html += `</table>`;
            }

            // Building list
            if (data.buildings.length > 0) {
                html += `<div class="coverage-buildings"><strong>사각지대 건축물 (${data.buildings.length}개)</strong>`;
                html += `<div class="coverage-building-list">`;
                for (const b of data.buildings.slice(0, 100)) {
                    html += `<span class="impact-entity label-Building" data-uid="${b.uid}" title="${b.gsid || b.uid}">
                        ${b.name || b.addr || b.uid} <small>${b.dong || ''}</small>
                    </span>`;
                }
                if (data.buildings.length > 100) html += `<small class="more-hint">+${data.buildings.length - 100}개 더...</small>`;
                html += `</div></div>`;
            }

            // Store for visualization
            this._lastCoverageData = data;
            html += `<button class="btn btn-sm" onclick="kgAnalysis.highlightCoverage()">지도에 표시</button>`;
            resultDiv.innerHTML = html;

            resultDiv.querySelectorAll('.impact-entity').forEach(el => {
                el.addEventListener('click', () => this.cm.highlightEntity(el.dataset.uid));
            });
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    highlightCoverage() {
        this.clearHighlights();
        const data = this._lastCoverageData;
        if (!data || !data.buildings) return;

        let firstPos = null;
        for (const b of data.buildings.slice(0, 500)) {
            const pos = this.cm.entityPositions[b.uid];
            if (!pos) continue;
            if (!firstPos) firstPos = pos;
            this.highlightEntities.push(this.cm.viewer.entities.add({
                position: pos,
                ellipse: {
                    semiMinorAxis: 12, semiMajorAxis: 12, height: 1,
                    material: Cesium.Color.RED.withAlpha(0.35),
                    outline: true, outlineColor: Cesium.Color.RED.withAlpha(0.8), outlineWidth: 2,
                },
                _isHighlight: true,
            }));
        }

        if (firstPos) {
            this.cm.viewer.camera.flyToBoundingSphere(
                new Cesium.BoundingSphere(firstPos, 0),
                { offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-50), 800), duration: 1.2 }
            );
        }
    }

    // ── Road Closure Impact ───────────────────────────────────
    async roadImpact() {
        const roadUid = document.getElementById('road-select').value;
        const resultDiv = document.getElementById('road-impact-result');
        if (!roadUid) { resultDiv.innerHTML = '<p class="error">도로를 선택하세요.</p>'; return; }

        resultDiv.innerHTML = '<p class="loading">도로 영향 분석 중...</p>';
        try {
            const resp = await fetch(`/api/kg/road_impact?road_uid=${encodeURIComponent(roadUid)}`);
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            const data = await resp.json();

            let html = `<div class="impact-summary">
                <strong>${data.road.name}</strong> 폐쇄 시 총 <strong style="color:#ef5350">${data.total_affected}</strong>개 엔티티 영향
            </div>`;

            // Entity type breakdown
            if (data.by_label) {
                html += `<div class="impact-type-breakdown">`;
                for (const [lbl, cnt] of Object.entries(data.by_label)) {
                    html += `<span class="label-badge label-${lbl}">${lbl}: ${cnt}</span> `;
                }
                html += `</div>`;
            }

            // Critical entities (no alternative road)
            if (data.critical.length > 0) {
                html += `<div class="critical-section">
                    <div class="critical-header">⚠️ 대안 도로 없는 취약 시설: <strong style="color:#ef5350">${data.critical.length}</strong>개</div>
                    <div class="layer-entities">`;
                for (const e of data.critical.slice(0, 30)) {
                    html += `<span class="impact-entity critical-entity" data-uid="${e.uid}" title="${e.gsid || e.uid}">
                        ${e.name || e.uid} <small class="label-badge label-${e.label}">${e.label}</small>
                    </span>`;
                }
                if (data.critical.length > 30) html += `<small class="more-hint">+${data.critical.length - 30}개 더...</small>`;
                html += `</div></div>`;
            }

            // All affected list (with alternative roads)
            html += `<div class="coverage-buildings"><strong>전체 영향 엔티티</strong>`;
            html += `<table class="result-table"><tr><th>엔티티</th><th>유형</th><th>대안 도로</th></tr>`;
            for (const e of data.all_affected.slice(0, 50)) {
                const altText = e.has_alternative
                    ? e.alt_roads.map(a => a.name || a.uid).join(', ')
                    : '<span style="color:#ef5350;font-weight:bold">대안 없음</span>';
                html += `<tr class="clickable-row" data-uid="${e.uid}">
                    <td>${e.name || e.uid}</td>
                    <td><span class="label-badge label-${e.label}">${e.label}</span></td>
                    <td>${altText}</td>
                </tr>`;
            }
            html += `</table>`;
            if (data.all_affected.length > 50) html += `<small>+${data.all_affected.length - 50}개 더...</small>`;
            html += `</div>`;

            this._lastRoadImpactData = data;
            html += `<button class="btn btn-sm" onclick="kgAnalysis.highlightRoadImpact()">지도에 표시</button>`;
            resultDiv.innerHTML = html;

            resultDiv.querySelectorAll('[data-uid]').forEach(el => {
                el.style.cursor = 'pointer';
                el.addEventListener('click', () => this.cm.highlightEntity(el.dataset.uid));
            });
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    highlightRoadImpact() {
        this.clearHighlights();
        const data = this._lastRoadImpactData;
        if (!data) return;

        const criticalUids = new Set(data.critical.map(e => e.uid));
        let firstPos = null;

        // Draw road polyline (red) if coordinates available
        if (data.road.coordinates) {
            try {
                const coords = typeof data.road.coordinates === 'string' ? JSON.parse(data.road.coordinates) : data.road.coordinates;
                if (Array.isArray(coords) && coords.length >= 2) {
                    const positions = coords.map(c => Cesium.Cartesian3.fromDegrees(c[0], c[1], 2));
                    this.highlightEntities.push(this.cm.viewer.entities.add({
                        polyline: {
                            positions, width: 8,
                            material: new Cesium.PolylineGlowMaterialProperty({ glowPower: 0.3, color: Cesium.Color.RED }),
                        },
                        _isHighlight: true,
                    }));
                    if (!firstPos && positions.length > 0) firstPos = positions[Math.floor(positions.length / 2)];
                }
            } catch (e) { /* ignore parse errors */ }
        }

        // Highlight affected entities
        for (const e of data.all_affected.slice(0, 300)) {
            const pos = this.cm.entityPositions[e.uid];
            if (!pos) continue;
            if (!firstPos) firstPos = pos;
            const isCritical = criticalUids.has(e.uid);
            const color = isCritical ? Cesium.Color.RED : Cesium.Color.ORANGE;
            this.highlightEntities.push(this.cm.viewer.entities.add({
                position: pos,
                ellipse: {
                    semiMinorAxis: isCritical ? 15 : 10, semiMajorAxis: isCritical ? 15 : 10, height: 1,
                    material: color.withAlpha(isCritical ? 0.4 : 0.25),
                    outline: true, outlineColor: color, outlineWidth: isCritical ? 3 : 1.5,
                },
                _isHighlight: true,
            }));
        }

        if (firstPos) {
            this.cm.viewer.camera.flyToBoundingSphere(
                new Cesium.BoundingSphere(firstPos, 0),
                { offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-50), 600), duration: 1.2 }
            );
        }
    }

    // ── Safety Profile ────────────────────────────────────────
    async safetyProfile() {
        const uid = document.getElementById('safety-uid').value.trim();
        const resultDiv = document.getElementById('safety-result');
        if (!uid) { resultDiv.innerHTML = '<p class="error">엔티티 GSID를 입력하세요.</p>'; return; }

        resultDiv.innerHTML = '<p class="loading">안전 프로파일 조회 중...</p>';
        try {
            const resp = await fetch(`/api/kg/safety_profile?uid=${encodeURIComponent(uid)}`);
            if (!resp.ok) throw new Error((await resp.json()).detail || resp.statusText);
            const data = await resp.json();

            const s = data.scores;
            const gaugeColor = s.overall >= 70 ? '#66bb6a' : s.overall >= 40 ? '#ff9800' : '#ef5350';
            const gaugeLabel = s.overall >= 70 ? '안전' : s.overall >= 40 ? '주의' : '위험';

            let html = `<div class="safety-header">
                <div class="safety-gauge" style="--score:${s.overall};--color:${gaugeColor}">
                    <div class="safety-gauge-value">${s.overall}<small>점</small></div>
                    <div class="safety-gauge-label" style="color:${gaugeColor}">${gaugeLabel}</div>
                </div>
                <div class="safety-entity-info">
                    <div class="safety-entity-name">${data.entity.name || data.entity.uid}</div>
                    <div class="safety-entity-gsid">${data.entity.gsid || ''}</div>
                    <div><span class="label-badge label-${data.entity.label}">${data.entity.label}</span></div>
                </div>
            </div>`;

            // Score bars
            const bars = [
                { key: 'shelter', label: '🛡️ 대피시설', score: s.shelter, weight: '30%' },
                { key: 'transit', label: '🚌 대중교통', score: s.transit, weight: '20%' },
                { key: 'park', label: '🌳 공원/녹지', score: s.park, weight: '15%' },
                { key: 'monitoring', label: '📡 모니터링', score: s.monitoring, weight: '20%' },
                { key: 'road', label: '🛣️ 도로 접근', score: s.road, weight: '15%' },
            ];

            html += `<div class="safety-bars">`;
            for (const b of bars) {
                const barColor = b.score >= 70 ? '#66bb6a' : b.score >= 40 ? '#ff9800' : '#ef5350';
                html += `<div class="safety-bar-row">
                    <span class="safety-bar-label">${b.label} <small style="color:#78909c">(${b.weight})</small></span>
                    <div class="safety-bar-track"><div class="safety-bar-fill" style="width:${b.score}%;background:${barColor}"></div></div>
                    <span class="safety-bar-score">${b.score}</span>
                </div>`;
            }
            html += `</div>`;

            // Detail sections
            const d = data.details;
            const sections = [
                { icon: '🛡️', name: '대피시설', items: d.shelters, nameKey: 'name', subKey: 'type_name' },
                { icon: '🚌', name: '대중교통', items: d.transit, nameKey: 'name', subKey: 'type_name' },
                { icon: '🌳', name: '공원/녹지', items: d.parks, nameKey: 'name', subKey: 'type_name' },
                { icon: '📡', name: '센서', items: d.sensors, nameKey: 'type', subKey: 'value', unit: true },
                { icon: '📹', name: 'CCTV', items: d.cameras, nameKey: 'name', subKey: 'status' },
                { icon: '🛣️', name: '도로', items: d.roads, nameKey: 'name', subKey: null },
            ];

            html += `<div class="safety-details">`;
            for (const sec of sections) {
                const count = sec.items.length;
                const countColor = count === 0 ? '#ef5350' : '#66bb6a';
                html += `<div class="safety-detail-section">
                    <div class="safety-detail-header">${sec.icon} ${sec.name} <span style="color:${countColor};font-weight:bold">(${count}개)</span></div>`;
                if (count > 0) {
                    html += `<div class="safety-detail-items">`;
                    for (const item of sec.items) {
                        const name = item[sec.nameKey] || item.uid;
                        let sub = sec.subKey ? (item[sec.subKey] || '') : '';
                        if (sec.unit && item.unit) sub = `${item.value}${item.unit}`;
                        html += `<span class="safety-item" data-uid="${item.uid}">${name} ${sub ? `<small>${sub}</small>` : ''}</span>`;
                    }
                    html += `</div>`;
                } else {
                    html += `<div class="safety-detail-empty">접근 불가</div>`;
                }
                html += `</div>`;
            }
            html += `</div>`;

            this._lastSafetyData = data;
            html += `<button class="btn btn-sm" onclick="kgAnalysis.highlightSafetyProfile()">지도에 표시</button>`;
            resultDiv.innerHTML = html;

            resultDiv.querySelectorAll('[data-uid]').forEach(el => {
                el.style.cursor = 'pointer';
                el.addEventListener('click', () => this.cm.highlightEntity(el.dataset.uid));
            });
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    highlightSafetyProfile() {
        this.clearHighlights();
        const data = this._lastSafetyData;
        if (!data) return;

        const targetPos = this.cm.entityPositions[data.entity.uid];
        if (!targetPos) return;

        // Target: white pulsing ring
        this.highlightEntities.push(this.cm.viewer.entities.add({
            position: targetPos,
            ellipse: { semiMinorAxis: 25, semiMajorAxis: 25, height: 1,
                material: Cesium.Color.WHITE.withAlpha(0.4), outline: true,
                outlineColor: Cesium.Color.WHITE, outlineWidth: 3 },
            _isHighlight: true,
        }));

        // Color-coded connected entities
        const colorMap = {
            shelters: Cesium.Color.fromCssColorString('#ab47bc'),  // purple
            transit: Cesium.Color.fromCssColorString('#29b6f6'),   // blue
            parks: Cesium.Color.fromCssColorString('#66bb6a'),     // green
            sensors: Cesium.Color.fromCssColorString('#ff9800'),   // orange
            cameras: Cesium.Color.fromCssColorString('#26c6da'),   // cyan
        };

        const d = data.details;
        for (const [key, color] of Object.entries(colorMap)) {
            for (const item of (d[key] || [])) {
                const pos = this.cm.entityPositions[item.uid];
                if (!pos) continue;
                this.highlightEntities.push(this.cm.viewer.entities.add({
                    position: pos,
                    ellipse: { semiMinorAxis: 12, semiMajorAxis: 12, height: 1.5,
                        material: color.withAlpha(0.3), outline: true,
                        outlineColor: color, outlineWidth: 2 },
                    _isHighlight: true,
                }));
                this.highlightEntities.push(this.cm.viewer.entities.add({
                    polyline: { positions: [targetPos, pos], width: 2.5,
                        material: new Cesium.PolylineGlowMaterialProperty({ glowPower: 0.2, color: color.withAlpha(0.6) }) },
                    _isHighlight: true,
                }));
            }
        }

        this.cm.viewer.camera.flyToBoundingSphere(
            new Cesium.BoundingSphere(targetPos, 0),
            { offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-50), 500), duration: 1.2 }
        );
    }

    // ── Cypher Query ─────────────────────────────────────────
    async runCypher() {
        const query = document.getElementById('cypher-input').value.trim();
        const resultDiv = document.getElementById('cypher-result');
        if (!query) { resultDiv.innerHTML = '<p class="error">Please enter a Cypher query.</p>'; return; }

        resultDiv.innerHTML = '<p class="loading">Executing query...</p>';
        try {
            const resp = await fetch('/api/kg/cypher', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ query }),
            });
            if (!resp.ok) { const e = await resp.json(); throw new Error(e.detail || resp.statusText); }
            const data = await resp.json();

            if (data.count === 0) {
                resultDiv.innerHTML = '<p>No results found.</p>';
                return;
            }

            // Auto-detect columns from first result
            const cols = Object.keys(data.results[0]);
            let html = `<div class="cypher-summary">${data.count} result(s)</div>`;
            html += `<table class="result-table"><tr>${cols.map(c => `<th>${c}</th>`).join('')}</tr>`;
            for (const row of data.results.slice(0, 50)) {
                html += `<tr>${cols.map(c => {
                    const val = row[c];
                    if (val && typeof val === 'object') return `<td><pre>${JSON.stringify(val, null, 1)}</pre></td>`;
                    return `<td>${val ?? ''}</td>`;
                }).join('')}</tr>`;
            }
            html += `</table>`;
            if (data.count > 50) html += `<p><small>Showing first 50 of ${data.count} results</small></p>`;
            resultDiv.innerHTML = html;
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }

    // ─── Road Profile Methods ─────────────────────────────────

    /** Load road profiles list for dropdown. */
    async _loadRoadProfiles() {
        try {
            const resp = await fetch('/api/kg/road_profiles');
            if (!resp.ok) return;
            const data = await resp.json();
            this._roadProfiles = data.profiles || [];

            const sel = document.getElementById('road-profile-select');
            // Clear existing options except first
            while (sel.options.length > 1) sel.remove(1);

            for (const p of this._roadProfiles) {
                const opt = document.createElement('option');
                opt.value = p.name;
                const r2 = p.r_squared != null ? p.r_squared.toFixed(3) : '?';
                const typeLabel = p.road_type === 'main' ? '대로' : p.road_type === 'secondary' ? '로' : '길';
                opt.textContent = `${p.name} (R²=${r2}, ${typeLabel}, ${p.building_count}건물)`;
                sel.appendChild(opt);
            }
        } catch (e) { /* ignore */ }
    }

    /** Load profile details for selected road. */
    async loadRoadProfile() {
        const roadName = document.getElementById('road-profile-select').value;
        if (!roadName) {
            alert('도로를 선택하세요.');
            return;
        }

        // Show summary from cached profiles
        const profile = (this._roadProfiles || []).find(p => p.name === roadName);
        const summaryDiv = document.getElementById('road-profile-summary');
        const entitiesWrap = document.getElementById('road-profile-entities');

        if (profile) {
            summaryDiv.style.display = '';
            const r2Val = profile.r_squared != null ? profile.r_squared.toFixed(4) : '—';
            const r2Pct = Math.min(100, (profile.r_squared || 0) * 100);
            const r2Color = r2Pct > 70 ? '#66bb6a' : r2Pct > 30 ? '#ffa726' : '#ef5350';

            document.getElementById('rp-r-squared').textContent = r2Val;
            document.getElementById('rp-r-squared').style.color = r2Color;
            const r2Bar = document.getElementById('rp-r2-bar');
            r2Bar.style.width = r2Pct + '%';
            r2Bar.style.background = r2Color;

            document.getElementById('rp-mpu').textContent =
                profile.meters_per_unit != null ? profile.meters_per_unit.toFixed(2) + 'm' : '—';
            document.getElementById('rp-bldg-count').textContent = profile.building_count || '—';
            document.getElementById('rp-num-range').textContent =
                (profile.min_num != null && profile.max_num != null) ? `${profile.min_num} ~ ${profile.max_num}` : '—';

            const typeMap = { main: '대로 (주간선)', secondary: '로 (보조간선)', path: '길 (이면도로)' };
            document.getElementById('rp-road-type').textContent = typeMap[profile.road_type] || profile.road_type || '—';
            document.getElementById('rp-residual').textContent =
                profile.residual_std_m != null ? profile.residual_std_m.toFixed(1) + 'm' : '—';
        }

        // Load road entities
        try {
            entitiesWrap.style.display = '';
            document.getElementById('rp-left-list').innerHTML = '<p class="loading">로딩 중...</p>';
            document.getElementById('rp-right-list').innerHTML = '<p class="loading">로딩 중...</p>';

            const resp = await fetch(`/api/kg/road_entities/${encodeURIComponent(roadName)}`);
            if (!resp.ok) {
                const e = await resp.json();
                throw new Error(e.detail || resp.statusText);
            }
            const data = await resp.json();

            this._currentRoadEntities = data;
            this._renderRoadSideList('rp-left-list', data.left_side || [], '좌측');
            this._renderRoadSideList('rp-right-list', data.right_side || [], '우측');

        } catch (e) {
            document.getElementById('rp-left-list').innerHTML = `<p class="error">${e.message}</p>`;
            document.getElementById('rp-right-list').innerHTML = '';
        }
    }

    /** Render entity list for one side of the road. */
    _renderRoadSideList(containerId, entities, sideLabel) {
        const container = document.getElementById(containerId);
        if (!entities.length) {
            container.innerHTML = `<p class="rp-empty">${sideLabel} 엔티티 없음</p>`;
            return;
        }

        let html = `<div class="rp-count">${entities.length}개</div>`;
        for (const e of entities) {
            const label = e.name || e.gsid || e.uid || '?';
            const typeIcon = this._entityTypeIcon(e.label);
            const bldgNum = e.building_main || e.bldg_main || '';
            const distStr = e.road_distance_m != null ? `${e.road_distance_m}m` : '';

            html += `<div class="rp-entity-item" onclick="kgAnalysis._flyToEntity(${e.longitude}, ${e.latitude}, '${(label).replace(/'/g, "\\'")}')">
                <span class="rp-entity-icon">${typeIcon}</span>
                <span class="rp-entity-name" title="${label}">${label}</span>
                <span class="rp-entity-num">#${bldgNum}</span>
                ${distStr ? `<span class="rp-entity-dist">${distStr}</span>` : ''}
            </div>`;
        }
        container.innerHTML = html;
    }

    /** Get icon for entity type. */
    _entityTypeIcon(label) {
        const icons = {
            'Building': '🏢', 'Road': '🛣️', 'Parcel': '📐',
            'AdminDong': '🏛️', 'IoTAddress': '📡', 'Facility': '⚙️',
            'OfficeBuilding': '🏢', 'SensorNode': '📡'
        };
        return icons[label] || '📍';
    }

    /** Fly to entity location on map. */
    _flyToEntity(lon, lat, name) {
        if (this.cm && this.cm.viewer && lon && lat) {
            this.cm.viewer.camera.flyTo({
                destination: Cesium.Cartesian3.fromDegrees(lon, lat, 500),
                duration: 1.5
            });
            // Minimal highlight
            this.cm.viewer.entities.add({
                position: Cesium.Cartesian3.fromDegrees(lon, lat, 5),
                point: { pixelSize: 12, color: Cesium.Color.YELLOW, outlineColor: Cesium.Color.BLACK, outlineWidth: 2 },
                label: { text: name, font: '13px sans-serif', fillColor: Cesium.Color.WHITE,
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE, outlineWidth: 2,
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM, pixelOffset: new Cesium.Cartesian2(0, -16) }
            });
        }
    }

    /** Show road polyline + entities on the Cesium map. */
    async showRoadOnMap() {
        const roadName = document.getElementById('road-profile-select').value;
        if (!roadName) {
            alert('도로를 선택하세요.');
            return;
        }

        if (!this.cm || !this.cm.viewer) return;
        const viewer = this.cm.viewer;

        // Clear previous road visualization
        if (this._roadVizEntities) {
            this._roadVizEntities.forEach(e => viewer.entities.remove(e));
        }
        this._roadVizEntities = [];

        // Get profile for polyline generation
        const profile = (this._roadProfiles || []).find(p => p.name === roadName);
        if (!profile) return;

        // Generate polyline from profile model
        const numPoints = 10;
        const step = (profile.max_num - profile.min_num) / (numPoints - 1);
        const positions = [];
        for (let i = 0; i < numPoints; i++) {
            const num = profile.min_num + i * step;
            // Linear model: lon = slope_lon * num + intercept_lon
            const lon = profile.slope_lon * num + profile.intercept_lon;
            const lat = profile.slope_lat * num + profile.intercept_lat;
            positions.push(Cesium.Cartesian3.fromDegrees(lon, lat, 3));
        }

        // Draw road polyline
        const roadEntity = viewer.entities.add({
            polyline: {
                positions: positions,
                width: 5,
                material: new Cesium.PolylineGlowMaterialProperty({
                    glowPower: 0.3,
                    color: Cesium.Color.fromCssColorString('#4fc3f7')
                }),
                clampToGround: true
            }
        });
        this._roadVizEntities.push(roadEntity);

        // Add road name label at midpoint
        const midIdx = Math.floor(numPoints / 2);
        const midNum = profile.min_num + midIdx * step;
        const midLon = profile.slope_lon * midNum + profile.intercept_lon;
        const midLat = profile.slope_lat * midNum + profile.intercept_lat;

        const labelEntity = viewer.entities.add({
            position: Cesium.Cartesian3.fromDegrees(midLon, midLat, 20),
            label: {
                text: `🛣️ ${roadName}`,
                font: 'bold 15px sans-serif',
                fillColor: Cesium.Color.WHITE,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                outlineWidth: 3,
                outlineColor: Cesium.Color.BLACK,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
            }
        });
        this._roadVizEntities.push(labelEntity);

        // Plot entities if loaded
        if (this._currentRoadEntities) {
            const allEnts = this._currentRoadEntities.all_entities || [];
            for (const e of allEnts) {
                if (!e.longitude || !e.latitude) continue;
                const isLeft = e.road_side === 'left';
                const color = isLeft
                    ? Cesium.Color.fromCssColorString('#66bb6a')
                    : Cesium.Color.fromCssColorString('#42a5f5');

                const ent = viewer.entities.add({
                    position: Cesium.Cartesian3.fromDegrees(e.longitude, e.latitude, 5),
                    point: { pixelSize: 8, color: color, outlineColor: Cesium.Color.WHITE, outlineWidth: 1 },
                    label: {
                        text: `#${e.building_main || e.bldg_main || ''}`,
                        font: '11px sans-serif',
                        fillColor: Cesium.Color.WHITE,
                        style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                        outlineWidth: 2,
                        pixelOffset: new Cesium.Cartesian2(0, -12),
                        scaleByDistance: new Cesium.NearFarScalar(200, 1.0, 2000, 0.3),
                    }
                });
                this._roadVizEntities.push(ent);
            }
        }

        // Fly to road midpoint
        viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(midLon, midLat, 1500),
            duration: 2.0
        });
    }

    /** Linear reference geocoding: estimate coordinates from road name + building number. */
    async roadGeocode() {
        const roadName = document.getElementById('rp-geocode-road').value.trim();
        const bldgMain = document.getElementById('rp-geocode-num').value.trim();
        const resultDiv = document.getElementById('rp-geocode-result');

        if (!roadName || !bldgMain) {
            resultDiv.innerHTML = '<p class="error">도로명과 건물본번을 입력하세요.</p>';
            return;
        }

        resultDiv.innerHTML = '<p class="loading">좌표 추정 중...</p>';

        try {
            const resp = await fetch(`/api/kg/geocode?road_name=${encodeURIComponent(roadName)}&building_main=${bldgMain}`);
            if (!resp.ok) {
                const e = await resp.json();
                throw new Error(e.detail || resp.statusText);
            }
            const data = await resp.json();

            const sideLabel = data.road_side === 'left' ? '좌측 (홀수)' : '우측 (짝수)';
            const r2Color = data.confidence.r_squared > 0.7 ? '#66bb6a' : data.confidence.r_squared > 0.3 ? '#ffa726' : '#ef5350';
            const inRangeLabel = data.confidence.in_range ? '✅ 범위 내' : '⚠️ 범위 밖 (외삽)';

            let html = `<div class="rp-geocode-card">
                <div class="rp-geocode-header">📍 추정 결과: ${roadName} ${bldgMain}</div>
                <div class="rp-geocode-grid">
                    <div class="rp-geocode-item">
                        <span class="rp-geocode-label">경도</span>
                        <span class="rp-geocode-val">${data.estimated_lon?.toFixed(6) || '—'}</span>
                    </div>
                    <div class="rp-geocode-item">
                        <span class="rp-geocode-label">위도</span>
                        <span class="rp-geocode-val">${data.estimated_lat?.toFixed(6) || '—'}</span>
                    </div>
                    <div class="rp-geocode-item">
                        <span class="rp-geocode-label">도로 측면</span>
                        <span class="rp-geocode-val">${sideLabel}</span>
                    </div>
                    <div class="rp-geocode-item">
                        <span class="rp-geocode-label">기점 거리</span>
                        <span class="rp-geocode-val">${data.distance_from_start_m?.toFixed(1) || '—'}m</span>
                    </div>
                    <div class="rp-geocode-item">
                        <span class="rp-geocode-label">모델 적합도</span>
                        <span class="rp-geocode-val" style="color:${r2Color}">R²=${data.confidence.r_squared?.toFixed(4) || '—'}</span>
                    </div>
                    <div class="rp-geocode-item">
                        <span class="rp-geocode-label">범위 판정</span>
                        <span class="rp-geocode-val">${inRangeLabel}</span>
                    </div>
                </div>`;

            // Nearby entities
            if (data.nearby_entities && data.nearby_entities.length > 0) {
                html += `<div class="rp-geocode-nearby"><strong>📌 인근 엔티티 (${data.nearby_entities.length}개):</strong>`;
                for (const ne of data.nearby_entities.slice(0, 8)) {
                    html += `<div class="rp-nearby-item" onclick="kgAnalysis._flyToEntity(${ne.longitude}, ${ne.latitude}, '${(ne.name || '').replace(/'/g, "\\'")}')">
                        ${ne.name || ne.gsid || ne.uid} <span class="rp-nearby-dist">${ne.distance_m?.toFixed(0) || '?'}m</span>
                    </div>`;
                }
                html += `</div>`;
            }

            html += `<button class="btn btn-accent rp-fly-btn" onclick="kgAnalysis._flyToEntity(${data.estimated_lon}, ${data.estimated_lat}, '${roadName} ${bldgMain}')">🗺️ 지도에서 보기</button>`;
            html += `</div>`;
            resultDiv.innerHTML = html;
        } catch (e) {
            resultDiv.innerHTML = `<p class="error">${e.message}</p>`;
        }
    }
}

// Global instance for onclick handlers
let kgAnalysis;
