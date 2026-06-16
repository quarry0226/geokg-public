"""Neo4j database client using py2neo.

Region-aware: the active region is read from a ContextVar (set per-request by
``RegionMiddleware``). Each region is mapped to its own Neo4j database
(``geokg`` for Yuseong, ``geokg-<region>`` otherwise). Graph instances are
cached per region.
"""

import contextvars
from py2neo import Graph, Node, Relationship, NodeMatcher, RelationshipMatcher
from backend.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE, REGION


_REGION_VAR = contextvars.ContextVar("geokg_region", default=None)


def set_region(region: str) -> None:
    """Set the active region for the current request context."""
    _REGION_VAR.set((region or "").lower() or None)


def get_region() -> str:
    """Return active region, falling back to import-time REGION."""
    return _REGION_VAR.get() or REGION


def _db_name_for(region: str) -> str:
    """Map region name to Neo4j database name."""
    region = (region or "").lower()
    if region in ("", "yuseong"):
        return "geokg"
    return f"geokg-{region}"


class Neo4jClient:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._graphs: dict[str, Graph] = {}
        self._initialized = True

    @property
    def graph(self) -> Graph:
        region = get_region()
        if region not in self._graphs:
            db_name = _db_name_for(region)
            self._graphs[region] = Graph(
                NEO4J_URI,
                auth=(NEO4J_USER, NEO4J_PASSWORD),
                name=db_name,
            )
        return self._graphs[region]

    def ensure_database(self):
        """Create the active-region database if it doesn't exist (requires Enterprise)."""
        db_name = _db_name_for(get_region())
        system_graph = Graph(
            NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD), name="system"
        )
        system_graph.run(f"CREATE DATABASE `{db_name}` IF NOT EXISTS")

    def init_schema(self, constraints: list[str]):
        """Run a list of Cypher constraint/index statements."""
        for stmt in constraints:
            try:
                self.graph.run(stmt)
            except Exception as e:
                # Ignore "already exists" errors for constraints/indexes
                if "AlreadyExists" in str(e) or "already exists" in str(e):
                    pass
                else:
                    raise

    def clear_all(self):
        """Delete all nodes and relationships (batched for large graphs)."""
        # Batched deletion to avoid Java heap OOM on large graphs
        while True:
            result = self.graph.run(
                "MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(*) AS deleted"
            ).data()
            deleted = result[0]["deleted"] if result else 0
            if deleted == 0:
                break
            print(f"  [clear_all] Deleted {deleted} nodes...")

    # --- Node operations ---

    def create_node(self, label: str, **properties) -> Node:
        node = Node(label, **properties)
        self.graph.create(node)
        return node

    def merge_node(self, label: str, primary_key: str, primary_value, **properties) -> Node:
        """Merge (get or create) a node by primary key."""
        matcher = NodeMatcher(self.graph)
        node = matcher.match(label, **{primary_key: primary_value}).first()
        if node is None:
            node = Node(label, **{primary_key: primary_value, **properties})
            self.graph.create(node)
        else:
            node.update(properties)
            self.graph.push(node)
        return node

    def get_node(self, label: str, **properties) -> Node | None:
        matcher = NodeMatcher(self.graph)
        return matcher.match(label, **properties).first()

    def get_nodes(self, label: str, **filters) -> list[Node]:
        matcher = NodeMatcher(self.graph)
        return list(matcher.match(label, **filters))

    def update_node(self, label: str, primary_key: str, primary_value, **properties):
        node = self.get_node(label, **{primary_key: primary_value})
        if node:
            node.update(properties)
            self.graph.push(node)
        return node

    # --- Relationship operations ---

    def create_relationship(self, start_node: Node, rel_type: str, end_node: Node, **properties) -> Relationship:
        rel = Relationship(start_node, rel_type, end_node, **properties)
        self.graph.create(rel)
        return rel

    def get_relationships(self, rel_type: str = None) -> list:
        matcher = RelationshipMatcher(self.graph)
        if rel_type:
            return list(matcher.match(r_type=rel_type))
        return list(matcher.match())

    # --- Query operations ---

    def query(self, cypher: str, **params) -> list[dict]:
        result = self.graph.run(cypher, **params)
        return result.data()

    def batch_update(self, cypher: str, batch_params: list[dict]):
        """Run a parameterized Cypher statement for each set of params in a single transaction."""
        tx = self.graph.begin()
        for params in batch_params:
            tx.run(cypher, **params)
        self.graph.commit(tx)

    def batch_create_nodes(self, label: str, nodes: list[dict], batch_size: int = 500):
        """Create nodes in batches using UNWIND for performance."""
        for i in range(0, len(nodes), batch_size):
            batch = nodes[i:i + batch_size]
            self.graph.run(
                f"UNWIND $batch AS props "
                f"MERGE (n:{label} {{uid: props.uid}}) "
                f"SET n += props",
                batch=batch,
            )

    def batch_create_relationships(self, rels: list[dict], batch_size: int = 500):
        """Create relationships in batches using UNWIND. Each rel: {from, to, type}."""
        # Group by relationship type for efficient batch creation
        from collections import defaultdict
        by_type = defaultdict(list)
        for r in rels:
            by_type[r["type"]].append({"from_uid": r["from"], "to_uid": r["to"]})

        for rel_type, items in by_type.items():
            for i in range(0, len(items), batch_size):
                batch = items[i:i + batch_size]
                self.graph.run(
                    f"UNWIND $batch AS rel "
                    f"MATCH (a {{uid: rel.from_uid}}), (b {{uid: rel.to_uid}}) "
                    f"CREATE (a)-[:{rel_type}]->(b)",
                    batch=batch,
                )

    # --- Graph statistics ---

    def stats(self) -> dict:
        node_count = self.graph.run("MATCH (n) RETURN count(n) as c").data()[0]["c"]
        rel_count = self.graph.run("MATCH ()-[r]->() RETURN count(r) as c").data()[0]["c"]
        labels = self.graph.run("CALL db.labels() YIELD label RETURN collect(label) as labels").data()[0]["labels"]
        rel_types = self.graph.run("CALL db.relationshipTypes() YIELD relationshipType RETURN collect(relationshipType) as types").data()[0]["types"]
        return {
            "node_count": node_count,
            "relationship_count": rel_count,
            "labels": labels,
            "relationship_types": rel_types,
        }


db = Neo4jClient()
