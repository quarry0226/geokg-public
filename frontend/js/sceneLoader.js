/**
 * SceneLoader - Loads scene data from the API and adds entities to Cesium viewer.
 * Buildings: KG-driven color overlays (extruded polygons per usage type).
 * Other entities: individual Cesium entities from scene API.
 */
class SceneLoader {
    constructor(cesiumManager) {
        this.cm = cesiumManager;
    }

    async loadScene() {
        try {
            // Step 1: Load building color overlays from KG (no 3D Tiles - use KG data directly)
            console.log('[SceneLoader] Loading building color overlays from KG...');
            const colorResp = await fetch('/api/kg/building_colors');
            const colorData = await colorResp.json();
            const bldgCount = this.cm.addBuildingColorOverlays(colorData.buildings);
            console.log(`[SceneLoader] ${bldgCount} building overlays loaded`);

            // Step 2: Load non-building entities from API
            console.log('[SceneLoader] Loading non-building entities...');
            const response = await fetch('/api/scene/models?exclude_label=Building');
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const models = await response.json();

            let count = 0;
            for (const model of models) {
                this._addModelToScene(model);
                count++;
            }

            // Step 3: Load parcel boundaries (async, non-blocking)
            this._loadParcels();

            const el = document.getElementById('entity-count');
            if (el) el.textContent = `${bldgCount + count} entities`;

            console.log(`[SceneLoader] Loaded ${count} non-building entities + ${bldgCount} buildings`);
            return count + bldgCount;
        } catch (err) {
            console.error('[SceneLoader] Failed to load scene:', err);
            return 0;
        }
    }

    async _loadParcels() {
        // Initialize viewport-based parcel loading (loads on-demand as camera moves)
        this.cm.initParcelViewport();
        console.log('[SceneLoader] Parcel viewport system initialized (loads on zoom-in)');
    }

    _addModelToScene(model) {
        const label = model.label;
        const addFunctions = {
            'Road':             (d) => this.cm.addRoad(d),
            'AutoRoadLink':     (d) => this.cm.addAutoRoadLink(d),
            'Vehicle':          (d) => this.cm.addVehicle(d),
            'Sensor':           (d) => this.cm.addSensor(d),
            'Camera':           (d) => this.cm.addCamera(d),
            'Tree':             (d) => this.cm.addTree(d),
            'ParkingLot':       (d) => this.cm.addParkingLot(d),
            'Facility':         (d) => this.cm.addFacility(d),
            'ParkingSpace':     (d) => this.cm.addParkingSpace(d),
            'ThingsAddr':       (d) => this.cm.addThingsAddr(d),
            'RoadIntersection': (d) => this.cm.addIntersection(d),
        };

        const fn = addFunctions[label];
        if (fn) {
            fn(model);
        } else if (model.properties && model.properties.longitude && model.properties.latitude) {
            this.cm.registerEntityPosition(
                model.properties.uid,
                model.properties.longitude,
                model.properties.latitude,
                5
            );
        }
    }

    applyDynamicUpdate(event) {
        if (event.vehicles) {
            for (const v of event.vehicles) {
                this.cm.updateVehiclePosition(v.uid, v.longitude, v.latitude, v.heading);
            }
        }
        if (event.parking) {
            for (const p of event.parking) {
                this.cm.updateParkingLot(p.uid, p.occupied, p.capacity);
            }
        }
        if (event.environment) {
            for (const s of event.environment) {
                this.cm.updateSensorValue(s.uid, s.value, '');
            }
        }
    }
}
