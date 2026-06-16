/**
 * CesiumManager - Manages the Cesium 3D viewer.
 * Handles viewer initialization, camera, and entity management.
 */
class CesiumManager {
    constructor(containerId) {
        this.containerId = containerId;
        this.viewer = null;
        this.entities = {};       // uid -> Cesium.Entity
        this.entityPositions = {}; // uid -> Cesium.Cartesian3 (center point for all entities)
        this.uidToGsid = {};      // uid -> gsid (for entity picker / path finder)
        this.roadPolylines = {};  // uid -> [[lon,lat], ...] (road polyline coordinates for nearest-point)
        this.layerVisibility = {
            Building: true, Road: true, Vehicle: true,
            Sensor: true, Camera: true, Tree: true,
            Facility: true, ParkingLot: true, ThingsAddr: true,
        };
        this.iotSubTypeVisibility = {
            BUSST: true, CoolingCen: true, CHPARK: true,
            CivilDefense: true, ChPlayground: true, EQOUT: true,
            TAXIST: true, SCPARK: true, SLEEPRA: true,
        };
        this._fpsFrames = 0;
        this._fpsTime = performance.now();
        this._fps = 0;
        this.tileset = null;
        this._highlightEntity = null;
        this._connectionDS = null;
        this._connectionInfoEl = null;
    }

    init(cesiumToken) {
        // Suppress Ion token warning (we use free tile providers)
        Cesium.Ion.defaultAccessToken = undefined;

        // CartoDB Dark Matter - free, CORS-enabled, matches dark UI theme
        const darkMapProvider = new Cesium.UrlTemplateImageryProvider({
            url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}.png',
            subdomains: ['a', 'b', 'c', 'd'],
            credit: new Cesium.Credit('Map tiles by CartoDB, under CC BY 3.0. Data by OpenStreetMap, under ODbL.'),
        });

        this.viewer = new Cesium.Viewer(this.containerId, {
            baseLayerPicker: false,
            geocoder: false,
            homeButton: false,
            sceneModePicker: false,
            navigationHelpButton: false,
            animation: false,
            timeline: false,
            fullscreenButton: false,
            vrButton: false,
            infoBox: true,
            selectionIndicator: true,
            // Cesium 1.107+: imageryProvider removed, use baseLayer
            baseLayer: new Cesium.ImageryLayer(darkMapProvider),
        });

        this.viewer.scene.globe.depthTestAgainstTerrain = false;

        // FPS tracking
        this.viewer.scene.postRender.addEventListener(() => {
            this._fpsFrames++;
            const now = performance.now();
            if (now - this._fpsTime >= 1000) {
                this._fps = this._fpsFrames;
                this._fpsFrames = 0;
                this._fpsTime = now;
                const el = document.getElementById('fps-counter');
                if (el) el.textContent = this._fps + ' FPS';
            }
        });

        // Connection visualization DataSource
        this._connectionDS = new Cesium.CustomDataSource('connections');
        this.viewer.dataSources.add(this._connectionDS);

        // Connection info overlay element
        this._connectionInfoEl = document.createElement('div');
        this._connectionInfoEl.id = 'connection-info';
        this._connectionInfoEl.style.cssText = `
            display: none; position: absolute; bottom: 45px; left: 50%;
            transform: translateX(-50%); padding: 6px 14px;
            background: rgba(0,0,0,0.85); color: #ccc; font-size: 11px;
            border-radius: 6px; border: 1px solid rgba(79,195,247,0.3);
            z-index: 60; white-space: nowrap; pointer-events: none;
        `;
        this.viewer.container.appendChild(this._connectionInfoEl);

        // Click handler (supports both entities and 3D Tile features)
        const handler = new Cesium.ScreenSpaceEventHandler(this.viewer.scene.canvas);
        handler.setInputAction((click) => {
            const picked = this.viewer.scene.pick(click.position);
            if (Cesium.defined(picked)) {
                // Entity click (buildings, sensors, vehicles, etc.)
                if (picked.id && picked.id._uid) {
                    if (this.onEntityClick) {
                        this.onEntityClick(picked.id._uid, picked.id._label);
                    }
                }
            } else {
                // Clicked empty space - clear connections
                this.clearConnections();
            }
        }, Cesium.ScreenSpaceEventType.LEFT_CLICK);
    }

    flyToCenter(lon, lat, height) {
        this.viewer.camera.flyTo({
            destination: Cesium.Cartesian3.fromDegrees(lon, lat, height || 1500),
            orientation: {
                heading: Cesium.Math.toRadians(15),
                pitch: Cesium.Math.toRadians(-45),
                roll: 0,
            },
            duration: 2,
        });
    }

    // ===== Buildings: extruded polygon =====
    addBuilding(data) {
        const props = data.properties;
        const style = data.style || {};
        const baseColor = Cesium.Color.fromCssColorString(style.color || '#4A90D9');
        const alpha = style.alpha || 0.9;
        const height = props.height || 20;

        // Rotate building footprint to align with Manhattan grid (~29° from north)
        const headingRad = (props.heading || 0) * Math.PI / 180;
        const lon = props.longitude;
        const lat = props.latitude;

        // Convert meters to degrees (accounting for latitude)
        const mPerDegLat = 111320;
        const mPerDegLon = 111320 * Math.cos(lat * Math.PI / 180);
        const w2m = (props.width || 30) / 2;   // half-width in meters
        const d2m = (props.depth || 20) / 2;   // half-depth in meters

        // Compute 4 corners: rotate in meters, then convert to degree offsets
        const cosH = Math.cos(headingRad);
        const sinH = Math.sin(headingRad);
        // Local corners in meters (x=east, y=north)
        const cornersM = [
            [-w2m, -d2m], [w2m, -d2m], [w2m, d2m], [-w2m, d2m]
        ];
        const rotatedCorners = cornersM.map(([ex, ny]) => {
            // Rotate: heading is clockwise from north
            const rx = ex * cosH + ny * sinH;   // rotated east
            const ry = -ex * sinH + ny * cosH;  // rotated north
            return [
                lon + rx / mPerDegLon,
                lat + ry / mPerDegLat,
            ];
        });

        const centerPos = Cesium.Cartesian3.fromDegrees(lon, lat, height / 2);

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.name || props.uid,
            position: centerPos,
            polygon: {
                hierarchy: Cesium.Cartesian3.fromDegreesArray([
                    rotatedCorners[0][0], rotatedCorners[0][1],
                    rotatedCorners[1][0], rotatedCorners[1][1],
                    rotatedCorners[2][0], rotatedCorners[2][1],
                    rotatedCorners[3][0], rotatedCorners[3][1],
                ]),
                extrudedHeight: height,
                height: 0,
                material: baseColor.withAlpha(alpha),
                outline: true,
                outlineColor: Cesium.Color.fromCssColorString('#222').withAlpha(0.8),
                closeTop: true,
                closeBottom: true,
            },
            label: {
                text: props.name || '',
                font: 'bold 15px sans-serif',
                fillColor: Cesium.Color.WHITE,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                outlineWidth: 3,
                outlineColor: Cesium.Color.BLACK,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                pixelOffset: new Cesium.Cartesian2(0, -5),
                eyeOffset: new Cesium.Cartesian3(0, 0, -(height + 10)),
                distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 2000),
                showBackground: true,
                backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                backgroundPadding: new Cesium.Cartesian2(7, 4),
                scaleByDistance: new Cesium.NearFarScalar(50, 1.4, 1500, 0.6),
            },
            description: this._buildDescription(props, 'Building'),
        });
        entity._uid = props.uid;
        entity._label = 'Building';
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = Cesium.Cartesian3.fromDegrees(lon, lat, height + 5);
        return entity;
    }

    // ===== Roads (TL_SPRD_MANAGE, admin / road-name network) =====
    // Two-layer rendering complements the AutoRoadLink corridor:
    //   1) AutoRoadLink (addAutoRoadLink) draws the *physical* carriageway
    //      face from TN_RODWAY_LINK width_m as a ground-clamped corridor.
    //   2) Road (this function) overlays the *administrative / named-road*
    //      centerline from TL_SPRD_MANAGE on top, so the road-name network
    //      structure stays visible on top of the asphalt surface.
    addRoad(data) {
        const props = data.properties;
        let coords;
        try {
            coords = typeof props.coordinates === 'string' ?
                JSON.parse(props.coordinates) : props.coordinates;
        } catch { return; }
        if (!coords || !coords.length) return;

        const rtype = props.road_type || 'secondary';
        const widthPx = rtype === 'main' ? 5 : rtype === 'secondary' ? 3 : 2;
        const roadColors = { main: '#4D8FE0', secondary: '#3A7BD5', path: '#2A5DB0' };
        const roadColor = Cesium.Color.fromCssColorString(roadColors[rtype] || '#3A7BD5');

        const positions = Cesium.Cartesian3.fromDegreesArray(
            coords.flatMap(c => [c[0], c[1]])
        );

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.name || props.uid,
            polyline: {
                positions: positions,
                width: widthPx,
                material: new Cesium.PolylineOutlineMaterialProperty({
                    color: roadColor.withAlpha(0.85),
                    outlineWidth: 1,
                    outlineColor: Cesium.Color.fromCssColorString('#0a1228').withAlpha(0.7),
                }),
                clampToGround: true,
                // Higher zIndex ensures the admin centerline draws above
                // ground-clamped corridor faces from AutoRoadLink, which
                // share the same z=0 plane.
                zIndex: 10,
            },
            description: this._buildDescription(props, 'Road'),
        });

        entity._uid = props.uid;
        entity._label = 'Road';
        this.entities[props.uid] = entity;
        if (props.gsid) this.uidToGsid[props.uid] = props.gsid;

        // Store polyline coordinates for perpendicular-foot connection lines
        this.roadPolylines[props.uid] = coords;

        // Store center position for relationship lines (fallback)
        const midIdx = Math.floor(coords.length / 2);
        this.entityPositions[props.uid] = Cesium.Cartesian3.fromDegrees(
            coords[midIdx][0], coords[midIdx][1], 1
        );
    }

    // ===== AutoRoadLink (TN_RODWAY_LINK): corridor with real carriageway width =====
    // Renders each physical road link as a ground-clamped corridor whose
    // metric width is the published TN_RODWAY_LINK width_m. road_class drives
    // the asphalt shade so highways read darker than residential streets.
    addAutoRoadLink(data) {
        const props = data.properties;
        let coords;
        try {
            coords = typeof props.geometry === 'string' ?
                JSON.parse(props.geometry) : props.geometry;
        } catch { return; }
        if (!coords || coords.length < 2) return;

        // KAIS / MoLIT width_m semantics: full carriageway width in metres.
        // Fall back to 6 m (typical sigungu road) when the source row omits it.
        const widthM = (typeof props.width_m === 'number' && props.width_m > 0)
            ? props.width_m
            : 6;

        // road_class colour ramp — darker = larger / higher-tier road.
        // Values come straight from RDC001--014 codes mapped in the loader.
        const colorByClass = {
            'highway':       '#2A2A2E',
            'urban_highway': '#2F2F35',
            'national':      '#363640',
            'metropolitan':  '#3A3A45',
            'provincial':    '#40404A',
            'county':        '#48484F',
            'residential':   '#52525A',
        };
        const cssColor = colorByClass[props.road_class] || '#48484F';
        const asphalt = Cesium.Color.fromCssColorString(cssColor).withAlpha(0.92);

        const positions = Cesium.Cartesian3.fromDegreesArray(
            coords.flatMap(c => [c[0], c[1]])
        );

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.road_name || props.uid,
            corridor: {
                positions: positions,
                width: widthM,
                cornerType: Cesium.CornerType.ROUNDED,
                material: asphalt,
                outline: false,
                clampToGround: true,
            },
            description: this._buildDescription(props, 'AutoRoadLink'),
        });
        entity._uid = props.uid;
        entity._label = 'AutoRoadLink';
        this.entities[props.uid] = entity;
        if (props.gsid) this.uidToGsid[props.uid] = props.gsid;

        // Midpoint position for relationship-line endpoints
        const midIdx = Math.floor(coords.length / 2);
        this.entityPositions[props.uid] = Cesium.Cartesian3.fromDegrees(
            coords[midIdx][0], coords[midIdx][1], 1
        );
    }

    // ===== Vehicles =====
    addVehicle(data) {
        const props = data.properties;
        const style = data.style || {};
        const vtype = props.vehicle_type || 'car';
        const size = vtype === 'car' ? 8 : vtype === 'bus' ? 12 : 10;
        const color = Cesium.Color.fromCssColorString(style.color || '#3498DB');
        const pos = Cesium.Cartesian3.fromDegrees(props.longitude, props.latitude, 5);

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.plate || props.uid,
            position: pos,
            point: {
                pixelSize: size,
                color: color,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 2,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            label: {
                text: props.plate || '',
                font: 'bold 13px monospace',
                fillColor: Cesium.Color.WHITE,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                outlineWidth: 3,
                outlineColor: Cesium.Color.BLACK,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                pixelOffset: new Cesium.Cartesian2(0, -12),
                distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 400),
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
                showBackground: true,
                backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                backgroundPadding: new Cesium.Cartesian2(6, 3),
                scaleByDistance: new Cesium.NearFarScalar(30, 1.4, 400, 0.6),
            },
            description: this._buildDescription(props, 'Vehicle'),
        });
        entity._uid = props.uid;
        entity._label = 'Vehicle';
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = pos;
    }

    // ===== Sensors =====
    addSensor(data) {
        const props = data.properties;
        const style = data.style || {};
        const color = Cesium.Color.fromCssColorString(style.color || '#9BC53D');
        const unit = props.unit || '';
        const displayValue = `${props.value}${unit}`;
        const pos = Cesium.Cartesian3.fromDegrees(props.longitude, props.latitude, 8);

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: `${props.sensor_type}: ${displayValue}`,
            position: pos,
            billboard: {
                image: this._createSensorIcon(style.color || '#9BC53D', props.sensor_type),
                width: 22,
                height: 22,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            label: {
                text: displayValue,
                font: 'bold 14px sans-serif',
                fillColor: color,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                outlineWidth: 3,
                outlineColor: Cesium.Color.BLACK,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                pixelOffset: new Cesium.Cartesian2(0, -26),
                distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 1000),
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
                showBackground: true,
                backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                backgroundPadding: new Cesium.Cartesian2(6, 3),
                scaleByDistance: new Cesium.NearFarScalar(50, 1.4, 800, 0.6),
            },
            description: this._buildDescription(props, 'Sensor'),
        });
        entity._uid = props.uid;
        entity._label = 'Sensor';
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = pos;
    }

    // ===== Cameras =====
    addCamera(data) {
        const props = data.properties;
        const style = data.style || {};
        const color = style.color || '#2ECC71';
        const pos = Cesium.Cartesian3.fromDegrees(
            props.longitude, props.latitude, (props.altitude || 8) + 5
        );

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.name || props.uid,
            position: pos,
            billboard: {
                image: this._createCameraIcon(color),
                width: 22,
                height: 22,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            label: {
                text: props.name || '',
                font: 'bold 14px sans-serif',
                fillColor: Cesium.Color.fromCssColorString(color),
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                outlineWidth: 3,
                outlineColor: Cesium.Color.BLACK,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                pixelOffset: new Cesium.Cartesian2(0, -26),
                distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 800),
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
                showBackground: true,
                backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                backgroundPadding: new Cesium.Cartesian2(6, 3),
                scaleByDistance: new Cesium.NearFarScalar(50, 1.4, 600, 0.6),
            },
            description: this._buildDescription(props, 'Camera'),
        });
        entity._uid = props.uid;
        entity._label = 'Camera';
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = pos;
    }

    // ===== Trees =====
    addTree(data) {
        const props = data.properties;
        const h = props.height || 8;
        const pos = Cesium.Cartesian3.fromDegrees(props.longitude, props.latitude, h * 0.6);

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.species || 'Tree',
            position: pos,
            ellipsoid: {
                radii: new Cesium.Cartesian3(h * 0.25, h * 0.25, h * 0.4),
                material: Cesium.Color.fromCssColorString('#2E7D32').withAlpha(0.75),
            },
            description: this._buildDescription(props, 'Tree'),
        });
        entity._uid = props.uid;
        entity._label = 'Tree';
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = pos;
    }

    // ===== Parking Lots =====
    addParkingLot(data) {
        const props = data.properties;
        const style = data.style || {};
        const color = Cesium.Color.fromCssColorString(style.color || '#2ECC71');

        const w = 40 / 111000;
        const h = 25 / 111000;
        const pos = Cesium.Cartesian3.fromDegrees(props.longitude, props.latitude, 3);

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.name || props.uid,
            position: pos,
            polygon: {
                hierarchy: Cesium.Cartesian3.fromDegreesArray([
                    props.longitude - w / 2, props.latitude - h / 2,
                    props.longitude + w / 2, props.latitude - h / 2,
                    props.longitude + w / 2, props.latitude + h / 2,
                    props.longitude - w / 2, props.latitude + h / 2,
                ]),
                height: 0.5,
                extrudedHeight: 1.5,
                material: color.withAlpha(0.25),
                outline: true,
                outlineColor: color.withAlpha(0.9),
            },
            label: {
                text: `${props.name}\n${style.label || ''}`,
                font: 'bold 15px sans-serif',
                fillColor: Cesium.Color.WHITE,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                outlineWidth: 3,
                outlineColor: Cesium.Color.BLACK,
                verticalOrigin: Cesium.VerticalOrigin.CENTER,
                distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 1500),
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
                showBackground: true,
                backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                backgroundPadding: new Cesium.Cartesian2(7, 4),
                scaleByDistance: new Cesium.NearFarScalar(50, 1.4, 1200, 0.6),
            },
            description: this._buildDescription(props, 'ParkingLot'),
        });
        entity._uid = props.uid;
        entity._label = 'ParkingLot';
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = pos;
    }

    // ===== Facilities (lamps, benches, signs, gates) =====
    addFacility(data) {
        const props = data.properties;
        const pos = Cesium.Cartesian3.fromDegrees(props.longitude, props.latitude, 4);
        const ftype = props.facility_type || 'lamp';
        const colors = { lamp: '#FFD54F', bench: '#8D6E63', sign: '#42A5F5', gate: '#78909C' };
        const color = Cesium.Color.fromCssColorString(colors[ftype] || '#AAAAAA');

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.name || ftype,
            position: pos,
            point: {
                pixelSize: 6,
                color: color,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 1,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            description: this._buildDescription(props, 'Facility'),
        });
        entity._uid = props.uid;
        entity._label = 'Facility';
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = pos;
    }

    // ===== Parking Spaces =====
    addParkingSpace(data) {
        const props = data.properties;
        const pos = Cesium.Cartesian3.fromDegrees(props.longitude, props.latitude, 1);
        const occupied = props.is_occupied;
        const color = occupied ? Cesium.Color.RED.withAlpha(0.7) : Cesium.Color.LIME.withAlpha(0.7);

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: `Space ${props.space_number || ''}`,
            position: pos,
            point: {
                pixelSize: 5,
                color: color,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 1,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            description: this._buildDescription(props, 'ParkingSpace'),
        });
        entity._uid = props.uid;
        entity._label = 'ParkingSpace';
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = pos;
    }

    // ===== IoT Address (사물주소) =====
    addThingsAddr(data) {
        const props = data.properties;
        if (!props.longitude || !props.latitude) return;
        const pos = Cesium.Cartesian3.fromDegrees(props.longitude, props.latitude, 6);

        const iotType = props.iot_type || '';
        const typeColors = {
            BUSST: '#29b6f6', CoolingCen: '#ff7043', CHPARK: '#66bb6a',
            CivilDefense: '#ab47bc', ChPlayground: '#26c6da', EQOUT: '#ef5350',
            TAXIST: '#ffa726', SCPARK: '#9ccc65', SLEEPRA: '#8d6e63',
        };
        const typeSymbols = {
            BUSST: 'B', CoolingCen: 'C', CHPARK: 'P',
            CivilDefense: 'D', ChPlayground: 'G', EQOUT: 'E',
            TAXIST: 'T', SCPARK: 'S', SLEEPRA: 'R',
        };
        const color = typeColors[iotType] || '#78909C';
        const symbol = typeSymbols[iotType] || '?';

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.name || props.iot_type_name || props.uid,
            position: pos,
            billboard: {
                image: this._createIoTIcon(color, symbol),
                width: 22,
                height: 22,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
            label: {
                text: props.iot_type_name || iotType,
                font: 'bold 14px sans-serif',
                fillColor: Cesium.Color.fromCssColorString(color),
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                outlineWidth: 3,
                outlineColor: Cesium.Color.BLACK,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                pixelOffset: new Cesium.Cartesian2(0, -26),
                distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 600),
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
                showBackground: true,
                backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                backgroundPadding: new Cesium.Cartesian2(6, 3),
                scaleByDistance: new Cesium.NearFarScalar(50, 1.4, 500, 0.6),
            },
            description: this._buildDescription(props, 'ThingsAddr'),
        });
        entity._uid = props.uid;
        entity._label = 'ThingsAddr';
        entity._iotType = iotType;
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = pos;
    }

    // ===== Road Intersections (교차로) =====
    // Ground-clamped point so the marker sits on the carriageway surface
    // rather than floating at an arbitrary altitude above terrain.
    addIntersection(data) {
        const props = data.properties;
        if (!props.longitude || !props.latitude) return;
        const pos = Cesium.Cartesian3.fromDegrees(props.longitude, props.latitude, 0);

        const entity = this.viewer.entities.add({
            id: props.uid,
            name: props.name || '교차로',
            position: pos,
            point: {
                pixelSize: 7,
                color: Cesium.Color.ORANGE,
                outlineColor: Cesium.Color.WHITE,
                outlineWidth: 1.5,
                heightReference: Cesium.HeightReference.CLAMP_TO_GROUND,
                // disableDepthTestDistance intentionally omitted so the marker
                // is occluded by buildings / overpasses at oblique angles.
            },
            label: {
                text: props.name || '',
                font: 'bold 11px sans-serif',
                fillColor: Cesium.Color.ORANGE,
                style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                outlineWidth: 2,
                outlineColor: Cesium.Color.BLACK,
                verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                pixelOffset: new Cesium.Cartesian2(0, -10),
                distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 800),
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
                showBackground: true,
                backgroundColor: Cesium.Color.BLACK.withAlpha(0.5),
                backgroundPadding: new Cesium.Cartesian2(5, 3),
                scaleByDistance: new Cesium.NearFarScalar(50, 1.2, 800, 0.5),
            },
            description: this._buildDescription(props, 'RoadIntersection'),
        });
        entity._uid = props.uid;
        entity._label = 'RoadIntersection';
        this.entities[props.uid] = entity; if (props.gsid) this.uidToGsid[props.uid] = props.gsid;
        this.entityPositions[props.uid] = pos;
    }

    // ===== Zones: boundary polygon outline =====
    addZone(uid, name, zoneType, boundaryCoords) {
        // Zone polygon rendering disabled - only register centroid position
        if (!boundaryCoords || boundaryCoords.length < 3) return;
        let sumLon = 0, sumLat = 0;
        for (const c of boundaryCoords) { sumLon += c[0]; sumLat += c[1]; }
        this.entityPositions[uid] = Cesium.Cartesian3.fromDegrees(
            sumLon / boundaryCoords.length, sumLat / boundaryCoords.length, 10
        );
    }

    // ===== Register position for non-renderable entities (State, etc.) =====
    registerEntityPosition(uid, lon, lat, height) {
        this.entityPositions[uid] = Cesium.Cartesian3.fromDegrees(lon, lat, height || 5);
    }

    // ===== Dynamic Updates =====
    updateVehiclePosition(uid, lon, lat, heading) {
        const entity = this.entities[uid];
        if (entity) {
            const pos = Cesium.Cartesian3.fromDegrees(lon, lat, 5);
            entity.position = pos;
            this.entityPositions[uid] = pos;
        }
    }

    updateSensorValue(uid, value, unit) {
        const entity = this.entities[uid];
        if (entity && entity.label) {
            const text = `${value}${unit || ''}`;
            entity.label.text = text;
            entity.name = `Sensor: ${text}`;
        }
    }

    updateParkingLot(uid, occupied, capacity) {
        const entity = this.entities[uid];
        if (entity && entity.label) {
            const name = entity.name || '';
            entity.label.text = `${name}\n${occupied}/${capacity}`;
        }
    }

    // ===== Layer Visibility =====
    setLayerVisibility(label, visible) {
        this.layerVisibility[label] = visible;
        for (const [uid, entity] of Object.entries(this.entities)) {
            if (entity._label === label) {
                // For ThingsAddr, also check sub-type visibility
                if (label === 'ThingsAddr' && entity._iotType) {
                    entity.show = visible && (this.iotSubTypeVisibility[entity._iotType] !== false);
                } else {
                    entity.show = visible;
                }
            }
        }
    }

    /**
     * Toggle visibility of a specific ThingsAddr sub-type.
     * @param {string} iotType - IoT type code (e.g., 'BUSST', 'CoolingCen')
     * @param {boolean} visible - Whether to show or hide
     */
    setIoTSubTypeVisibility(iotType, visible) {
        this.iotSubTypeVisibility[iotType] = visible;
        const layerVisible = this.layerVisibility['ThingsAddr'] !== false;
        for (const [uid, entity] of Object.entries(this.entities)) {
            if (entity._label === 'ThingsAddr' && entity._iotType === iotType) {
                entity.show = layerVisible && visible;
            }
        }
    }

    // ===== Entity Highlight (single-click: fly to overview) =====
    highlightEntity(uid) {
        const entity = this.entities[uid];
        if (entity) {
            this.viewer.selectedEntity = entity;
        }
        const pos = this.entityPositions[uid];
        if (pos) {
            // Use flyToBoundingSphere so the entity is exactly at screen center
            const sphere = new Cesium.BoundingSphere(pos, 0);
            this.viewer.camera.flyToBoundingSphere(sphere, {
                offset: new Cesium.HeadingPitchRange(
                    Cesium.Math.toRadians(0),   // heading: north
                    Cesium.Math.toRadians(-45),  // pitch: look down 45°
                    250                          // range: 250m from entity
                ),
                duration: 1.0,
            });
        } else {
            console.warn(`[CesiumManager] No position found for uid: ${uid}`);
        }
    }

    // ===== Close zoom to entity (double-click) =====
    flyToEntityClose(uid) {
        const entity = this.entities[uid];
        if (entity) {
            this.viewer.selectedEntity = entity;
        }
        const pos = this.entityPositions[uid];
        if (pos) {
            // Use flyToBoundingSphere so the entity is exactly at screen center
            const sphere = new Cesium.BoundingSphere(pos, 0);
            this.viewer.camera.flyToBoundingSphere(sphere, {
                offset: new Cesium.HeadingPitchRange(
                    Cesium.Math.toRadians(0),   // heading: north
                    Cesium.Math.toRadians(-50),  // pitch: look down 50°
                    80                           // range: 80m from entity (close zoom)
                ),
                duration: 0.8,
            });
        } else {
            console.warn(`[CesiumManager] No position found for uid: ${uid}`);
        }
    }

    // ===== Relationship Lines on 3D map =====
    addRelationshipLines(relationships) {
        const relColors = {
            'ADJACENT_TO': Cesium.Color.ORANGE.withAlpha(0.7),
            'CONNECTED_TO': Cesium.Color.PURPLE.withAlpha(0.7),
            'MONITORS': Cesium.Color.RED.withAlpha(0.5),
            'HAS_STATE': Cesium.Color.AQUA.withAlpha(0.45),
            'SERVES': Cesium.Color.LIME.withAlpha(0.55),
            'NEAR': Cesium.Color.CYAN.withAlpha(0.4),
            'ON_ROAD': Cesium.Color.DEEPSKYBLUE.withAlpha(0.5),
            'ALONG': Cesium.Color.YELLOW.withAlpha(0.45),
        };

        // Skip CONTAINS on 3D map — logical hierarchy, not spatial visualization
        const skipTypes = new Set(['CONTAINS']);

        let count = 0;
        let skipped = 0;
        for (const rel of relationships) {
            if (skipTypes.has(rel.type)) continue;

            const srcPos = this.entityPositions[rel.source];
            const tgtPos = this.entityPositions[rel.target];
            if (!srcPos || !tgtPos) { skipped++; continue; }

            const color = relColors[rel.type] || Cesium.Color.WHITE.withAlpha(0.3);

            this.viewer.entities.add({
                polyline: {
                    positions: [srcPos, tgtPos],
                    width: 2.5,
                    material: color,
                },
                _isRelLine: true,
            });
            count++;
        }
        console.log(`[CesiumManager] Added ${count} relationship lines (skipped ${skipped} missing positions)`);
    }

    // ===== Spatial Relationship Lines (with embedded coordinates) =====
    addSpatialRelationLines(relations) {
        const relColors = {
            'ADJACENT_TO': Cesium.Color.ORANGE.withAlpha(0.6),
            'CONNECTED_TO': Cesium.Color.PURPLE.withAlpha(0.7),
            'MONITORS': Cesium.Color.RED.withAlpha(0.5),
            'HAS_STATE': Cesium.Color.AQUA.withAlpha(0.4),
            'SERVES': Cesium.Color.LIME.withAlpha(0.5),
            'NEAR': Cesium.Color.CYAN.withAlpha(0.4),
            'ON_ROAD': Cesium.Color.DEEPSKYBLUE.withAlpha(0.5),
            'ALONG': Cesium.Color.YELLOW.withAlpha(0.4),
        };

        let count = 0;
        let skipped = 0;
        for (const rel of relations) {
            // Use embedded coordinates directly — no need for entityPositions lookup
            if (!rel.src_lon || !rel.src_lat || !rel.tgt_lon || !rel.tgt_lat) {
                skipped++;
                continue;
            }

            const srcH = Math.min((rel.src_h || 3), 100) + 2;
            const tgtH = Math.min((rel.tgt_h || 3), 100) + 2;
            const color = relColors[rel.type] || Cesium.Color.WHITE.withAlpha(0.3);

            this.viewer.entities.add({
                polyline: {
                    positions: [
                        Cesium.Cartesian3.fromDegrees(rel.src_lon, rel.src_lat, srcH),
                        Cesium.Cartesian3.fromDegrees(rel.tgt_lon, rel.tgt_lat, tgtH),
                    ],
                    width: 2.0,
                    material: color,
                },
                _isRelLine: true,
            });
            count++;
        }
        console.log(`[CesiumManager] Added ${count} spatial relation lines (skipped ${skipped})`);
    }

    toggleRelationshipLines(visible) {
        this.viewer.entities.values.forEach(e => {
            if (e._isRelLine) e.show = visible;
        });
    }

    // ===== 3D Tileset Loading (kept for optional future use) =====
    async loadTileset(url) {
        try {
            this.tileset = await Cesium.Cesium3DTileset.fromUrl(url, {
                maximumScreenSpaceError: 16,
                maximumMemoryUsage: 512,
            });
            this.viewer.scene.primitives.add(this.tileset);
            console.log('[CesiumManager] 3D Tileset loaded');
            return this.tileset;
        } catch (err) {
            console.error('[CesiumManager] Failed to load tileset:', err);
            return null;
        }
    }

    // ===== Building Color Overlays (extruded polygons per usage type from KG) =====
    addBuildingColorOverlays(buildings) {
        const mPerDegLat = 111320;
        let count = 0;

        // Use a DataSource for batch management and better performance
        const ds = new Cesium.CustomDataSource('buildingOverlays');
        this.viewer.dataSources.add(ds);
        this._buildingOverlayDS = ds;

        for (const b of buildings) {
            const lon = b.lon;
            const lat = b.lat;
            const h = Math.min(b.h || 3, 200); // cap at 200m
            const color = Cesium.Color.fromCssColorString(b.c).withAlpha(0.85);

            // Build polygon hierarchy: prefer actual SHP boundary, fallback to bbox
            let hierarchy;
            if (b.bnd) {
                // Actual polygon footprint from SHP data
                let coords;
                try {
                    coords = typeof b.bnd === 'string' ? JSON.parse(b.bnd) : b.bnd;
                } catch (e) {
                    coords = null;
                }
                if (coords && coords.length >= 3) {
                    const flat = [];
                    for (const c of coords) {
                        flat.push(c[0], c[1]);
                    }
                    hierarchy = Cesium.Cartesian3.fromDegreesArray(flat);
                }
            }

            if (!hierarchy) {
                // Fallback: compute 4 rotated corners from width/depth/heading
                const halfW = Math.max((b.w || 5) / 2, 2);
                const halfD = Math.max((b.d || b.w || 5) / 2, 2);
                const headingRad = (b.hd || 0) * Math.PI / 180;
                const mPerDegLon = 111320 * Math.cos(lat * Math.PI / 180);
                const cosH = Math.cos(headingRad);
                const sinH = Math.sin(headingRad);
                const cornersM = [
                    [-halfW, -halfD], [halfW, -halfD], [halfW, halfD], [-halfW, halfD]
                ];
                const rotatedCorners = cornersM.map(([along, perp]) => {
                    const east = along * sinH + perp * cosH;
                    const north = along * cosH - perp * sinH;
                    return [lon + east / mPerDegLon, lat + north / mPerDegLat];
                });
                hierarchy = Cesium.Cartesian3.fromDegreesArray([
                    rotatedCorners[0][0], rotatedCorners[0][1],
                    rotatedCorners[1][0], rotatedCorners[1][1],
                    rotatedCorners[2][0], rotatedCorners[2][1],
                    rotatedCorners[3][0], rotatedCorners[3][1],
                ]);
            }

            // Build description HTML with GSID at the top
            let desc = '<table class="cesium-infoBox-defaultTable"><tbody>';
            desc += `<tr><td><b>유형</b></td><td><b>Building</b></td></tr>`;
            if (b.gsid) desc += `<tr><td><b>GSID</b></td><td style="font-family:monospace;color:#4FC3F7;">${b.gsid}</td></tr>`;
            if (b.n) desc += `<tr><td>건물명</td><td>${b.n}</td></tr>`;
            if (b.nf) desc += `<tr><td>고유번호</td><td style="font-family:monospace">${b.nf}</td></tr>`;
            if (b.t) desc += `<tr><td>건물유형</td><td>${b.t}</td></tr>`;
            if (b.un) desc += `<tr><td>주용도</td><td>${b.un}</td></tr>`;
            if (b.st) desc += `<tr><td>구조</td><td>${b.st}</td></tr>`;
            if (b.fl) desc += `<tr><td>지상층수</td><td>${b.fl}층</td></tr>`;
            if (b.ufl) desc += `<tr><td>지하층수</td><td>${b.ufl}층</td></tr>`;
            if (b.h) desc += `<tr><td>건축물높이</td><td>${b.h}m</td></tr>`;
            if (b.w) desc += `<tr><td>폭</td><td>${b.w}m</td></tr>`;
            if (b.d) desc += `<tr><td>깊이</td><td>${b.d}m</td></tr>`;
            if (b.ba) desc += `<tr><td>건축면적</td><td>${Math.round(b.ba * 100) / 100}m²</td></tr>`;
            if (b.gfa) desc += `<tr><td>연면적</td><td>${Math.round(b.gfa * 100) / 100}m²</td></tr>`;
            if (b.ra) desc += `<tr><td>도로명주소</td><td>${b.ra}</td></tr>`;
            if (b.pnu) desc += `<tr><td>PNU</td><td style="font-family:monospace">${b.pnu}</td></tr>`;
            if (b.zip) desc += `<tr><td>우편번호</td><td>${b.zip}</td></tr>`;
            if (b.adn) desc += `<tr><td>행정동</td><td>${b.adn}</td></tr>`;
            if (b.ad) desc += `<tr><td>사용승인일</td><td>${b.ad}</td></tr>`;
            desc += `<tr><td>경도</td><td>${lon}</td></tr>`;
            desc += `<tr><td>위도</td><td>${lat}</td></tr>`;
            desc += '</tbody></table>';

            const entity = ds.entities.add({
                name: b.n || b.uid,
                polygon: {
                    hierarchy: hierarchy,
                    extrudedHeight: h,
                    height: 0,
                    material: color,
                    outline: true,
                    outlineColor: Cesium.Color.fromCssColorString('#222').withAlpha(0.5),
                },
                description: desc,
            });
            entity._uid = b.uid;
            entity._label = 'Building';

            // Register in entities map so highlightEntity / flyToEntityClose can find it
            this.entities[b.uid] = entity;
            if (b.gsid) this.uidToGsid[b.uid] = b.gsid;
            // Register position for relationship lines & camera flyTo
            this.entityPositions[b.uid] = Cesium.Cartesian3.fromDegrees(lon, lat, h + 2);
            count++;
        }

        console.log(`[CesiumManager] Added ${count} building color overlays`);
        return count;
    }

    toggleBuildingOverlays(visible) {
        if (this._buildingOverlayDS) {
            this._buildingOverlayDS.show = visible;
        }
    }

    // ===== Parcel Boundary Overlays =====
    // ===== Viewport-based Parcel Loading =====
    initParcelViewport() {
        // Initialize parcel DataSource and camera listener for viewport-based loading
        if (this._parcelOverlayDS) {
            this.viewer.dataSources.remove(this._parcelOverlayDS, true);
        }
        const ds = new Cesium.CustomDataSource('parcelOverlays');
        this.viewer.dataSources.add(ds);
        this._parcelOverlayDS = ds;
        this._parcelOverlayDS.show = false;
        this._parcelVisible = false;
        this._parcelLoadedUids = new Set();
        this._parcelDebounce = null;

        // Listen for camera movement
        this.viewer.camera.moveEnd.addEventListener(() => {
            if (this._parcelVisible) this._loadViewportParcels();
        });
    }

    toggleParcelOverlays(visible) {
        this._parcelVisible = visible;
        if (this._parcelOverlayDS) {
            this._parcelOverlayDS.show = visible;
        }
        if (visible) {
            this._loadViewportParcels();
        }
    }

    _getViewportBBox() {
        const canvas = this.viewer.scene.canvas;
        const ellipsoid = this.viewer.scene.globe.ellipsoid;
        // Sample 4 corners + center of the viewport
        const corners = [
            [0, 0], [canvas.width, 0],
            [0, canvas.height], [canvas.width, canvas.height],
            [canvas.width / 2, canvas.height / 2],
        ];
        let minLon = 180, maxLon = -180, minLat = 90, maxLat = -90;
        let validCount = 0;
        for (const [x, y] of corners) {
            const ray = this.viewer.camera.getPickRay(new Cesium.Cartesian2(x, y));
            if (!ray) continue;
            const pos = this.viewer.scene.globe.pick(ray, this.viewer.scene);
            if (!pos) continue;
            const carto = ellipsoid.cartesianToCartographic(pos);
            const lon = Cesium.Math.toDegrees(carto.longitude);
            const lat = Cesium.Math.toDegrees(carto.latitude);
            minLon = Math.min(minLon, lon);
            maxLon = Math.max(maxLon, lon);
            minLat = Math.min(minLat, lat);
            maxLat = Math.max(maxLat, lat);
            validCount++;
        }
        if (validCount < 2) return null;
        return { minLon, maxLon, minLat, maxLat };
    }

    async _loadViewportParcels() {
        if (this._parcelDebounce) clearTimeout(this._parcelDebounce);
        this._parcelDebounce = setTimeout(async () => {
            const bbox = this._getViewportBBox();
            if (!bbox) return;

            // Skip if bbox is too large (zoomed out too far — would load too many).
            // The perspective camera in tilted views projects a wider footprint
            // onto the ground than the screen-aligned bbox suggests, so the limit
            // here is intentionally generous (~16 km).
            const lonSpan = bbox.maxLon - bbox.minLon;
            const latSpan = bbox.maxLat - bbox.minLat;
            if (lonSpan > 0.15 || latSpan > 0.15) {
                // Clear parcels when zoomed out very far to save memory
                if (this._parcelOverlayDS && this._parcelOverlayDS.entities.values.length > 0) {
                    this._parcelOverlayDS.entities.removeAll();
                    this._parcelLoadedUids.clear();
                    console.log('[CesiumManager] Parcels cleared (zoomed out too far)');
                }
                return;
            }
            console.log(`[CesiumManager] Loading parcels for bbox lonSpan=${lonSpan.toFixed(4)}, latSpan=${latSpan.toFixed(4)}`);

            try {
                const url = `/api/kg/parcel_boundaries?limit=5000` +
                    `&min_lon=${bbox.minLon}&max_lon=${bbox.maxLon}` +
                    `&min_lat=${bbox.minLat}&max_lat=${bbox.maxLat}`;
                const resp = await fetch(url);
                if (!resp.ok) return;
                const data = await resp.json();

                const ds = this._parcelOverlayDS;
                // Remove parcels that are outside the new bbox (memory cleanup)
                const toRemove = [];
                for (const entity of ds.entities.values) {
                    if (!entity._parcelLon) continue;
                    if (entity._parcelLon < bbox.minLon - 0.005 || entity._parcelLon > bbox.maxLon + 0.005 ||
                        entity._parcelLat < bbox.minLat - 0.005 || entity._parcelLat > bbox.maxLat + 0.005) {
                        toRemove.push(entity);
                    }
                }
                if (toRemove.length > 0) {
                    ds.entities.suspendEvents();
                    for (const e of toRemove) {
                        this._parcelLoadedUids.delete(e._uid);
                        ds.entities.remove(e);
                    }
                    ds.entities.resumeEvents();
                }

                // Add new parcels not yet rendered
                let added = 0;
                ds.entities.suspendEvents();
                for (const p of data.parcels) {
                    if (this._parcelLoadedUids.has(p.uid)) continue;
                    let coords = null;
                    if (p.boundary) {
                        try {
                            coords = typeof p.boundary === 'string' ? JSON.parse(p.boundary) : p.boundary;
                        } catch(e) { coords = null; }
                    }
                    // Point-only fallback (e.g., Sejong's synthesized parcels):
                    // build an ~30 m square centered on the parcel centroid so
                    // the marker is visible at typical district-scale zoom.
                    if (!coords || coords.length < 3) {
                        if (p.lon == null || p.lat == null) continue;
                        const dLat = 0.00027;  // ~30 m
                        const dLon = 0.00034;  // ~30 m at lat 36°
                        coords = [
                            [p.lon - dLon, p.lat - dLat],
                            [p.lon + dLon, p.lat - dLat],
                            [p.lon + dLon, p.lat + dLat],
                            [p.lon - dLon, p.lat + dLat],
                        ];
                    }

                    const flat = [];
                    for (const c of coords) { flat.push(c[0], c[1]); }
                    const hierarchy = Cesium.Cartesian3.fromDegreesArray(flat);
                    const isPoint = !!p.is_point;  // remaining (rare) point-only parcels

                    // Cadastral rendering — outline-emphasis with a UNIFORM outline
                    // colour across every category so that the 지적선 layer reads
                    // consistently across the whole study area. Earlier we tinted
                    // the outline by category which let mis-classified parcels
                    // (e.g. Sejong's "unknown" / grey #DDDDDD lots, ~5% of the
                    // city) visually overrun their correctly-classified neighbours
                    // and made the cadastral grid look broken. The fill keeps a
                    // very small category-coloured tint so the user can still
                    // identify built-up vs. natural lots when zoomed in.
                    const naturalCats = new Set([
                        'forest', 'paddy', 'field', 'road', 'ditch', 'embankment',
                        'miscellaneous', 'unknown', 'river', 'waterway', 'orchard',
                        'pasture', 'cemetery', 'historic', 'rail'
                    ]);
                    const isNatural = naturalCats.has(p.cat);
                    const fillAlpha = isNatural ? 0.06 : 0.14;
                    const color = Cesium.Color.fromCssColorString(p.color || '#888888').withAlpha(fillAlpha);
                    // Uniform yellow outline (#FFD700 gold). Slightly stronger
                    // for built-up lots so building blocks pop, faintly visible
                    // for natural lots so the grid remains continuous.
                    const outlineAlpha = isNatural ? 0.55 : 0.90;
                    const outlineColor = Cesium.Color.fromCssColorString('#FFD700').withAlpha(outlineAlpha);

                    let desc = '<table class="cesium-infoBox-defaultTable"><tbody>';
                    desc += `<tr><td><b>유형</b></td><td><b>Parcel (필지)</b></td></tr>`;
                    if (p.gsid) desc += `<tr><td>GSID</td><td style="font-family:monospace;color:#4FC3F7">${p.gsid}</td></tr>`;
                    if (p.subtype) desc += `<tr><td>SubType</td><td>${p.subtype}</td></tr>`;
                    desc += `<tr><td>PNU</td><td style="font-family:monospace">${p.pnu}</td></tr>`;
                    if (p.jibun) desc += `<tr><td>지번</td><td>${p.jibun}</td></tr>`;
                    desc += `<tr><td>지목</td><td>${p.cat_code || ''} (${p.cat || ''})</td></tr>`;
                    if (p.area) desc += `<tr><td>면적</td><td>${Math.round(p.area * 10) / 10}㎡</td></tr>`;
                    desc += `<tr><td>경도</td><td>${p.lon}</td></tr>`;
                    desc += `<tr><td>위도</td><td>${p.lat}</td></tr>`;
                    desc += '</tbody></table>';

                    const entity = ds.entities.add({
                        name: `필지 ${p.jibun || p.pnu}`,
                        polygon: {
                            hierarchy: hierarchy,
                            height: 0,
                            material: color,
                            outline: true,
                            outlineColor: outlineColor,
                            outlineWidth: 1,
                        },
                        description: desc,
                    });
                    entity._uid = p.uid;
                    entity._label = 'Parcel';
                    entity._parcelLon = p.lon;
                    entity._parcelLat = p.lat;
                    this._parcelLoadedUids.add(p.uid);
                    added++;
                }
                ds.entities.resumeEvents();

                if (added > 0) {
                    console.log(`[CesiumManager] Parcels: +${added} loaded (total ${ds.entities.values.length} in view)`);
                }
            } catch (err) {
                console.warn('[CesiumManager] Parcel viewport load failed:', err);
            }
        }, 300); // 300ms debounce
    }

    // ===== Highlight a building at coordinates =====
    highlightBuildingAt(lon, lat, height, info) {
        if (this._highlightEntity) {
            this.viewer.entities.remove(this._highlightEntity);
        }
        const h = (info && info.height > 0) ? info.height : (height || 10);
        const w = (info && info.width > 0) ? info.width / 2 : 10;
        const d = (info && info.depth > 0) ? info.depth / 2 : 10;
        const radius = Math.max(w, d, 5);
        this._highlightEntity = this.viewer.entities.add({
            position: Cesium.Cartesian3.fromDegrees(lon, lat, 0),
            ellipse: {
                semiMinorAxis: radius,
                semiMajorAxis: radius,
                height: 0,
                extrudedHeight: h,
                material: Cesium.Color.YELLOW.withAlpha(0.2),
                outline: true,
                outlineColor: Cesium.Color.YELLOW.withAlpha(0.8),
                outlineWidth: 2,
            },
            name: info ? (info.name || 'Selected Building') : 'Selected Building',
            description: info ? this._buildDescription(info, 'Building') : '',
        });
        this.viewer.selectedEntity = this._highlightEntity;
    }

    clearBuildingHighlight() {
        if (this._highlightEntity) {
            this.viewer.entities.remove(this._highlightEntity);
            this._highlightEntity = null;
        }
    }

    // ===== Connection Visualization (클릭 시 연결된 Entity 표시) =====
    async showConnections(uid) {
        this.clearConnections();

        const sourcePos = this.entityPositions[uid];
        if (!sourcePos) return;

        try {
            const resp = await fetch(`/api/geokg/nodes/${uid}/connections`);
            const data = await resp.json();
            this._renderConnections(uid, sourcePos, data.connections);
            this._showConnectionSummary(data.summary, data.connections.length);
        } catch (e) {
            console.warn('[CesiumManager] Failed to load connections:', e);
        }
    }

    /**
     * Find the nearest point (perpendicular foot) on a road polyline to a target point.
     * Uses segment-projection in lon/lat space (sufficient for ~10km Yuseong-gu scale).
     * @param {string} roadUid - Road entity UID
     * @param {number} targetLon - Target longitude
     * @param {number} targetLat - Target latitude
     * @returns {Cesium.Cartesian3|null} Nearest point on road, or null if road not found
     */
    _nearestPointOnRoad(roadUid, targetLon, targetLat) {
        const coords = this.roadPolylines[roadUid];
        if (!coords || coords.length < 2) return null;

        let minDistSq = Infinity;
        let nearestLon = coords[0][0], nearestLat = coords[0][1];

        for (let i = 0; i < coords.length - 1; i++) {
            const [x1, y1] = coords[i];
            const [x2, y2] = coords[i + 1];

            // Project target onto segment [p1, p2]
            const dx = x2 - x1, dy = y2 - y1;
            const len2 = dx * dx + dy * dy;
            const t = len2 > 0
                ? Math.max(0, Math.min(1, ((targetLon - x1) * dx + (targetLat - y1) * dy) / len2))
                : 0;

            const projLon = x1 + t * dx;
            const projLat = y1 + t * dy;
            const distSq = (projLon - targetLon) ** 2 + (projLat - targetLat) ** 2;

            if (distSq < minDistSq) {
                minDistSq = distSq;
                nearestLon = projLon;
                nearestLat = projLat;
            }
        }
        return Cesium.Cartesian3.fromDegrees(nearestLon, nearestLat, 2);
    }

    _renderConnections(sourceUid, sourcePos, connections) {
        // Vibrant, distinct colors per relationship type
        const relColors = {
            'ADJACENT_TO':           '#ff9800',  // 오렌지: 인접
            'MONITORS':              '#e91e63',  // 핑크: 모니터링
            'SERVES':                '#8bc34a',  // 연두: 서비스
            'ON_STREET':             '#26c6da',  // 시안: 도로명
            'ON_ROAD':               '#29b6f6',  // 하늘: 도로 위
            'ON_PARCEL':             '#e8d44d',  // 노랑: 필지
            'NEAR':                  '#66bb6a',  // 초록: 근접
            'NEAR_BUILDING':         '#78909c',  // 회색: 건물 근접
            'NEAREST_SHELTER':       '#ef5350',  // 빨강: 대피시설
            'ACCESSIBLE_BY_TRANSIT': '#42a5f5',  // 파랑: 대중교통
            'NEAR_PARK':             '#4caf50',  // 녹색: 공원
            'NEAR_FACILITY':         '#ffa726',  // 주황: 시설 간
            'ALONG':                 '#ffee58',  // 밝은노랑: 도로변
            'CONNECTED_TO':          '#9c27b0',  // 보라: 도로연결
            'HAS_STATE':             '#00bcd4',  // 청록: 상태
            'CONTAINS':              '#4fc3f7',  // 밝은파랑: 포함
            'COLOCATED':             '#80deea',  // 연한청록: 공존
            'SAME_DONG':             '#ffcc80',  // 연한주황: 같은동
            'SAME_USAGE':            '#ce93d8',  // 연한보라: 같은용도
        };
        // Line width per relationship category
        const relWidths = {
            'ADJACENT_TO': 4, 'MONITORS': 3.5, 'SERVES': 3,
            'ON_PARCEL': 2.5, 'ON_STREET': 2.5, 'ON_ROAD': 2.5,
            'NEAREST_SHELTER': 3, 'ACCESSIBLE_BY_TRANSIT': 3, 'NEAR_PARK': 3,
        };

        const ds = this._connectionDS;

        // Source highlight ring
        ds.entities.add({
            position: sourcePos,
            point: {
                pixelSize: 22,
                color: Cesium.Color.TRANSPARENT,
                outlineColor: Cesium.Color.fromCssColorString('#4fc3f7').withAlpha(0.95),
                outlineWidth: 3,
                disableDepthTestDistance: Number.POSITIVE_INFINITY,
            },
        });

        // Collect unique rel types for legend
        const usedTypes = new Set();

        for (const conn of connections) {
            // Use existing entity position if available, fall back to API coordinates
            let targetPos = this.entityPositions[conn.uid];
            if (!targetPos && conn.longitude && conn.latitude) {
                targetPos = Cesium.Cartesian3.fromDegrees(
                    conn.longitude, conn.latitude, (conn.height || 5) + 2
                );
            }
            // Skip if no position available (e.g., Zone without coordinates)
            if (!targetPos) continue;

            const colorHex = relColors[conn.rel_type] || '#aaaaaa';
            const color = Cesium.Color.fromCssColorString(colorHex);
            const lineWidth = relWidths[conn.rel_type] || 2.5;
            usedTypes.add(conn.rel_type);

            // Compute perpendicular foot for road connections (수선의 발)
            let lineStart = sourcePos;
            let lineEnd = targetPos;

            // Source is a road → connect from nearest point on road to each target
            if (this.roadPolylines[sourceUid] && targetPos) {
                try {
                    const tc = Cesium.Cartographic.fromCartesian(targetPos);
                    const foot = this._nearestPointOnRoad(
                        sourceUid, Cesium.Math.toDegrees(tc.longitude), Cesium.Math.toDegrees(tc.latitude)
                    );
                    if (foot) lineStart = foot;
                } catch (_) { /* keep sourcePos fallback */ }
            }

            // Target is a road → connect from source to nearest point on target road
            if (this.roadPolylines[conn.uid]) {
                try {
                    const sc = Cesium.Cartographic.fromCartesian(sourcePos);
                    const foot = this._nearestPointOnRoad(
                        conn.uid, Cesium.Math.toDegrees(sc.longitude), Cesium.Math.toDegrees(sc.latitude)
                    );
                    if (foot) lineEnd = foot;
                } catch (_) { /* keep targetPos fallback */ }
            }

            // Solid colored connection line (no glow → clear color distinction)
            ds.entities.add({
                polyline: {
                    positions: [lineStart, lineEnd],
                    width: lineWidth,
                    material: new Cesium.PolylineOutlineMaterialProperty({
                        color: color.withAlpha(0.85),
                        outlineWidth: 1,
                        outlineColor: Cesium.Color.BLACK.withAlpha(0.4),
                    }),
                },
            });

            // Target: colored ring + name label with distance
            // For road targets, show marker at perpendicular foot (lineEnd) instead of midpoint
            const markerPos = this.roadPolylines[conn.uid] ? lineEnd : targetPos;
            const distStr = conn.dist_m ? ` (${conn.dist_m}m)` : '';
            ds.entities.add({
                position: markerPos,
                point: {
                    pixelSize: 12,
                    color: color.withAlpha(0.3),
                    outlineColor: color.withAlpha(0.95),
                    outlineWidth: 2.5,
                    disableDepthTestDistance: Number.POSITIVE_INFINITY,
                },
                label: {
                    text: `${conn.name || conn.uid}${distStr}`,
                    font: 'bold 14px sans-serif',
                    fillColor: color,
                    style: Cesium.LabelStyle.FILL_AND_OUTLINE,
                    outlineWidth: 3,
                    outlineColor: Cesium.Color.BLACK,
                    verticalOrigin: Cesium.VerticalOrigin.BOTTOM,
                    pixelOffset: new Cesium.Cartesian2(0, -14),
                    distanceDisplayCondition: new Cesium.DistanceDisplayCondition(0, 500),
                    disableDepthTestDistance: Number.POSITIVE_INFINITY,
                    showBackground: true,
                    backgroundColor: Cesium.Color.BLACK.withAlpha(0.6),
                    backgroundPadding: new Cesium.Cartesian2(6, 3),
                    scaleByDistance: new Cesium.NearFarScalar(30, 1.4, 400, 0.6),
                },
            });
        }

        // Draw connection legend overlay
        this._showConnectionLegend(usedTypes, relColors);

        console.log(`[CesiumManager] Showing ${connections.length} connections for ${sourceUid}`);
    }

    _showConnectionLegend(usedTypes, relColors) {
        // Remove previous legend if exists
        const oldLegend = document.getElementById('connection-legend');
        if (oldLegend) oldLegend.remove();

        if (usedTypes.size === 0) return;

        const relLabels = {
            'ADJACENT_TO': '인접', 'MONITORS': '모니터링', 'SERVES': '서비스',
            'ON_STREET': '도로명', 'ON_ROAD': '도로 위', 'ON_PARCEL': '필지',
            'NEAR': '근접', 'NEAR_BUILDING': '건물 근접', 'CONTAINS': '포함',
            'NEAREST_SHELTER': '대피시설', 'ACCESSIBLE_BY_TRANSIT': '대중교통',
            'NEAR_PARK': '공원', 'NEAR_FACILITY': '시설 간', 'COLOCATED': '공존',
            'SAME_DONG': '같은동', 'SAME_USAGE': '같은용도',
            'ALONG': '도로변', 'CONNECTED_TO': '도로연결', 'HAS_STATE': '상태',
        };

        const legend = document.createElement('div');
        legend.id = 'connection-legend';
        legend.style.cssText = `
            position: absolute; top: 8px; left: 8px;
            padding: 8px 12px; background: rgba(10,15,30,0.92);
            border-radius: 8px; border: 1px solid rgba(79,195,247,0.4);
            z-index: 100; font-size: 11px; color: #ccc;
            min-width: 100px; line-height: 1.4;
            pointer-events: auto; box-shadow: 0 2px 8px rgba(0,0,0,0.5);
        `;

        let html = '<div style="color:#4fc3f7;font-weight:bold;margin-bottom:6px;font-size:12px;">연결 관계</div>';
        for (const type of usedTypes) {
            const color = relColors[type] || '#aaa';
            const label = relLabels[type] || type;
            html += `<div style="margin:3px 0;display:flex;align-items:center;white-space:nowrap;">
                <span style="display:inline-block;width:20px;height:3px;background:${color};margin-right:8px;border-radius:2px;flex-shrink:0;"></span>
                <span>${label}</span>
            </div>`;
        }
        legend.innerHTML = html;
        this.viewer.container.appendChild(legend);
    }

    _showConnectionSummary(summary, shownCount) {
        if (!summary || Object.keys(summary).length === 0) {
            this._connectionInfoEl.style.display = 'none';
            return;
        }
        const relLabels = {
            'ADJACENT_TO': '인접', 'MONITORS': '모니터링', 'SERVES': '서비스',
            'ON_STREET': '도로명', 'ON_ROAD': '도로 위', 'ON_PARCEL': '필지',
            'NEAR': '근접', 'NEAR_BUILDING': '건물 근접', 'CONTAINS': '포함',
            'NEAREST_SHELTER': '대피시설', 'ACCESSIBLE_BY_TRANSIT': '대중교통',
            'NEAR_PARK': '공원', 'NEAR_FACILITY': '시설 간', 'COLOCATED': '공존',
            'SAME_DONG': '같은동', 'SAME_USAGE': '같은용도',
            'ALONG': '도로변', 'CONNECTED_TO': '도로연결', 'HAS_STATE': '상태',
        };
        const total = Object.values(summary).reduce((a, b) => a + b, 0);
        const parts = Object.entries(summary)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 8)
            .map(([type, count]) => {
                const label = relLabels[type] || type;
                return `<span style="color:#4fc3f7">${label}</span>(${count})`;
            })
            .join(' · ');
        const shown = shownCount !== undefined ? shownCount : total;
        const showInfo = shown < total ? ` (${shown}개 표시)` : '';
        this._connectionInfoEl.innerHTML = `🔗 전체 ${total}개 관계${showInfo}: ${parts}`;
        this._connectionInfoEl.style.display = 'block';
    }

    clearConnections() {
        if (this._connectionDS) {
            this._connectionDS.entities.removeAll();
        }
        if (this._connectionInfoEl) {
            this._connectionInfoEl.style.display = 'none';
        }
        const legend = document.getElementById('connection-legend');
        if (legend) legend.remove();
    }

    // ===== Icon Creation =====
    _createSensorIcon(color, type) {
        const canvas = document.createElement('canvas');
        canvas.width = 28;
        canvas.height = 28;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = 'rgba(0,0,0,0.5)';
        ctx.beginPath();
        ctx.arc(14, 14, 12, 0, Math.PI * 2);
        ctx.fill();
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(14, 14, 11, 0, Math.PI * 2);
        ctx.stroke();
        ctx.fillStyle = color;
        ctx.font = 'bold 14px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        const symbols = { temperature: 'T', humidity: 'H', aqi: 'A', noise: 'N', pm25: 'P' };
        ctx.fillText(symbols[type] || '?', 14, 15);
        return canvas;
    }

    _createCameraIcon(color) {
        const canvas = document.createElement('canvas');
        canvas.width = 28;
        canvas.height = 28;
        const ctx = canvas.getContext('2d');
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.beginPath();
        ctx.roundRect(4, 8, 16, 12, 2);
        ctx.fill();
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(22, 14, 5, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = '#fff';
        ctx.beginPath();
        ctx.arc(22, 14, 2, 0, Math.PI * 2);
        ctx.fill();
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.arc(8, 11, 2, 0, Math.PI * 2);
        ctx.fill();
        return canvas;
    }

    _createIoTIcon(color, symbol) {
        const canvas = document.createElement('canvas');
        canvas.width = 28;
        canvas.height = 28;
        const ctx = canvas.getContext('2d');

        // Diamond shape background
        ctx.fillStyle = 'rgba(0,0,0,0.6)';
        ctx.beginPath();
        ctx.moveTo(14, 2);   // top
        ctx.lineTo(26, 14);  // right
        ctx.lineTo(14, 26);  // bottom
        ctx.lineTo(2, 14);   // left
        ctx.closePath();
        ctx.fill();

        // Diamond outline
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(14, 3);
        ctx.lineTo(25, 14);
        ctx.lineTo(14, 25);
        ctx.lineTo(3, 14);
        ctx.closePath();
        ctx.stroke();

        // Symbol text
        ctx.fillStyle = color;
        ctx.font = 'bold 13px sans-serif';
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(symbol, 14, 14);

        return canvas;
    }

    _buildDescription(props, label) {
        // Korean labels for building registry fields
        const koreanLabels = {
            name: '건물명',
            building_type: '건물유형',
            usage_name: '주용도',
            usage_code: '용도코드',
            structure_type: '구조',
            floors: '지상층수',
            underground_floors: '지하층수',
            height: '건축물높이(m)',
            elevation: '해발고도(m)',
            width: '폭(m)',
            depth: '깊이(m)',
            building_area: '건축면적(m²)',
            gross_floor_area: '연면적(m²)',
            household_count: '세대수',
            ho_count: '호수',
            address: '지번주소',
            road_address: '도로명주소',
            approval_date: '사용승인일',
            admin_dong: '행정동코드',
            nf_id: '고유번호',
            longitude: '경도',
            latitude: '위도',
            // Non-building entities
            sensor_type: '센서유형',
            camera_type: '카메라유형',
            vehicle_type: '차량유형',
            road_type: '도로유형',
            facility_type: '시설유형',
            tree_species: '수종',
            plate: '차량번호',
            speed: '속도(km/h)',
            value: '측정값',
            unit: '단위',
            status: '상태',
            resolution: '해상도',
            capacity: '수용량',
            occupied: '점유수',
            lanes: '차선수',
            height_m: '높이(m)',
            // IoT Address entities
            iot_type: '사물유형코드',
            iot_type_name: '사물유형',
            bjd_name: '법정동명',
            bjd_code: '법정동코드',
            road_name: '도로명',
            bldg_main: '건물본번',
            bldg_sub: '건물부번',
            geocode_method: '지오코딩방법',
            // Road intersection entities
            intersection_type: '교차로유형',
            type_code: '유형코드',
            eng_name: '영문명',
        };
        const skip = new Set(['uid', 'coordinates', 'boundary', '_label', 'gsid', 'subtype',
                              'color', 'lod_level', 'altitude', 'heading', 'pitch', 'roll',
                              'importance', 'obj_id']);
        // Skip zero/empty values for certain numeric fields
        const skipIfZero = new Set(['underground_floors', 'household_count', 'ho_count',
                                     'building_area', 'gross_floor_area', 'elevation']);

        let html = `<table class="cesium-infoBox-defaultTable"><tbody>`;
        html += `<tr><td><b>유형</b></td><td><b>${label}</b></td></tr>`;

        // Show GSID and SubType prominently at top
        if (props.gsid) {
            html += `<tr><td><b>GSID</b></td><td style="font-family:monospace;color:#4FC3F7;">${props.gsid}</td></tr>`;
        }
        if (props.subtype) {
            html += `<tr><td><b>서브타입</b></td><td>${props.subtype}</td></tr>`;
        }
        if (props.obj_id) {
            html += `<tr><td><b>사물주소ID</b></td><td style="font-family:monospace;">${props.obj_id}</td></tr>`;
        }
        for (const [key, val] of Object.entries(props)) {
            if (skip.has(key)) continue;
            if (val === null || val === undefined || val === '') continue;
            if (skipIfZero.has(key) && (val === 0 || val === '0')) continue;
            const display = typeof val === 'number' ? Math.round(val * 100) / 100 : val;
            const displayKey = koreanLabels[key] || key;
            html += `<tr><td>${displayKey}</td><td>${display}</td></tr>`;
        }
        html += `</tbody></table>`;
        return html;
    }
}
