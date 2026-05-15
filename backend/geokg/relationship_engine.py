"""
Rule-Based Relationship Engine for GeoKG.

Reads declarative rules from relationship_rules.py and executes them
using strategy-specific handlers.  Any new entity type automatically
participates in rules whose source_labels include "*" and whose
required properties the entity possesses.

Strategies:
  1. attribute_match           — exact key match (indexed)
  2. text_contains             — substring match
  3. nearest                   — nearest single target
  4. same_attribute_cluster    — grid-sampled same-attribute pairs
  5. proximity                 — radius-based multi-target
  6. attribute_prefix_proximity— prefix match + proximity
  7. through_relationship      — indirect via shared intermediate node
"""

import json
import time
from collections import defaultdict

from backend.geokg.relationship_rules import RELATIONSHIP_RULES
from backend.geokg.ontology import ENTITY_DOMAIN_LABELS


# ───────────────────────────────────────────────────────────────────
# Public entry point
# ───────────────────────────────────────────────────────────────────

def enrich_by_rules(db):
    """Execute all relationship rules in order. Returns timing report dict."""
    print("[RuleEngine] Starting rule-based relationship enrichment...")

    timing = {
        "rules": [],           # per-rule timing [{rule_idx, rel_type, strategy, elapsed, rels_before, rels_after}]
        "strategies": {},      # aggregated by strategy name
        "total_elapsed": 0,
    }
    t_total = time.time()

    for idx, rule in enumerate(RELATIONSHIP_RULES):
        rel_type = rule["rel_type"]
        strategy = rule["strategy"]
        desc = rule.get("description", "")

        handler = _STRATEGY_HANDLERS.get(strategy)
        if not handler:
            print(f"[RuleEngine] WARNING: Unknown strategy '{strategy}' for {rel_type}, skipping.")
            continue

        # Resolve source labels
        source_labels = _resolve_labels(
            db, rule.get("source_labels", []),
            rule.get("exclude_source", []),
        )

        if not source_labels:
            continue

        # Count relationships before
        count_before = _count_rels(db, rel_type)

        t_rule = time.time()
        handler(db, rule, source_labels)
        elapsed = time.time() - t_rule

        # Count relationships after
        count_after = _count_rels(db, rel_type)
        created = count_after - count_before

        rule_timing = {
            "rule_idx": idx + 1,
            "rel_type": rel_type,
            "strategy": strategy,
            "elapsed": round(elapsed, 3),
            "rels_created": created,
            "source_labels": source_labels,
        }
        timing["rules"].append(rule_timing)

        # Aggregate by strategy
        if strategy not in timing["strategies"]:
            timing["strategies"][strategy] = {"elapsed": 0, "rels_created": 0, "rule_count": 0}
        timing["strategies"][strategy]["elapsed"] += elapsed
        timing["strategies"][strategy]["rels_created"] += created
        timing["strategies"][strategy]["rule_count"] += 1

        print(f"[RuleEngine] Rule {idx + 1}: {rel_type} ({strategy}) → {created} rels in {elapsed:.2f}s")

    timing["total_elapsed"] = round(time.time() - t_total, 3)

    # Print summary
    print(f"\n[RuleEngine] ═══ Timing Report (Rule Engine) ═══")
    print(f"  {'Strategy':<30} {'Rules':>5} {'Rels':>10} {'Time(s)':>10} {'Avg(ms/rel)':>12}")
    print(f"  {'─' * 70}")
    for strat, info in sorted(timing["strategies"].items(), key=lambda x: -x[1]["elapsed"]):
        avg_ms = (info["elapsed"] / info["rels_created"] * 1000) if info["rels_created"] > 0 else 0
        print(f"  {strat:<30} {info['rule_count']:>5} {info['rels_created']:>10,} {info['elapsed']:>10.2f} {avg_ms:>12.4f}")
    print(f"  {'─' * 70}")
    total_rels = sum(s["rels_created"] for s in timing["strategies"].values())
    print(f"  {'TOTAL':<30} {len(timing['rules']):>5} {total_rels:>10,} {timing['total_elapsed']:>10.2f}")
    print()

    print("[RuleEngine] Rule-based relationship enrichment complete.")
    return timing


def _count_rels(db, rel_type):
    """Count existing relationships of a given type."""
    try:
        result = db.query(f"MATCH ()-[r:{rel_type}]->() RETURN count(r) AS c")
        return result[0]["c"] if result else 0
    except Exception:
        return 0


# ───────────────────────────────────────────────────────────────────
# Label resolution helpers
# ───────────────────────────────────────────────────────────────────

def _resolve_labels(db, label_spec, exclude):
    """Resolve label specification to concrete labels present in the graph."""
    if "*" in label_spec:
        # All Entity domain labels that actually exist in the graph
        labels = _get_existing_labels(db, ENTITY_DOMAIN_LABELS)
    else:
        labels = _get_existing_labels(db, label_spec)

    return [l for l in labels if l not in exclude]


_label_cache = {}

def _get_existing_labels(db, candidates):
    """Return only labels that actually have nodes in the graph (cached)."""
    result = []
    for label in candidates:
        if label not in _label_cache:
            cnt = db.query(f"MATCH (n:{label}) RETURN count(n) AS c")
            _label_cache[label] = cnt[0]["c"] if cnt else 0
        if _label_cache[label] > 0:
            result.append(label)
    return result


def _check_props_exist(db, label, props):
    """Check that at least one node of the label has the required properties."""
    if not props:
        return True
    conditions = " AND ".join(f"n.{p} IS NOT NULL" for p in props)
    q = f"MATCH (n:{label}) WHERE {conditions} RETURN count(n) AS c LIMIT 1"
    r = db.query(q)
    return r and r[0]["c"] > 0


def _clear_label_cache():
    """Clear the label existence cache (call at start of reseed)."""
    global _label_cache
    _label_cache = {}


# ───────────────────────────────────────────────────────────────────
# Strategy 1: attribute_match
# ───────────────────────────────────────────────────────────────────

def _handle_attribute_match(db, rule, source_labels):
    """Exact key match between source and target nodes (indexed lookup)."""
    rel_type = rule["rel_type"]
    src_key = rule["params"]["source_key"]
    tgt_key = rule["params"]["target_key"]
    tgt_label = rule["target_labels"][0]

    for src_label in source_labels:
        if not _check_props_exist(db, src_label, rule["requires"].get("source", [])):
            continue

        result = db.query(
            f"""
            MATCH (s:{src_label})
            WHERE s.{src_key} IS NOT NULL AND s.{src_key} <> ''
            WITH s
            MATCH (t:{tgt_label} {{{tgt_key}: s.{src_key}}})
            CREATE (s)-[:{rel_type}]->(t)
            RETURN count(*) AS cnt
            """
        )
        cnt = result[0]["cnt"] if result else 0
        if cnt > 0:
            print(f"[RuleEngine] {rel_type}: {cnt} ({src_label} → {tgt_label}, attribute_match)")


# ───────────────────────────────────────────────────────────────────
# Strategy 2: text_contains
# ───────────────────────────────────────────────────────────────────

def _handle_text_contains(db, rule, source_labels):
    """Substring match: source field CONTAINS target name."""
    rel_type = rule["rel_type"]
    src_field = rule["params"]["source_field"]
    tgt_field = rule["params"]["target_field"]
    tgt_label = rule["target_labels"][0]

    # Get all target nodes with their name field
    targets = db.query(f"MATCH (t:{tgt_label}) RETURN t.uid AS uid, t.{tgt_field} AS name")
    if not targets:
        return

    total = 0
    for src_label in source_labels:
        if not _check_props_exist(db, src_label, rule["requires"].get("source", [])):
            continue
        for tgt in targets:
            if not tgt["name"]:
                continue
            result = db.query(
                f"""
                MATCH (s:{src_label}), (t:{tgt_label} {{uid: $tgt_uid}})
                WHERE s.{src_field} CONTAINS $tgt_name
                CREATE (s)-[:{rel_type}]->(t)
                RETURN count(*) AS cnt
                """,
                tgt_uid=tgt["uid"],
                tgt_name=tgt["name"],
            )
            n = result[0]["cnt"] if result else 0
            total += n
            if n > 0:
                print(f"  [{rel_type}] {tgt['name']}: {n} ({src_label})")

    if total > 0:
        print(f"[RuleEngine] {rel_type}: {total} total (text_contains)")


# ───────────────────────────────────────────────────────────────────
# Strategy 3: nearest
# ───────────────────────────────────────────────────────────────────

def _handle_nearest(db, rule, source_labels):
    """Connect each source to its single nearest target (batched for large counts)."""
    rel_type = rule["rel_type"]
    tgt_label = rule["target_labels"][0]
    exclude_if_rel = rule["params"].get("exclude_if_rel", "")

    # Get target nodes with coordinates
    targets = db.query(
        f"MATCH (t:{tgt_label}) WHERE t.longitude IS NOT NULL "
        f"RETURN t.uid AS uid, t.longitude AS lon, t.latitude AS lat"
    )
    if not targets:
        return

    # For Road targets that may use coordinates JSON instead of lon/lat
    if not targets or all(t["lon"] is None for t in targets):
        targets = db.query(
            f"MATCH (t:{tgt_label}) WHERE t.coordinates IS NOT NULL "
            f"RETURN t.uid AS uid, t.coordinates AS coords"
        )
        road_points = []
        for t in targets:
            coords = json.loads(t["coords"]) if t["coords"] else []
            if coords:
                mid = coords[len(coords) // 2]
                road_points.append({"uid": t["uid"], "lon": mid[0], "lat": mid[1]})
        targets = road_points

    if not targets:
        return

    # Build target list for UNWIND
    tgt_list = [{"uid": t["uid"], "lon": t["lon"], "lat": t["lat"]}
                for t in targets if t.get("lon") is not None]
    if not tgt_list:
        return

    total = 0
    # ── Adaptive batch sizing ─────────────────────────────────────────
    # The nearest-strategy Cypher creates a Cartesian product of size
    #     batch_size × len(tgt_list)
    # inside a single transaction. Neo4j's default per-transaction memory
    # pool is 716.8 MiB; at ~1 KiB per intermediate row, that caps us at
    # roughly 7 × 10^5 rows per transaction. We size the batch so the
    # Cartesian stays comfortably under that ceiling on every region —
    # regardless of how many targets a particular city has — so the
    # OOM fallback never fires in steady-state operation.
    MAX_PAIRS_PER_BATCH = 700_000
    MAX_BATCH = 500   # Cypher param-list cap (py2neo round-trip cost)
    MIN_BATCH = 25    # Sanity floor — even a 25K-road region stays safe
    n_targets = len(tgt_list)
    batch_size = max(MIN_BATCH, min(MAX_BATCH, MAX_PAIRS_PER_BATCH // max(n_targets, 1)))
    print(f"[RuleEngine] {rel_type} batch_size auto-tuned to {batch_size} "
          f"({n_targets:,} {tgt_label} targets × {batch_size} sources "
          f"= {n_targets * batch_size:,} pairs/tx)")

    for src_label in source_labels:
        if not _check_props_exist(db, src_label, rule["requires"].get("source", [])):
            continue

        # exclude_if_rel: source already has the named relationship to ANY
        # target → skip the fallback. Earlier we used (s)->(r) which only
        # filtered the specific nearest road; that produced FRONTS_ROAD
        # edges to a *different* road than the source's existing ON_STREET
        # in 25,848 cases (paper R1.6 contract: "23 % via FRONTS_ROAD only").
        exclude_clause = ""
        if exclude_if_rel:
            exclude_clause = f"WHERE NOT (s)-[:{exclude_if_rel}]->()"

        # Get source node UIDs for batching, filtered by exclude_if_rel
        if exclude_if_rel:
            src_nodes = db.query(
                f"""
                MATCH (s:{src_label})
                WHERE s.longitude IS NOT NULL
                  AND NOT (s)-[:{exclude_if_rel}]->()
                RETURN s.uid AS uid
                """
            )
        else:
            src_nodes = db.query(
                f"MATCH (s:{src_label}) WHERE s.longitude IS NOT NULL "
                f"RETURN s.uid AS uid"
            )
        if not src_nodes:
            continue

        # Cypher template — single source for both primary and backoff paths
        nearest_query = f"""
            UNWIND $uids AS src_uid
            MATCH (s:{src_label} {{uid: src_uid}})
            WITH s,
                 coalesce(s.entrance_lon, s.longitude) AS slon,
                 coalesce(s.entrance_lat, s.latitude)  AS slat
            UNWIND $targets AS tgt
            WITH s, slon, slat, tgt,
                 (slon - tgt.lon) * 111320 * cos(radians(slat)) AS dx,
                 (slat - tgt.lat) * 110540 AS dy
            WITH s, tgt, dx*dx + dy*dy AS dist_sq
            ORDER BY s.uid, dist_sq
            WITH s, collect(tgt)[0] AS nearest
            MATCH (r:{tgt_label} {{uid: nearest.uid}})
            {exclude_clause}
            CREATE (s)-[:{rel_type}]->(r)
            RETURN count(*) AS cnt
        """

        def _exec_batch(uid_batch: list, current_size: int) -> int:
            """Run nearest query with binary-backoff on MemoryPool failure.

            Returns count of created edges. Recursively halves the batch
            on OOM until it either succeeds or hits MIN_BATCH-of-1.
            """
            try:
                result = db.query(
                    nearest_query, uids=uid_batch, targets=tgt_list,
                )
                return result[0]["cnt"] if result else 0
            except Exception as e:
                msg = str(e)
                if ("MemoryPool" in msg or "OutOfMemory" in msg) and len(uid_batch) > 1:
                    # Binary backoff: half the batch, retry both halves.
                    # This keeps Cypher-level batching (orders of magnitude
                    # faster than per-uid execution) while staying under
                    # the per-transaction memory limit.
                    half = max(1, len(uid_batch) // 2)
                    return (_exec_batch(uid_batch[:half], half) +
                            _exec_batch(uid_batch[half:], len(uid_batch) - half))
                if ("MemoryPool" in msg or "OutOfMemory" in msg):
                    # Single uid still OOM — skip silently (target list too large).
                    return 0
                raise

        for i in range(0, len(src_nodes), batch_size):
            batch_uids = [n["uid"] for n in src_nodes[i:i + batch_size]]
            total += _exec_batch(batch_uids, len(batch_uids))

    if total > 0:
        print(f"[RuleEngine] {rel_type}: {total} (nearest)")


# ───────────────────────────────────────────────────────────────────
# Strategy 4: same_attribute_cluster
# ───────────────────────────────────────────────────────────────────

def _handle_same_attribute_cluster(db, rule, source_labels):
    """Grid-based clustering with 9-cell neighborhood and all-pairs matching."""
    rel_type = rule["rel_type"]
    group_key = rule["params"]["group_key"]
    grid_deg = rule["params"]["grid_size_deg"]
    max_per_node = rule["params"].get("max_per_node", 5)
    src_label = source_labels[0]  # same_attribute_cluster is always self-referencing

    rows = db.query(
        f"""
        MATCH (n:{src_label})
        WHERE n.{group_key} IS NOT NULL AND n.{group_key} <> ''
              AND n.longitude IS NOT NULL
        RETURN n.uid AS uid, n.longitude AS lon, n.latitude AS lat,
               n.{group_key} AS grp
        """
    )
    if not rows:
        return

    # Grid grouping by (cell_x, cell_y, group_value)
    grid = defaultdict(list)
    inv_grid = 1.0 / grid_deg if grid_deg > 0 else 500
    node_cell = {}  # uid -> (gx, gy, grp)
    for r in rows:
        gx = int(r["lon"] * inv_grid)
        gy = int(r["lat"] * inv_grid)
        grid[(gx, gy, r["grp"])].append(r["uid"])
        node_cell[r["uid"]] = (gx, gy, r["grp"])

    # Build pairs with 9-cell neighborhood (fixes cell boundary problem)
    seen_pairs = set()
    per_node = defaultdict(int)
    pairs = []

    for (gx, gy, grp), cell_uids in grid.items():
        # Collect candidates from same group in 9-cell neighborhood
        candidates = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                candidates.extend(grid.get((gx + dx, gy + dy, grp), []))

        for uid1 in cell_uids:
            if per_node[uid1] >= max_per_node:
                continue
            for uid2 in candidates:
                if uid1 >= uid2:  # avoid self & duplicates
                    continue
                if per_node[uid2] >= max_per_node:
                    continue
                pair = (uid1, uid2) if uid1 < uid2 else (uid2, uid1)
                if pair not in seen_pairs:
                    seen_pairs.add(pair)
                    pairs.append({"from_uid": uid1, "to_uid": uid2})
                    per_node[uid1] += 1
                    per_node[uid2] += 1

    # Batch create
    batch_size = 500
    count = 0
    for i in range(0, len(pairs), batch_size):
        batch = pairs[i:i + batch_size]
        db.graph.run(
            f"""
            UNWIND $batch AS rel
            MATCH (a:{src_label} {{uid: rel.from_uid}}), (b:{src_label} {{uid: rel.to_uid}})
            CREATE (a)-[:{rel_type}]->(b)
            """,
            batch=batch,
        )
        count += len(batch)

    if count > 0:
        print(f"[RuleEngine] {rel_type}: {count} (same_attribute_cluster on {group_key})")


# ───────────────────────────────────────────────────────────────────
# Strategy 5: proximity
# ───────────────────────────────────────────────────────────────────

def _handle_proximity(db, rule, source_labels):
    """Radius-based multi-target proximity matching."""
    rel_type = rule["rel_type"]
    tgt_labels = rule["target_labels"]
    radius_m = rule["params"]["radius_m"]
    max_targets = rule["params"]["max_targets"]
    batch_size = rule["params"].get("batch_size", 0)
    exclude_same_uid = rule["params"].get("exclude_same_uid", False)
    target_iot_types = rule.get("target_iot_types")

    radius_deg = radius_m / 111320.0

    for src_label in source_labels:
        if not _check_props_exist(db, src_label, rule["requires"].get("source", [])):
            continue

        for tgt_label in tgt_labels:
            # Build target filter clause
            tgt_filter = ""
            if target_iot_types:
                types_str = ", ".join(f"'{t}'" for t in target_iot_types)
                tgt_filter = f"AND t.iot_type IN [{types_str}]"

            same_uid_filter = ""
            if exclude_same_uid or (src_label == tgt_label):
                same_uid_filter = "AND s.uid <> t.uid"

            if batch_size > 0:
                _proximity_batched(
                    db, rel_type, src_label, tgt_label,
                    radius_m, radius_deg, max_targets, batch_size,
                    tgt_filter, same_uid_filter,
                )
            else:
                _proximity_single(
                    db, rel_type, src_label, tgt_label,
                    radius_m, radius_deg, max_targets,
                    tgt_filter, same_uid_filter,
                )


def _proximity_single(db, rel_type, src_label, tgt_label,
                       radius_m, radius_deg, max_targets,
                       tgt_filter, same_uid_filter):
    """Single-query proximity (for small source counts)."""
    result = db.query(
        f"""
        MATCH (s:{src_label})
        WHERE s.longitude IS NOT NULL
        MATCH (t:{tgt_label})
        WHERE t.longitude IS NOT NULL
              AND abs(t.longitude - s.longitude) < $radius
              AND abs(t.latitude - s.latitude) < $radius
              {tgt_filter}
              {same_uid_filter}
        WITH s, t,
             (t.longitude - s.longitude) * 111320 * cos(radians(s.latitude)) AS dx,
             (t.latitude - s.latitude) * 110540 AS dy
        WITH s, t, sqrt(dx*dx + dy*dy) AS dist_m
        WHERE dist_m < $radius_m
        WITH s, t, dist_m
        ORDER BY s.uid, dist_m
        WITH s, collect(t)[..{max_targets}] AS nearby
        UNWIND nearby AS nb
        CREATE (s)-[:{rel_type}]->(nb)
        RETURN count(*) AS cnt
        """,
        radius=radius_deg,
        radius_m=radius_m,
    )
    cnt = result[0]["cnt"] if result else 0
    if cnt > 0:
        print(f"[RuleEngine] {rel_type}: {cnt} ({src_label} → {tgt_label}, proximity {radius_m}m)")


def _proximity_batched(db, rel_type, src_label, tgt_label,
                        radius_m, radius_deg, max_targets, batch_size,
                        tgt_filter, same_uid_filter):
    """Batched proximity for large source counts (memory-safe).

    Uses a binary-backoff strategy on MemoryPoolOutOfMemoryError so the
    same code path handles any region — small (Yuseong) or large (Sejong,
    Daejeon-wide, etc.) — without per-city tuning. When a Cypher
    transaction trips the per-tx memory ceiling, the batch is halved
    and retried, preserving Cypher-level batching efficiency while
    staying inside the JVM transaction pool.
    """
    src_nodes = db.query(
        f"MATCH (s:{src_label}) WHERE s.longitude IS NOT NULL "
        f"RETURN s.uid AS uid"
    )
    if not src_nodes:
        return

    proximity_query = f"""
        UNWIND $uids AS src_uid
        MATCH (s:{src_label} {{uid: src_uid}})
        MATCH (t:{tgt_label})
        WHERE t.longitude IS NOT NULL
              AND abs(t.longitude - s.longitude) < $radius
              AND abs(t.latitude - s.latitude) < $radius
              {tgt_filter}
              {same_uid_filter}
        WITH s, t,
             (t.longitude - s.longitude) * 111320 * cos(radians(s.latitude)) AS dx,
             (t.latitude - s.latitude) * 110540 AS dy
        WITH s, t, sqrt(dx*dx + dy*dy) AS dist_m
        WHERE dist_m < $radius_m
        WITH s, t, dist_m
        ORDER BY s.uid, dist_m
        WITH s, collect(t)[..{max_targets}] AS nearby
        UNWIND nearby AS nb
        CREATE (s)-[:{rel_type}]->(nb)
        RETURN count(*) AS cnt
    """

    def _exec_batch(uid_batch: list) -> int:
        try:
            result = db.query(
                proximity_query,
                uids=uid_batch, radius=radius_deg, radius_m=radius_m,
            )
            return result[0]["cnt"] if result else 0
        except Exception as e:
            msg = str(e)
            if ("MemoryPool" in msg or "OutOfMemory" in msg) and len(uid_batch) > 1:
                half = max(1, len(uid_batch) // 2)
                return (_exec_batch(uid_batch[:half]) +
                        _exec_batch(uid_batch[half:]))
            if "MemoryPool" in msg or "OutOfMemory" in msg:
                return 0
            raise

    count = 0
    for i in range(0, len(src_nodes), batch_size):
        batch_uids = [n["uid"] for n in src_nodes[i:i + batch_size]]
        count += _exec_batch(batch_uids)

    if count > 0:
        print(f"[RuleEngine] {rel_type}: {count} ({src_label} → {tgt_label}, proximity {radius_m}m, batched)")


# ───────────────────────────────────────────────────────────────────
# Strategy 6: attribute_prefix_proximity
# ───────────────────────────────────────────────────────────────────

def _handle_attribute_prefix_proximity(db, rule, source_labels):
    """Prefix match on an attribute + proximity to find nearest target."""
    rel_type = rule["rel_type"]
    tgt_label = rule["target_labels"][0]
    prefix_key = rule["params"]["prefix_key"]
    prefix_len = rule["params"]["prefix_length"]
    radius_m = rule["params"]["radius_m"]
    max_targets = rule["params"]["max_targets"]
    batch_size = rule["params"].get("batch_size", 10)

    radius_deg = radius_m / 111320.0

    for src_label in source_labels:
        if not _check_props_exist(db, src_label, rule["requires"].get("source", [])):
            continue

        # Get source UIDs
        src_nodes = db.query(
            f"MATCH (s:{src_label}) WHERE s.longitude IS NOT NULL "
            f"AND s.{prefix_key} IS NOT NULL AND s.{prefix_key} <> '' "
            f"RETURN s.uid AS uid"
        )
        if not src_nodes:
            continue

        count = 0
        for i in range(0, len(src_nodes), batch_size):
            batch_uids = [n["uid"] for n in src_nodes[i:i + batch_size]]
            try:
                result = db.query(
                    f"""
                    UNWIND $uids AS src_uid
                    MATCH (s:{src_label} {{uid: src_uid}})
                    MATCH (t:{tgt_label})
                    WHERE t.{prefix_key} IS NOT NULL
                          AND left(t.{prefix_key}, {prefix_len}) = left(s.{prefix_key}, {prefix_len})
                          AND t.longitude IS NOT NULL
                          AND abs(t.longitude - s.longitude) < $radius
                          AND abs(t.latitude - s.latitude) < $radius
                    WITH s, t,
                         (t.longitude - s.longitude) * 111320 * cos(radians(s.latitude)) AS dx,
                         (t.latitude - s.latitude) * 110540 AS dy
                    WITH s, t, sqrt(dx*dx + dy*dy) AS dist_m
                    WHERE dist_m < $radius_m
                    WITH s, t, dist_m
                    ORDER BY s.uid, dist_m
                    WITH s, collect(t)[..{max_targets}] AS nearest
                    UNWIND nearest AS nb
                    CREATE (s)-[:{rel_type}]->(nb)
                    RETURN count(*) AS cnt
                    """,
                    uids=batch_uids,
                    radius=radius_deg,
                    radius_m=radius_m,
                )
                count += result[0]["cnt"] if result else 0
            except Exception as e:
                if "MemoryPool" in str(e):
                    for uid in batch_uids:
                        try:
                            result = db.query(
                                f"""
                                MATCH (s:{src_label} {{uid: $uid}})
                                MATCH (t:{tgt_label})
                                WHERE t.{prefix_key} IS NOT NULL
                                      AND left(t.{prefix_key}, {prefix_len}) = left(s.{prefix_key}, {prefix_len})
                                      AND t.longitude IS NOT NULL
                                      AND abs(t.longitude - s.longitude) < $radius
                                      AND abs(t.latitude - s.latitude) < $radius
                                WITH s, t,
                                     (t.longitude - s.longitude) * 111320 * cos(radians(s.latitude)) AS dx,
                                     (t.latitude - s.latitude) * 110540 AS dy
                                WITH s, t, sqrt(dx*dx + dy*dy) AS dist_m
                                WHERE dist_m < $radius_m
                                WITH s, t, dist_m
                                ORDER BY dist_m
                                WITH s, collect(t)[..{max_targets}] AS nearest
                                UNWIND nearest AS nb
                                CREATE (s)-[:{rel_type}]->(nb)
                                RETURN count(*) AS cnt
                                """,
                                uid=uid,
                                radius=radius_deg,
                                radius_m=radius_m,
                            )
                            count += result[0]["cnt"] if result else 0
                        except Exception:
                            pass
                else:
                    raise

        if count > 0:
            print(f"[RuleEngine] {rel_type}: {count} ({src_label} → {tgt_label}, prefix_proximity)")


# ───────────────────────────────────────────────────────────────────
# Strategy 7: through_relationship
# ───────────────────────────────────────────────────────────────────

def _handle_through_relationship(db, rule, source_labels):
    """Connect entities that share a common intermediate node."""
    rel_type = rule["rel_type"]
    through_label = rule["params"]["through_label"]
    through_rel = rule["params"]["through_rel"]
    max_per_src = rule["params"].get("max_per_source", 5)
    batch_size = rule["params"].get("batch_size", 200)
    exclude_target = rule.get("exclude_target", [])

    for src_label in source_labels:
        # Get source UIDs that have the through_rel
        src_nodes = db.query(
            f"MATCH (s:{src_label})-[:{through_rel}]->(:{through_label}) "
            f"RETURN DISTINCT s.uid AS uid"
        )
        if not src_nodes:
            continue

        count = 0
        for i in range(0, len(src_nodes), batch_size):
            batch_uids = [n["uid"] for n in src_nodes[i:i + batch_size]]
            try:
                result = db.query(
                    f"""
                    UNWIND $uids AS src_uid
                    MATCH (s:{src_label} {{uid: src_uid}})-[:{through_rel}]->(mid:{through_label})<-[:{through_rel}]-(t)
                    WHERE s.uid <> t.uid
                    WITH s, t, mid
                    WITH s, collect(DISTINCT t)[..{max_per_src}] AS colocated
                    UNWIND colocated AS nb
                    CREATE (s)-[:{rel_type}]->(nb)
                    RETURN count(*) AS cnt
                    """,
                    uids=batch_uids,
                )
                count += result[0]["cnt"] if result else 0
            except Exception as e:
                if "MemoryPool" in str(e):
                    for uid in batch_uids:
                        try:
                            result = db.query(
                                f"""
                                MATCH (s:{src_label} {{uid: $uid}})-[:{through_rel}]->(mid:{through_label})<-[:{through_rel}]-(t)
                                WHERE s.uid <> t.uid
                                WITH s, collect(DISTINCT t)[..{max_per_src}] AS colocated
                                UNWIND colocated AS nb
                                CREATE (s)-[:{rel_type}]->(nb)
                                RETURN count(*) AS cnt
                                """,
                                uid=uid,
                            )
                            count += result[0]["cnt"] if result else 0
                        except Exception:
                            pass
                else:
                    raise

        if count > 0:
            print(f"[RuleEngine] {rel_type}: {count} ({src_label} via {through_label}, through_relationship)")


# ───────────────────────────────────────────────────────────────────
# Strategy handler registry
# ───────────────────────────────────────────────────────────────────

_STRATEGY_HANDLERS = {
    "attribute_match": _handle_attribute_match,
    "text_contains": _handle_text_contains,
    "nearest": _handle_nearest,
    "same_attribute_cluster": _handle_same_attribute_cluster,
    "proximity": _handle_proximity,
    "attribute_prefix_proximity": _handle_attribute_prefix_proximity,
    "through_relationship": _handle_through_relationship,
}
