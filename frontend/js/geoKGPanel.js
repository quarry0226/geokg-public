/**
 * GeoKGPanel - Knowledge Graph visualization sidebar.
 * Shows graph stats, entity list, and relationships.
 */
class GeoKGPanel {
    constructor(cesiumManager) {
        this.cm = cesiumManager;
        this.allNodes = [];
    }

    async loadStats() {
        try {
            const resp = await fetch('/api/geokg/stats');
            const stats = await resp.json();
            const el = document.getElementById('kg-stats-content');
            el.innerHTML = `
                <div class="kg-stat-row"><span>Nodes</span><span class="kg-stat-value">${stats.node_count}</span></div>
                <div class="kg-stat-row"><span>Relationships</span><span class="kg-stat-value">${stats.relationship_count}</span></div>
                <div class="kg-stat-row"><span>Labels</span><span class="kg-stat-value">${stats.labels.length}</span></div>
                <div class="kg-stat-row"><span>Rel Types</span><span class="kg-stat-value">${stats.relationship_types.length}</span></div>
            `;
        } catch (err) {
            document.getElementById('kg-stats-content').textContent = 'Error loading stats';
        }
    }

    async loadEntities(filterLabel) {
        try {
            const url = filterLabel ? `/api/geokg/nodes?label=${filterLabel}` : '/api/geokg/nodes';
            const resp = await fetch(url);
            this.allNodes = await resp.json();
            this._renderEntityList(this.allNodes);
        } catch (err) {
            document.getElementById('entity-list').textContent = 'Error loading entities';
        }
    }

    async loadRelationships() {
        try {
            const resp = await fetch('/api/geokg/relationships?limit=50');
            const rels = await resp.json();
            const el = document.getElementById('relationship-list');
            el.innerHTML = rels.map(r =>
                `<div class="rel-item">
                    <span>${r.from_uid}</span>
                    <span class="rel-type"> ${r.rel_type} </span>
                    <span>${r.to_uid}</span>
                </div>`
            ).join('');
        } catch (err) {
            document.getElementById('relationship-list').textContent = 'Error loading relationships';
        }
    }

    _renderEntityList(nodes) {
        const el = document.getElementById('entity-list');
        el.innerHTML = nodes.map(n => {
            const label = n.label;
            const name = n.name || n.iot_type_name || n.jibun || n.plate || n.sensor_type || n.uid;
            const gsidShort = n.gsid ? `<span class="entity-gsid">${n.gsid}</span>` : '';
            return `<div class="entity-item" data-uid="${n.uid}" data-label="${label}">
                <div><span>${name}</span>${gsidShort}</div>
                <span class="entity-label label-${label}">${label}</span>
            </div>`;
        }).join('');

        // Click handlers: single-click = select + flyTo, double-click = close zoom
        // Use a timer to distinguish single-click from double-click
        let clickTimer = null;
        el.querySelectorAll('.entity-item').forEach(item => {
            item.addEventListener('click', () => {
                // Remove previous selection
                el.querySelectorAll('.entity-item.selected').forEach(s => s.classList.remove('selected'));
                item.classList.add('selected');

                const uid = item.dataset.uid;
                this._loadEntityInfo(uid);

                // Show connections on 3D map
                this.cm.showConnections(uid);

                // Delay flyTo so double-click can cancel it
                if (clickTimer) clearTimeout(clickTimer);
                clickTimer = setTimeout(() => {
                    this.cm.highlightEntity(uid);
                    clickTimer = null;
                }, 250);
            });
            item.addEventListener('dblclick', () => {
                // Cancel the single-click flyTo
                if (clickTimer) { clearTimeout(clickTimer); clickTimer = null; }
                const uid = item.dataset.uid;
                this.cm.flyToEntityClose(uid);
            });
        });
    }

    async _loadEntityInfo(uid) {
        try {
            const resp = await fetch(`/api/geokg/nodes/${uid}`);
            const node = await resp.json();

            // Also load neighbors
            const nResp = await fetch(`/api/geokg/nodes/${uid}/neighbors`);
            const neighbors = await nResp.json();

            const infoEl = document.getElementById('entity-info-content');
            let html = '';

            // Show GSID prominently at top if present
            if (node.gsid) {
                html += `<div class="info-row gsid-row"><span class="info-key">GSID</span><span class="info-value gsid-value">${node.gsid}</span></div>`;
            }
            if (node.subtype) {
                html += `<div class="info-row"><span class="info-key">SubType</span><span class="info-value">${node.subtype}</span></div>`;
            }

            for (const [key, val] of Object.entries(node)) {
                if (key === 'uid' || key === 'coordinates' || key === 'boundary') continue;
                if (key === 'gsid' || key === 'subtype') continue;  // already shown above
                if (val === null || val === undefined || val === '') continue;
                html += `<div class="info-row"><span class="info-key">${key}</span><span class="info-value">${val}</span></div>`;
            }

            if (neighbors.length > 0) {
                // Group by rel_type for organized display
                const grouped = {};
                for (const n of neighbors) {
                    if (!grouped[n.rel_type]) grouped[n.rel_type] = [];
                    grouped[n.rel_type].push(n);
                }
                const typeCount = Object.keys(grouped).length;
                const totalCount = neighbors.length;
                html += `<h3 style="margin-top:10px;font-size:11px;color:#607d8b;">연결 관계 (${totalCount}개, ${typeCount}종)</h3>`;

                for (const [relType, items] of Object.entries(grouped)) {
                    html += `<div style="margin-top:4px;font-size:10px;color:#4fc3f7;font-weight:bold;">${relType} (${items.length})</div>`;
                    for (const n of items.slice(0, 5)) {
                        const name = n.name || n.iot_type_name || n.jibun || n.uid;
                        const labelBadge = `<span class="entity-label label-${n.label}" style="font-size:8px;padding:1px 4px;">${n.label}</span>`;
                        html += `<div class="info-row neighbor-item" data-uid="${n.uid}" style="cursor:pointer;" title="클릭: 이동">
                            <span class="info-key">${labelBadge}</span>
                            <span class="info-value" style="font-size:10px;">${name}</span>
                        </div>`;
                    }
                    if (items.length > 5) {
                        html += `<div style="font-size:9px;color:#607d8b;padding-left:8px;">... +${items.length - 5}개 더</div>`;
                    }
                }
            }

            infoEl.innerHTML = html || 'No data';

            // Make neighbor items clickable (fly to neighbor entity)
            infoEl.querySelectorAll('.neighbor-item').forEach(item => {
                item.addEventListener('click', () => {
                    const neighborUid = item.dataset.uid;
                    this.cm.highlightEntity(neighborUid);
                    this.cm.showConnections(neighborUid);
                    this._loadEntityInfo(neighborUid);
                });
            });
        } catch (err) {
            document.getElementById('entity-info-content').textContent = 'Error loading info';
        }
    }

    init() {
        // Filter dropdown handler
        document.getElementById('entity-filter').addEventListener('change', (e) => {
            this.loadEntities(e.target.value);
        });

        // Load initial data
        this.loadStats();
        this.loadEntities();
        this.loadRelationships();
    }
}
