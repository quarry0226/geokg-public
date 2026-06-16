"""
Declarative Relationship Rules for GeoKG.

Each rule defines:
  - rel_type: the Neo4j relationship type name
  - strategy: one of 7 matching strategies
  - source_labels / target_labels: node labels ("*" = all Entity labels)
  - exclude_source / exclude_target: labels to skip
  - source_iot_types / target_iot_types: filter ThingsAddr by iot_type (optional)
  - requires: properties that must exist on source/target nodes
  - params: strategy-specific parameters
  - description: human-readable purpose

When a new entity type is added, it automatically participates in rules
whose source_labels include "*" and whose required properties it possesses.
"""

# ---------------------------------------------------------------------------
# Rule list — order matters (heavier rules last)
# ---------------------------------------------------------------------------

RELATIONSHIP_RULES = [

    # ══════════════════════════════════════════════════════════════════
    #  Strategy: attribute_match  (속성 정확 일치)
    # ══════════════════════════════════════════════════════════════════
    {
        "rel_type": "ON_PARCEL",
        "strategy": "attribute_match",
        "source_labels": ["Building"],
        "target_labels": ["Parcel"],
        "requires": {"source": ["pnu"], "target": ["pnu"]},
        "params": {"source_key": "pnu", "target_key": "pnu"},
        "description": "건축물 → 필지 (PNU 정확 일치)",
    },

    # ══════════════════════════════════════════════════════════════════
    #  ON_STREET: road_name 정확 매칭 (attribute_match)
    # ══════════════════════════════════════════════════════════════════
    {
        "rel_type": "ON_STREET",
        "strategy": "attribute_match",
        "source_labels": ["Building"],
        "target_labels": ["Road"],
        "requires": {"source": ["rn_cd"], "target": ["rn_cd"]},
        "params": {"source_key": "rn_cd", "target_key": "rn_cd"},
        "description": "건축물 → 도로 (KAIS RN_CD 7자리 정확 매칭, 100% 보장)",
    },

    # ══════════════════════════════════════════════════════════════════
    #  Strategy: nearest  (최근접 1개)
    # ══════════════════════════════════════════════════════════════════
    {
        "rel_type": "FRONTS_ROAD",
        "strategy": "nearest",
        "source_labels": ["Building"],
        "target_labels": ["Road"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {},
        "description": "건축물 → 공간상 최근접 도로 (모든 건물에 부여, ON_STREET와 별개)",
    },
    {
        "rel_type": "ALONG_ROAD",
        "strategy": "nearest",
        "source_labels": ["*"],
        "target_labels": ["Road"],
        "exclude_source": ["Road", "Zone", "Parcel", "Building"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {},
        "description": "비건축물 엔티티 → 최근접 도로 (도로변 시설)",
    },

    # ══════════════════════════════════════════════════════════════════
    #  Strategy: same_attribute_cluster  (같은 속성 클러스터)
    # ══════════════════════════════════════════════════════════════════
    {
        "rel_type": "SAME_DONG",
        "strategy": "same_attribute_cluster",
        "source_labels": ["Building"],
        "target_labels": ["Building"],
        "requires": {"source": ["admin_dong", "longitude", "latitude"]},
        "params": {"group_key": "admin_dong", "grid_size_deg": 0.003, "max_per_node": 5},
        "description": "같은 행정동 건축물 클러스터 (~300m 그리드, 9-cell 탐색)",
    },
    {
        "rel_type": "SAME_USAGE",
        "strategy": "same_attribute_cluster",
        "source_labels": ["Building"],
        "target_labels": ["Building"],
        "requires": {"source": ["building_type", "longitude", "latitude"]},
        "params": {"group_key": "building_type", "grid_size_deg": 0.003, "max_per_node": 5},
        "description": "같은 용도 건축물 클러스터 (~300m 그리드, 9-cell 탐색)",
    },

    # ══════════════════════════════════════════════════════════════════
    #  Strategy: proximity  (공간 근접 — 반경 내 다수 연결)
    # ══════════════════════════════════════════════════════════════════
    {
        "rel_type": "MONITORS",
        "strategy": "proximity",
        "source_labels": ["Sensor", "Camera"],
        "target_labels": ["Building"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {"radius_m": 100, "max_targets": 10, "batch_size": 0},
        "description": "센서/카메라 → 모니터링 건축물 (100m, 최대 10)",
    },
    {
        "rel_type": "SERVES",
        "strategy": "proximity",
        "source_labels": ["Facility", "ParkingLot"],
        "target_labels": ["Building"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {"radius_m": 200, "max_targets": 10, "batch_size": 0},
        "description": "시설물/주차장 → 서비스 건축물 (200m, 최대 10)",
    },
    {
        "rel_type": "NEAR",
        "strategy": "proximity",
        "source_labels": ["Tree"],
        "target_labels": ["Building"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {"radius_m": 50, "max_targets": 10, "batch_size": 0},
        "description": "수목 → 주변 건축물 (50m, 최대 10)",
    },
    {
        "rel_type": "NEAR_BUILDING",
        "strategy": "proximity",
        "source_labels": ["*"],
        "target_labels": ["Building"],
        "exclude_source": ["Building", "Parcel", "Zone", "Road", "Sensor", "Camera",
                           "Tree", "Facility", "ParkingLot"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {"radius_m": 100, "max_targets": 5, "batch_size": 200},
        "description": "기타 엔티티 → 주변 건축물 (100m, 최대 5)",
    },
    {
        # 건축물 ↔ 건축물 인접성 (50m, grid-bucketed cap per source)
        # Restores the paper-baseline ADJACENT_TO behaviour after the v2
        # rule-engine refactor moved this from a seed-time grid walk into
        # the rule-engine proper.
        "rel_type": "ADJACENT_TO",
        "strategy": "proximity",
        "source_labels": ["Building"],
        "target_labels": ["Building"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {"radius_m": 50, "max_targets": 10, "batch_size": 200,
                   "exclude_same_uid": True},
        "description": "건축물 ↔ 건축물 인접성 (50m, 최대 10)",
    },

    # ── 신규: 재난 대피 접근성 ──
    {
        "rel_type": "NEAREST_SHELTER",
        "strategy": "proximity",
        "source_labels": ["Building"],
        "target_labels": ["ThingsAddr"],
        "target_iot_types": ["SHELTER", "CivilDefense", "EQOUT", "EWRC", "CoolingCen", "EMERWAT"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {"radius_m": 500, "max_targets": 3, "batch_size": 200},
        "description": "건축물 → 가장 가까운 대피시설 (500m, 최대 3) — 새 KAIS 사물주소 SHELTER 통합",
    },

    # ── 신규: 대중교통 접근성 ──
    {
        "rel_type": "ACCESSIBLE_BY_TRANSIT",
        "strategy": "proximity",
        "source_labels": ["Building"],
        "target_labels": ["ThingsAddr"],
        "target_iot_types": ["BUSST", "TAXIST", "BUS_STOP", "TAXI_STAND"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {"radius_m": 300, "max_targets": 3, "batch_size": 200},
        "description": "건축물 → 가장 가까운 대중교통 (300m, 최대 3)",
    },

    # ── 신규: 공원/녹지 접근성 ──
    {
        "rel_type": "NEAR_PARK",
        "strategy": "proximity",
        "source_labels": ["Building"],
        "target_labels": ["ThingsAddr"],
        "target_iot_types": ["PARK", "CHPARK", "SCPARK", "ChPlayground"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {"radius_m": 500, "max_targets": 3, "batch_size": 200},
        "description": "건축물 → 가장 가까운 공원/놀이터 (500m, 최대 3)",
    },

    # ── 신규: 사물주소 간 근접 ──
    {
        "rel_type": "NEAR_FACILITY",
        "strategy": "proximity",
        "source_labels": ["ThingsAddr"],
        "target_labels": ["ThingsAddr"],
        "requires": {"source": ["longitude", "latitude"], "target": ["longitude", "latitude"]},
        "params": {"radius_m": 200, "max_targets": 5, "batch_size": 200, "exclude_same_uid": True},
        "description": "사물주소 ↔ 사물주소 (200m, 서로 다른 유형 우선)",
    },

    # ══════════════════════════════════════════════════════════════════
    #  Strategy: attribute_prefix_proximity  (접두사 + 근접)
    # ══════════════════════════════════════════════════════════════════
    {
        "rel_type": "ON_PARCEL",
        "strategy": "attribute_prefix_proximity",
        "source_labels": ["*"],
        "target_labels": ["Parcel"],
        "exclude_source": ["Building", "Parcel", "Zone", "Road"],
        "requires": {
            "source": ["bjd_code", "longitude", "latitude"],
            "target": ["bjd_code", "longitude", "latitude"],
        },
        "params": {
            "prefix_key": "bjd_code",
            "prefix_length": 10,
            "radius_m": 200,
            "max_targets": 1,
            "batch_size": 10,
        },
        "description": "법정동코드 접두사 + 200m 근접 → 최근접 필지",
    },

    # ══════════════════════════════════════════════════════════════════
    #  Strategy: through_relationship  (경유 관계)
    # ══════════════════════════════════════════════════════════════════
    {
        # COLOCATED via shared Parcel (PNU 정확 매칭 기반)
        "rel_type": "COLOCATED",
        "strategy": "through_relationship",
        "source_labels": ["*"],
        "target_labels": ["*"],
        "exclude_source": ["Parcel", "Zone", "Road"],
        "exclude_target": ["Parcel", "Zone", "Road"],
        "requires": {},
        "params": {
            "through_label": "Parcel",
            "through_rel": "ON_PARCEL",
            "max_per_source": 20,
            "batch_size": 200,
        },
        "description": "같은 필지(Parcel) 위 서로 다른 엔티티 연결",
    },
    {
        # COLOCATED via shared RN_CD Road (도로명 코드 단위 동일 도로변)
        # Restores the paper-baseline COLOCATED count after the v2 ON_PARCEL
        # tightening (PNU 19-digit exact match) reduced per-parcel building
        # clustering. Buildings on the same RN_CD-coded road are conceptually
        # "co-located on the same street" and are linked through this second
        # through-relationship variant.
        "rel_type": "COLOCATED",
        "strategy": "through_relationship",
        "source_labels": ["Building"],
        "target_labels": ["Building"],
        "requires": {},
        "params": {
            "through_label": "Road",
            "through_rel": "ON_STREET",
            "max_per_source": 20,
            "batch_size": 200,
        },
        "description": "같은 도로(RN_CD) 위 서로 다른 건축물 연결",
    },
]
