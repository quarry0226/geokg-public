"""
Dynamic Update Mechanism for GeoKG.

Implements the paper's dynamic data-driven KG update:
- Simulates real-time sensor data (vehicles, parking, environment)
- Performs batch updates to Neo4j
- Publishes change events for WebSocket broadcast
"""

import random
import math
import asyncio
from datetime import datetime
from backend.db.neo4j_client import db
from backend.config import SAMPLE_CENTER_LON, SAMPLE_CENTER_LAT, DYNAMIC_UPDATE_INTERVAL


class DynamicUpdateEngine:
    def __init__(self):
        self.db = db
        self.subscribers: list[asyncio.Queue] = []
        self._running = False

    def subscribe(self) -> asyncio.Queue:
        q = asyncio.Queue()
        self.subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self.subscribers:
            self.subscribers.remove(q)

    async def _publish(self, event: dict):
        for q in self.subscribers:
            await q.put(event)

    def _simulate_vehicle_movements(self) -> list[dict]:
        """Simulate vehicle position updates."""
        vehicles = self.db.query("MATCH (v:Vehicle) RETURN v.uid as uid, v.longitude as lon, v.latitude as lat, v.heading as heading, v.speed as speed")
        updates = []
        for v in vehicles:
            heading_rad = math.radians(v["heading"] + random.uniform(-15, 15))
            speed_factor = (v["speed"] or 30) / 3600 / 111000  # approx degrees per second
            new_lon = v["lon"] + math.cos(heading_rad) * speed_factor * DYNAMIC_UPDATE_INTERVAL
            new_lat = v["lat"] + math.sin(heading_rad) * speed_factor * DYNAMIC_UPDATE_INTERVAL
            new_heading = (v["heading"] + random.uniform(-10, 10)) % 360
            new_speed = max(0, min(80, (v["speed"] or 30) + random.uniform(-5, 5)))
            updates.append({
                "uid": v["uid"],
                "longitude": round(new_lon, 6),
                "latitude": round(new_lat, 6),
                "heading": round(new_heading, 1),
                "speed": round(new_speed, 1),
            })
        if updates:
            self.db.batch_update(
                "MATCH (v:Vehicle {uid: $uid}) SET v.longitude=$longitude, v.latitude=$latitude, v.heading=$heading, v.speed=$speed",
                updates,
            )
        return updates

    def _simulate_parking_updates(self) -> list[dict]:
        """Simulate parking occupancy changes."""
        lots = self.db.query("MATCH (p:ParkingLot) RETURN p.uid as uid, p.capacity as cap, p.occupied as occ")
        updates = []
        for lot in lots:
            delta = random.choice([-1, 0, 0, 1])
            new_occ = max(0, min(lot["cap"], lot["occ"] + delta))
            if new_occ != lot["occ"]:
                updates.append({"uid": lot["uid"], "occupied": new_occ, "capacity": lot["cap"]})
                self.db.query(
                    "MATCH (p:ParkingLot {uid: $uid}) SET p.occupied=$occupied",
                    uid=lot["uid"], occupied=new_occ,
                )
        return updates

    def _simulate_environment_updates(self) -> list[dict]:
        """Simulate environmental sensor readings."""
        sensors = self.db.query("MATCH (s:Sensor) RETURN s.uid as uid, s.sensor_type as stype, s.value as val")
        updates = []
        for s in sensors:
            if s["stype"] == "temperature":
                new_val = round(s["val"] + random.uniform(-0.3, 0.3), 1)
            elif s["stype"] == "humidity":
                new_val = round(max(20, min(95, s["val"] + random.uniform(-1, 1))), 1)
            elif s["stype"] == "aqi":
                new_val = max(10, min(200, int(s["val"] + random.randint(-3, 3))))
            elif s["stype"] == "noise":
                new_val = round(max(30, min(90, s["val"] + random.uniform(-2, 2))), 1)
            else:
                continue
            now = datetime.utcnow().isoformat()
            updates.append({"uid": s["uid"], "value": new_val, "last_updated": now, "sensor_type": s["stype"]})
            self.db.query(
                "MATCH (s:Sensor {uid: $uid}) SET s.value=$value, s.last_updated=$ts",
                uid=s["uid"], value=new_val, ts=now,
            )
        return updates

    async def run_update_cycle(self):
        """Run one cycle of simulated updates and publish events."""
        vehicle_updates = self._simulate_vehicle_movements()
        parking_updates = self._simulate_parking_updates()
        env_updates = self._simulate_environment_updates()

        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "vehicles": vehicle_updates,
            "parking": parking_updates,
            "environment": env_updates,
        }
        await self._publish(event)
        return event

    async def start(self):
        """Start the continuous update loop."""
        self._running = True
        while self._running:
            await self.run_update_cycle()
            await asyncio.sleep(DYNAMIC_UPDATE_INTERVAL)

    def stop(self):
        self._running = False


dynamic_engine = DynamicUpdateEngine()
