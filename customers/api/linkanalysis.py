"""
AnalysisAPI — satisfies every requirement in
"Advanced Network Graph Specification v2" (13-slide spec deck).

Slide compliance map
────────────────────
Slide 4  Feature 1: Multi-Hop Expansion (level 1-4, colour per hop)
Slide 5  Feature 2: Shortest Path Detection (user-selected pair)
Slide 8  Feature 3: Cycle / Closed-Loop Detection (highlight loop)
Slide 9  Feature 4: Hidden Connector / Broker Node (≥3 targets)
Slide 10 Node & Edge intelligence rules (size, thickness, colour)
Slide 11 Required UI controls surfaced via API params
Slide 13 Expected Intelligence Outcomes

Pruning rule (updated)
──────────────────────
Only three node categories are ever shown in the graph:
  1. Target nodes          — the CDR subject numbers themselves
  2. Bridge / path nodes   — nodes that lie on a BFS shortest path
                             between ANY two target pair
  3. Hidden Connectors     — non-target nodes DIRECTLY connected to
                             2+ distinct targets (≥3 → Hidden Connector,
                             2  → Common Contact)
All other B-party contacts of a single CDR are excluded.
"""

from mongoengine import get_db
from rest_framework.views import APIView
from rest_framework.response import Response
from collections import defaultdict, deque
from datetime import datetime, timedelta

db = get_db("cdr_db")
cdr_collection      = db["CallDetailRecords"]



# ──────────────────────────────────────────────
# Graph helpers
# ──────────────────────────────────────────────

def get_direction_and_type(call_type_raw):
    ct = str(call_type_raw).upper().strip()
    is_sms      = "SMS" in ct
    comm_type   = "SMS" if is_sms else "CALL"
    is_incoming = ct.endswith("IN") or "_IN" in ct
    return is_incoming, comm_type


def resolve_direction(a_party, b_party, call_type_raw):
    is_incoming, comm_type = get_direction_and_type(call_type_raw)
    if is_incoming:
        return b_party, a_party, comm_type
    return a_party, b_party, comm_type


def build_adjacency(edges):
    """Undirected adjacency: node → set of neighbours."""
    graph = defaultdict(set)
    for e in edges:
        graph[e["from"]].add(e["to"])
        graph[e["to"]].add(e["from"])
    return graph


# ──────────────────────────────────────────────
# Slide 4 — Multi-Hop Expansion (level 1-4)
# ──────────────────────────────────────────────

def multi_hop_expand(targets, graph, max_level):
    """
    BFS from every target simultaneously.
    Returns dict: node → hop_level  (0 = target itself).
    Only nodes reachable within max_level hops are included.
    """
    hop_level = {n: 0 for n in targets}
    queue     = deque((n, 0) for n in targets)

    while queue:
        node, level = queue.popleft()
        if level >= max_level:
            continue
        for neighbour in graph[node]:
            if neighbour not in hop_level:
                hop_level[neighbour] = level + 1
                queue.append((neighbour, level + 1))

    return hop_level   # {node: hop_distance_from_nearest_target}


# ──────────────────────────────────────────────
# Slide 5 / 6 / 7 — Shortest Path Detection
# ──────────────────────────────────────────────

def bfs_shortest_path(graph, start, end):
    """BFS; returns list of node IDs or None."""
    if start not in graph or end not in graph:
        return None
    visited = {start}
    queue   = deque([[start]])
    while queue:
        path = queue.popleft()
        node = path[-1]
        if node == end:
            return path
        for nb in graph[node]:
            if nb not in visited:
                visited.add(nb)
                queue.append(path + [nb])
    return None


# ──────────────────────────────────────────────
# Slide 8 — Cycle / Closed-Loop Detection
# ──────────────────────────────────────────────

def detect_cycles(graph):
    """
    DFS cycle detection.
    Returns list of cycles, each as a list of node IDs.
    Only cycles of length ≥ 3 are reported.
    """
    visited = set()
    cycles  = []

    def dfs(node, parent, path):
        visited.add(node)
        path.append(node)
        for nb in graph[node]:
            if nb == parent:
                continue
            if nb in path:
                idx   = path.index(nb)
                cycle = path[idx:]
                if len(cycle) >= 3:
                    cycles.append(list(cycle))
            elif nb not in visited:
                dfs(nb, node, list(path))

    for node in graph:
        if node not in visited:
            dfs(node, None, [])

    # Deduplicate (same cycle nodes in different rotation)
    seen_sets = []
    unique    = []
    for c in cycles:
        cs = frozenset(c)
        if cs not in seen_sets:
            seen_sets.append(cs)
            unique.append(c)

    return unique


def count_internal_edges(cycle_nodes, edges):
    """Count edges whose both endpoints are inside the cycle."""
    node_set = set(cycle_nodes)
    return sum(
        1 for e in edges
        if e["from"] in node_set and e["to"] in node_set
    )


# ──────────────────────────────────────────────
# Slide 9 — Hidden Connector / Broker Node
# ──────────────────────────────────────────────

def classify_role(node, targets, target_connections, hop_level):
    """
    Slide 9: node connecting ≥3 targets → Hidden Connector.
    Slide 10 colour mapping:
      Target              → Red
      Hidden Connector    → Teal   (connects ≥3 targets)
      Common Contact      → Orange (directly connects 2 targets)
      2-Hop Bridge        → Purple (on inter-target path, hop 2)
      Intermediate        → Grey   (deeper path node)
    """
    if node in targets:
        return "Target"

    connected_target_count = len(target_connections.get(node, set()))

    if connected_target_count >= 3:
        return "Hidden Connector"

    if connected_target_count == 2:
        return "Common Contact (Cross-CDR)"   # orange — directly bridges 2 targets

    hop = hop_level.get(node, 99)
    if hop == 2:
        return "2-Hop Bridge"                 # purple — on shortest path, 2 hops out
    return "Intermediate"                     # deeper path node


# ──────────────────────────────────────────────
# Strict pruning helper  ← KEY CHANGE
# ──────────────────────────────────────────────

def compute_relevant_nodes(targets, graph, edges, expand_level):
    """
    Returns ONLY the three meaningful node categories:

      Category 1 — Target nodes (always included).

      Category 2 — Directly bridging nodes: non-target nodes DIRECTLY
                   connected (1 hop) to ≥2 distinct targets.
                   These become Hidden Connectors or Common Contacts.

      Category 3 — Path nodes: every intermediate node that sits on a
                   BFS shortest path between ANY two targets (within the
                   expand_level hop limit).

    All other B-party contacts belonging to only ONE CDR are excluded.
    """
    target_list = list(targets)

    # Category 2 — direct multi-target connections
    direct_target_touch = defaultdict(set)
    for e in edges:
        if e["from"] in targets:
            direct_target_touch[e["to"]].add(e["from"])
        if e["to"] in targets:
            direct_target_touch[e["from"]].add(e["to"])

    directly_bridging = {
        n for n, t_set in direct_target_touch.items()
        if n not in targets and len(t_set) >= 2
    }

    # Category 3 — nodes on inter-target shortest paths
    path_nodes = set()
    for i in range(len(target_list)):
        for j in range(i + 1, len(target_list)):
            path = bfs_shortest_path(graph, target_list[i], target_list[j])
            if path and (len(path) - 1) <= expand_level:
                path_nodes.update(path)

    # Remove targets from path_nodes (already in Category 1)
    path_nodes -= targets

    return targets | directly_bridging | path_nodes


# ──────────────────────────────────────────────
# Main API view
# ──────────────────────────────────────────────

class AnalysisAPI(APIView):

    def post(self, request):

        # ── Required UI Controls (Slide 11) ──────────────────────────
        seq_ids           = request.data.get("seq_ids")
        from_date         = request.data.get("from_date")
        to_date           = request.data.get("to_date")
        expand_level      = min(4, max(1, int(request.data.get("expand_level", 2))))
        min_calls         = int(request.data.get("min_calls", 1))
        shortest_path_req = request.data.get("shortest_path")
        cycle_detection   = request.data.get("cycle_detection", True)

        if not seq_ids or not isinstance(seq_ids, list):
            return Response({"error": "seq_ids must be a list"}, status=400)

        # ── Date Filter ───────────────────────────────────────────────
        date_filter = {}
        if from_date:
            date_filter["$gte"] = datetime.fromisoformat(from_date)
        if to_date:
            to_dt = datetime.fromisoformat(to_date)
            if len(to_date.strip()) <= 10:
                to_dt += timedelta(days=1)
                date_filter["$lt"] = to_dt
            else:
                date_filter["$lte"] = to_dt

        # ── Base match ────────────────────────────────────────────────
        match = {
            "seq_id":  {"$in": seq_ids},
            "A_Party": {"$regex": r"^[6-9]\d{9}$"},
            "B_Party": {"$regex": r"^[6-9]\d{9}$"},
        }
        if date_filter:
            match["SDateTime"] = date_filter

        # ── Identify one target number per CDR ────────────────────────
        profiles = list(cdr_collection.aggregate([
            {"$match": match},
            {"$group": {"_id": "$seq_id", "target": {"$first": "$A_Party"}}},
        ]))

        if len(profiles) < 2:
            return Response(
                {"error": "Need at least 2 CDRs with valid numbers"},
                status=404,
            )

        targets = set(p["target"] for p in profiles)

        # ── Build ALL directed edges ──────────────────────────────────
        edge_counts = defaultdict(int)
        for row in cdr_collection.aggregate([
            {"$match": match},
            {"$group": {
                "_id":   {"a": "$A_Party", "b": "$B_Party", "type": "$Call_Type"},
                "count": {"$sum": 1},
            }},
        ]):
            caller, receiver, comm_type = resolve_direction(
                row["_id"]["a"], row["_id"]["b"], row["_id"].get("type", "")
            )
            edge_counts[(caller, receiver, comm_type)] += row["count"]

        all_edges = [
            {"from": u, "to": v, "calls": c, "comm_type": t}
            for (u, v, t), c in edge_counts.items()
            if c >= min_calls
        ]

        # ── Slide 4: Multi-Hop Expansion ─────────────────────────────
        # BFS from ALL targets to discover reachable nodes within expand_level.
        full_graph     = build_adjacency(all_edges)
        hop_level      = multi_hop_expand(targets, full_graph, expand_level)
        expanded_nodes = set(hop_level.keys())

        # Keep only edges where BOTH endpoints are within the hop limit.
        hop_edges = [
            e for e in all_edges
            if e["from"] in expanded_nodes and e["to"] in expanded_nodes
        ]
        hop_graph = build_adjacency(hop_edges)

        # ── Strict pruning: only inter-CDR meaningful nodes ───────────
        #
        # Replaces the old "reachable_from ≥ 2 targets" heuristic with an
        # explicit three-category filter:
        #   1. Targets
        #   2. Nodes directly connected to 2+ targets  (Hidden Connector / Common Contact)
        #   3. Nodes on a BFS shortest path between any target pair  (Bridge nodes)
        #
        relevant_nodes = compute_relevant_nodes(
            targets, hop_graph, hop_edges, expand_level
        )

        # Re-filter edges to relevant nodes only
        edges = [
            e for e in hop_edges
            if e["from"] in relevant_nodes and e["to"] in relevant_nodes
        ]

        if not edges:
            return Response(
                {"error": "No interconnections found between the selected CDR targets"},
                status=404,
            )

        # Rebuild graph and hop_level from the final pruned edge set so
        # hop colours on the frontend stay accurate (Slide 4).
        graph     = build_adjacency(edges)
        hop_level = multi_hop_expand(targets, graph, expand_level)

        # ── Slide 5: Shortest Path (user-selected pair) ───────────────
        shortest_path_result = None
        if shortest_path_req:
            sp_from = shortest_path_req.get("from")
            sp_to   = shortest_path_req.get("to")
            path    = bfs_shortest_path(graph, sp_from, sp_to)
            if path:
                path_set    = set(zip(path, path[1:]))
                total_calls = sum(
                    e["calls"] for e in edges
                    if (e["from"], e["to"]) in path_set
                    or (e["to"], e["from"]) in path_set
                )
                shortest_path_result = {
                    "path":        path,
                    "hop_count":   len(path) - 1,
                    "total_calls": total_calls,
                }

        # ── Slide 8: Cycle / Closed-Loop Detection ────────────────────
        cycles = []
        if cycle_detection:
            raw_cycles = detect_cycles(graph)
            for c in raw_cycles:
                internal = count_internal_edges(c, edges)
                cycles.append({
                    "cycle":          c,
                    "length":         len(c),
                    "internal_edges": internal,
                })

        # ── Node stats + role classification ─────────────────────────
        node_degree = defaultdict(int)
        for e in edges:
            node_degree[e["from"]] += 1
            node_degree[e["to"]]   += 1

        # How many distinct targets directly connect to each node?
        target_connections = defaultdict(set)
        for e in edges:
            if e["from"] in targets:
                target_connections[e["to"]].add(e["from"])
            if e["to"] in targets:
                target_connections[e["from"]].add(e["to"])

        # Slide 10: Node Size = Total call frequency
        node_total_calls = defaultdict(int)
        for e in edges:
            node_total_calls[e["from"]] += e["calls"]
            node_total_calls[e["to"]]   += e["calls"]

        nodes = []
        for n in relevant_nodes:
            degree = node_degree[n]
            role   = classify_role(n, targets, target_connections, hop_level)

            nodes.append({
                "number":            n,
                "degree":            degree,
                "total_calls":       node_total_calls[n],
                "risk_score":        min(100, degree * 8),
                "role":              role,
                "hop_level":         hop_level.get(n, 0),
                "connected_targets": list(target_connections.get(n, set())),
            })

        nodes.sort(key=lambda x: x["risk_score"], reverse=True)

        # ── Slide 10: Edge enrichment ─────────────────────────────────
        max_calls = max((e["calls"] for e in edges), default=1)
        for e in edges:
            e["normalised_weight"] = round(e["calls"] / max_calls, 4)

        # ── Meeting Point Detection ───────────────────────────────────
        meeting_points = []

        meeting_match = {
            "seq_id":    {"$in": seq_ids},
            "SDateTime": {"$exists": True},
            "First_CGI": {"$exists": True}
        }
        if date_filter:
            meeting_match["SDateTime"] = {**{"$exists": True}, **date_filter}

        meeting_pipeline = [
            {"$match": meeting_match},
            {"$project": {
                "A_Party":   1,
                "B_Party":   1,
                "First_CGI": 1,
                "SDateTime": 1
            }}
        ]

        meeting_records = list(cdr_collection.aggregate(meeting_pipeline))

        tower_map = defaultdict(list)

        for rec in meeting_records:
            tower  = rec.get("First_CGI")
            dt_obj = rec.get("SDateTime")

            if not tower or not dt_obj:
                continue

            for number in [rec.get("A_Party"), rec.get("B_Party")]:
                if number not in relevant_nodes:
                    continue

                tower_map[tower].append({
                    "number":   number,
                    "datetime": dt_obj
                })

        for tower, records in tower_map.items():
            records.sort(key=lambda x: x["datetime"])

            for i in range(len(records)):
                base          = records[i]
                close_persons = {base["number"]}

                for j in range(i + 1, len(records)):
                    diff = records[j]["datetime"] - base["datetime"]

                    if diff <= timedelta(minutes=5):
                        close_persons.add(records[j]["number"])
                    else:
                        break

                if len(close_persons) >= 2:
                    meeting_points.append({
                        "tower":   tower,
                        "date":    base["datetime"].strftime("%Y-%m-%d"),
                        "time":    base["datetime"].strftime("%H:%M:%S"),
                        "persons": list(close_persons)
                    })
                    break

        # ── Night Call Activity (10 PM – 5 AM) ───────────────────────
        night_call_activity = []

        night_match = {
            "seq_id":  {"$in": seq_ids},
            "A_Party": {"$in": list(relevant_nodes)},
            "B_Party": {"$in": list(relevant_nodes)},
        }
        if date_filter:
            night_match["SDateTime"] = {**{"$exists": True}, **date_filter}
        else:
            night_match["SDateTime"] = {"$exists": True}

        night_pipeline = [
            {"$match": night_match},
            {"$match": {
                "A_Party": {"$regex": r"^[6-9]\d{9}$"},
                "B_Party": {"$regex": r"^[6-9]\d{9}$"}
            }},
            {"$addFields": {
                "hour": {"$hour": "$SDateTime"}
            }},
            {"$match": {
                "$or": [
                    {"hour": {"$gte": 22}},
                    {"hour": {"$lt": 5}}
                ]
            }},
            {"$group": {
                "_id": {
                    "a_party": "$A_Party",
                    "b_party": "$B_Party",
                    "date":    {"$dateToString": {"format": "%Y-%m-%d", "date": "$SDateTime"}}
                },
                "night_calls": {"$sum": 1},
                "first_time":  {"$min": "$SDateTime"},
                "last_time":   {"$max": "$SDateTime"}
            }},
            {"$sort": {"_id.date": 1}}
        ]

        night_records = list(cdr_collection.aggregate(night_pipeline, allowDiskUse=True))

        for rec in night_records:
            a = rec["_id"]["a_party"]
            b = rec["_id"]["b_party"]

            if a not in relevant_nodes or b not in relevant_nodes:
                continue

            night_call_activity.append({
                "from":        a,
                "to":          b,
                "date":        rec["_id"]["date"],
                "night_calls": rec["night_calls"],
                "first_time":  rec["first_time"].strftime("%H:%M:%S") if rec.get("first_time") else None,
                "last_time":   rec["last_time"].strftime("%H:%M:%S")  if rec.get("last_time")  else None,
            })

        # ── Top 10 Highest Common Numbers ────────────────────────────
        common_contacts = {
            n for n in relevant_nodes
            if n not in targets and len(target_connections.get(n, set())) >= 2
        }

        common_number_stats = []

        for contact in common_contacts:
            connected_targets = list(target_connections.get(contact, set()))

            total_calls = 0
            total_sms   = 0

            for target in connected_targets:
                total_calls += edge_counts.get((target, contact, "CALL"), 0)
                total_calls += edge_counts.get((contact, target, "CALL"), 0)
                total_sms   += edge_counts.get((target, contact, "SMS"),  0)
                total_sms   += edge_counts.get((contact, target, "SMS"),  0)

            common_number_stats.append({
                "number":             contact,
                "connected_targets":  connected_targets,
                "target_count":       len(connected_targets),
                "total_calls":        total_calls,
                "total_sms":          total_sms,
                "total_interactions": total_calls + total_sms,
            })

        top_common_numbers = sorted(
            common_number_stats,
            key=lambda x: (x["target_count"], x["total_interactions"]),
            reverse=True
        )[:10]

        # ── Persist & return ──────────────────────────────────────────
        # result = analysis_collection.insert_one({
        #     "seq_ids":     seq_ids,
        #     "total_nodes": len(nodes),
        #     "total_edges": len(edges),
        # })

        hidden_connectors = [n for n in nodes if n["role"] == "Hidden Connector"]
        target_nodes      = [n for n in nodes if n["role"] == "Target"]

        return Response({
            # "analysis_id":        str(result.inserted_id),
            "nodes":              nodes,
            "edges":              edges,
            "targets":            list(targets),
            "total_nodes":        len(nodes),
            "total_edges":        len(edges),

            "expand_level":       expand_level,
            "shortest_path":      shortest_path_result,
            "cycles":             cycles,
            "hidden_connectors":  hidden_connectors,
            "min_call_threshold": min_calls,
            "cycle_detection":    cycle_detection,

            "intelligence_summary": {
                "target_count":           len(target_nodes),
                "hidden_connector_count": len(hidden_connectors),
                "cycle_count":            len(cycles),
                "has_shortest_path":      shortest_path_result is not None,
                "deepest_hop":            max((n["hop_level"] for n in nodes), default=0),
            },

            "meetings":            meeting_points,
            "night_call_activity": night_call_activity,
            "top_common_numbers":  top_common_numbers,

            "message": "Inter-target network intelligence generated",
        }, status=200)