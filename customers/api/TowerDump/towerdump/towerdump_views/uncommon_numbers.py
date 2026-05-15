from django.utils.dateparse import parse_datetime
from django.http import StreamingHttpResponse
from mongoengine import get_db
from rest_framework.response import Response
from rest_framework.views import APIView
from collections import defaultdict
import json


class TowerDumpAdvancedOptionsView(APIView):

    PAGE_SIZE = 500

    def post(self, request):

        filterer    = request.data.get('filter')
        number_type = request.data.get('number_type', 'mobile')  # 'mobile' | 'imei'
        ab_filter   = request.data.get('ab_filter', 'A')          # 'A' | 'B' | 'BOTH'
        page        = int(request.data.get('page', 1))
        page_size   = int(request.data.get('page_size', self.PAGE_SIZE))

        db = get_db(alias="tower_dump")

        # ── A/B/Both only applies to mobile; IMEI has no party concept ─
        # group_field   → what field to GROUP BY (deduplicate numbers)
        # number_label  → key name in response
        # party_fields  → list of fields to check for call presence
        #                  used in $match to ensure the number exists
        #                  in the right party column
        if number_type == "imei":
            group_field  = "$IMEI"
            number_label = "IMEI_No"
            party_fields = None          # IMEI — no A/B concept
        else:
            # Mobile — group field depends on ab_filter
            if ab_filter == "A":
                group_field  = "$A_Party"
                number_label = "Mobile_No"
                party_fields = ["A_Party"]
            elif ab_filter == "B":
                group_field  = "$B_Party"
                number_label = "Mobile_No"
                party_fields = ["B_Party"]
            else:
                # BOTH — we need a different approach:
                # unwind both A_Party and B_Party into one stream
                group_field  = None           # handled specially in pipeline
                number_label = "Mobile_No"
                party_fields = ["A_Party", "B_Party"]

        # ══════════════════════════════════════════════════════════════
        # AUCN — flat structure, no groups
        # ══════════════════════════════════════════════════════════════
        if filterer == "AUCN":
            seq_id_input = request.data.get('seq_ids')
            seq_ids      = seq_id_input if isinstance(seq_id_input, list) else [seq_id_input]

            recce_from = parse_datetime(request.data.get('recce_from')) if request.data.get('recce_from') else None
            recce_to   = parse_datetime(request.data.get('recce_to'))   if request.data.get('recce_to')   else None
            from_date  = parse_datetime(request.data.get('from_date'))  if request.data.get('from_date')  else None
            to_date    = parse_datetime(request.data.get('to_date'))    if request.data.get('to_date')    else None

            not_existed_ranges = request.data.get('not_existed_ranges', [])

            if not from_date or not to_date:
                return Response(
                    {"error": "from_date and to_date (crime window) are mandatory"},
                    status=400
                )
            if not not_existed_ranges:
                return Response(
                    {"error": "not_existed_ranges is mandatory"},
                    status=400
                )

            pipeline = self._build_aucn_pipeline(
                seq_ids            = seq_ids,
                recce_from         = recce_from,
                recce_to           = recce_to,
                from_date          = from_date,
                to_date            = to_date,
                group_field        = group_field,
                number_label       = number_label,
                party_fields       = party_fields,
                ab_filter          = ab_filter,
                number_type        = number_type,
                not_existed_ranges = not_existed_ranges,
            )

            raw_results = list(db.TowerDumpRecords.aggregate(pipeline))

            total_records = len(raw_results)
            total_pages   = (total_records + page_size - 1) // page_size
            skip          = (page - 1) * page_size
            page_data     = raw_results[skip: skip + page_size]

            meta = {
                "total_records": total_records,
                "total_pages":   total_pages,
                "page":          page,
                "page_size":     page_size,
                "count":         len(page_data),
            }

            if len(page_data) > 200:
                def stream_json():
                    yield json.dumps(meta)[:-1]
                    yield ', "data": ['
                    for i, rec in enumerate(page_data):
                        yield json.dumps(rec, default=str)
                        if i < len(page_data) - 1:
                            yield ","
                    yield "]}"
                return StreamingHttpResponse(stream_json(), content_type="application/json")

            return Response({**meta, "data": page_data})

        # ══════════════════════════════════════════════════════════════
        # UCN / MN — group_list structure
        # ══════════════════════════════════════════════════════════════
        group_list = request.data.get('group_list', [])

        if not group_list:
            return Response({"count": 0, "total_records": 0, "data": []})

        all_group_names = [g['group_name'] for g in group_list]

        number_map = defaultdict(lambda: {
            "groups":     [],
            "first_call": None,
            "last_call":  None,
            "total_hits": 0
        })

        for group in group_list:
            group_name = group.get('group_name')
            seq_ids    = group.get('seq_id', [])
            from_date  = parse_datetime(group.get('from_date')) if group.get('from_date') else None
            to_date    = parse_datetime(group.get('to_date'))   if group.get('to_date')   else None

            pipeline = self._build_pipeline(
                filterer     = filterer,
                seq_ids      = seq_ids,
                from_date    = from_date,
                to_date      = to_date,
                group_field  = group_field,
                number_label = number_label,
                party_fields = party_fields,
                ab_filter    = ab_filter,
                number_type  = number_type,
            )

            if not pipeline:
                continue

            for record in db.TowerDumpRecords.aggregate(pipeline):
                num = record.get(number_label)
                if not num:
                    continue

                entry     = number_map[num]
                rec_first = record.get("first_call_raw")
                rec_last  = record.get("last_call_raw")

                entry["groups"].append(group_name)

                if rec_first:
                    if entry["first_call"] is None or rec_first < entry["first_call"]:
                        entry["first_call"] = rec_first
                if rec_last:
                    if entry["last_call"] is None or rec_last > entry["last_call"]:
                        entry["last_call"] = rec_last

                entry["total_hits"] += record.get("Count", 0)

        raw_results = []

        for number, entry in number_map.items():
            qualified_groups = entry["groups"]

            first_call_str = (
                entry["first_call"].strftime("%d/%b/%Y %H:%M:%S")
                if entry["first_call"] else ""
            )
            last_call_str = (
                entry["last_call"].strftime("%d/%b/%Y %H:%M:%S")
                if entry["last_call"] else ""
            )

            record = {
                number_label:          number,
                "Count":               len(qualified_groups),
                "Un_Common_In_Groups": ", ".join(qualified_groups),
                "First_Last_Call":     f"{first_call_str} - {last_call_str}",
                "First_Call":          first_call_str,
                "Last_Call":           last_call_str,
                "Total_Hits":          entry["total_hits"],
            }

            for gname in all_group_names:
                record[gname] = "YES" if gname in qualified_groups else "NO"

            raw_results.append(record)

        raw_results.sort(key=lambda x: (-x["Count"], x[number_label]))

        total_records = len(raw_results)
        total_pages   = (total_records + page_size - 1) // page_size
        skip          = (page - 1) * page_size
        page_data     = raw_results[skip: skip + page_size]

        meta = {
            "total_records": total_records,
            "total_pages":   total_pages,
            "page":          page,
            "page_size":     page_size,
            "count":         len(page_data),
            "groups":        all_group_names,
        }

        if len(page_data) > 200:
            def stream_json():
                yield json.dumps(meta)[:-1]
                yield ', "data": ['
                for i, rec in enumerate(page_data):
                    yield json.dumps(rec, default=str)
                    if i < len(page_data) - 1:
                        yield ","
                yield "]}"
            return StreamingHttpResponse(stream_json(), content_type="application/json")

        return Response({**meta, "data": page_data})

    # ══════════════════════════════════════════════════════════════════════
    # HELPER — build the first two stages for A / B / BOTH
    #
    # A    → $match seq_id, $group by A_Party
    # B    → $match seq_id, $group by B_Party
    # BOTH → $match seq_id, $addFields number=$A_Party OR $B_Party
    #        via $facet + $unionWith trick OR via $project + $unwind
    #        We use the cleanest approach: $addFields with $setUnion on
    #        [A_Party, B_Party] then $unwind so each number gets its own doc
    # ══════════════════════════════════════════════════════════════════════
    def _get_entry_stages(self, seq_ids, ab_filter, number_type):
        """
        Returns the opening pipeline stages that normalise every document
        into a shape where 'number' field holds the value to group by.

        A    → {"number": "$A_Party", ...rest of doc}
        B    → {"number": "$B_Party", ...rest of doc}
        BOTH → one doc per unique number (A_Party and B_Party unwound)
        IMEI → {"number": "$IMEI",    ...rest of doc}
        """
        base_match = {"$match": {"seq_id": {"$in": seq_ids}}}

        if number_type == "imei":
            return [
                base_match,
                {"$addFields": {"number": "$IMEI"}}
            ]

        if ab_filter == "A":
            return [
                base_match,
                # Only include docs where A_Party exists and is not null
                {"$match": {"A_Party": {"$exists": True, "$ne": None, "$ne": ""}}},
                {"$addFields": {"number": "$A_Party"}}
            ]

        elif ab_filter == "B":
            return [
                base_match,
                {"$match": {"B_Party": {"$exists": True, "$ne": None, "$ne": ""}}},
                {"$addFields": {"number": "$B_Party"}}
            ]

        else:
            # BOTH — create an array of [A_Party, B_Party], unwind into
            # separate docs, filter out nulls/empty, deduplicate per doc
            return [
                base_match,
                {
                    # Build array of both parties, filter nulls
                    "$addFields": {
                        "parties": {
                            "$filter": {
                                "input": ["$A_Party", "$B_Party"],
                                "as":    "p",
                                "cond":  {
                                    "$and": [
                                        {"$ne": ["$$p", None]},
                                        {"$ne": ["$$p", ""]},
                                        {"$gt": ["$$p", None]}
                                    ]
                                }
                            }
                        }
                    }
                },
                # Remove docs with no valid parties at all
                {"$match": {"parties": {"$not": {"$size": 0}}}},
                # Unwind — one doc per party number
                {"$unwind": "$parties"},
                {"$addFields": {"number": "$parties"}},
            ]

    # ══════════════════════════════════════════════════════════════════════
    # AUCN PIPELINE
    # ══════════════════════════════════════════════════════════════════════
    def _build_aucn_pipeline(self, seq_ids, recce_from, recce_to,
                              from_date, to_date,
                              group_field, number_label,
                              party_fields, ab_filter, number_type,
                              not_existed_ranges=None):

        if not_existed_ranges is None:
            not_existed_ranges = []

        recce_provided = bool(recce_from and recce_to)

        not_existed_conditions = []
        for gap in not_existed_ranges:
            g_from = parse_datetime(gap.get('from'))
            g_to   = parse_datetime(gap.get('to'))
            if g_from and g_to:
                not_existed_conditions.append(
                    {"$and": [
                        {"$gte": ["$SDateTime", g_from]},
                        {"$lte": ["$SDateTime", g_to]}
                    ]}
                )

        in_not_existed_expr = (
            {"$or": not_existed_conditions}
            if not_existed_conditions else False
        )

        # Entry stages normalise A/B/BOTH into a "number" field
        entry_stages = self._get_entry_stages(seq_ids, ab_filter, number_type)

        group_stage = {
            "$group": {
                # Always group by the normalised "number" field
                "_id": "$number",

                "in_crime": {
                    "$sum": {
                        "$cond": [
                            {"$and": [
                                {"$gte": ["$SDateTime", from_date]},
                                {"$lte": ["$SDateTime", to_date]}
                            ]}, 1, 0
                        ]
                    }
                },

                "in_not_existed": {
                    "$sum": {"$cond": [in_not_existed_expr, 1, 0]}
                },

                "first_call": {"$min": "$SDateTime"},
                "last_call":  {"$max": "$SDateTime"},
                "total_hits": {"$sum": 1}
            }
        }

        if recce_provided:
            group_stage["$group"]["in_recce"] = {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$gte": ["$SDateTime", recce_from]},
                            {"$lte": ["$SDateTime", recce_to]}
                        ]}, 1, 0
                    ]
                }
            }

        match_conditions = {
            "in_crime":       {"$gt": 0},
            "in_not_existed": 0,
        }

        if recce_provided:
            # Recce dates given → always enforce in_recce > 0
            match_conditions["in_recce"] = {"$gt": 0}

        project_stage = {
            "$project": {
                "_id":        0,
                number_label: "$_id",
                "Count": {
                    "$add": [
                        "$in_recce" if recce_provided else {"$literal": 0},
                        "$in_crime"
                    ]
                },
                "In_Crime": "$in_crime",
                "First_Call": {
                    "$dateToString": {
                        "format": "%d/%b/%Y %H:%M:%S",
                        "date":   "$first_call"
                    }
                },
                "Last_Call": {
                    "$dateToString": {
                        "format": "%d/%b/%Y %H:%M:%S",
                        "date":   "$last_call"
                    }
                },
                "First_Last_Call": {
                    "$concat": [
                        {"$dateToString": {"format": "%d/%b/%Y %H:%M:%S", "date": "$first_call"}},
                        " - ",
                        {"$dateToString": {"format": "%d/%b/%Y %H:%M:%S", "date": "$last_call"}}
                    ]
                }
            }
        }

        if recce_provided:
            project_stage["$project"]["In_Recce"] = "$in_recce"

        return [
            *entry_stages,           # $match seq_id + normalise "number" field
            group_stage,
            {"$match": match_conditions},
            project_stage,
            {"$sort": {"First_Call": 1}}
        ]

    # ══════════════════════════════════════════════════════════════════════
    # UCN / MN PIPELINE BUILDER
    # ══════════════════════════════════════════════════════════════════════
    def _build_pipeline(self, filterer, seq_ids, from_date, to_date,
                         group_field, number_label,
                         party_fields=None, ab_filter="A", number_type="mobile"):

        # Entry stages handle A/B/BOTH normalisation into "number" field
        entry_stages = self._get_entry_stages(seq_ids, ab_filter, number_type)

        def result_project(extra_fields=None):
            proj = {
                "$project": {
                    "_id":            0,
                    number_label:     "$_id",
                    "Count":          "$total_hits",
                    "first_call_raw": "$first_call",
                    "last_call_raw":  "$last_call",
                    "First_Last_Call": {
                        "$concat": [
                            {"$dateToString": {"format": "%d/%b/%Y %H:%M:%S", "date": "$first_call"}},
                            " - ",
                            {"$dateToString": {"format": "%d/%b/%Y %H:%M:%S", "date": "$last_call"}}
                        ]
                    }
                }
            }
            if extra_fields:
                proj["$project"].update(extra_fields)
            return proj

        # ── UCN ────────────────────────────────────────────────────────
        if filterer == "UCN":
            return [
                *entry_stages,
                {
                    "$group": {
                        "_id":        "$number",   # normalised field
                        "total_hits": {"$sum": 1},
                        "hits_in_window": {
                            "$sum": {
                                "$cond": [
                                    {"$and": [
                                        {"$gte": ["$SDateTime", from_date]},
                                        {"$lte": ["$SDateTime", to_date]}
                                    ]}, 1, 0
                                ]
                            }
                        },
                        "hits_before_window": {
                            "$sum": {"$cond": [{"$lt": ["$SDateTime", from_date]}, 1, 0]}
                        },
                        "hits_after_window": {
                            "$sum": {"$cond": [{"$gt": ["$SDateTime", to_date]}, 1, 0]}
                        },
                        "first_call": {"$min": "$SDateTime"},
                        "last_call":  {"$max": "$SDateTime"},
                    }
                },
                {
                    "$match": {
                        "hits_in_window":     {"$gt": 0},
                        "hits_before_window": 0,
                        "hits_after_window":  0,
                        "$expr": {"$eq": ["$total_hits", "$hits_in_window"]}
                    }
                },
                result_project({
                    "Hits_In_Window":     "$hits_in_window",
                    "Hits_Before_Window": "$hits_before_window",
                    "Hits_After_Window":  "$hits_after_window",
                })
            ]

        # ── MN ─────────────────────────────────────────────────────────
        elif filterer == "MN":
            return [
                *entry_stages,
                {
                    "$group": {
                        "_id":        "$number",   # normalised field
                        "total_hits": {"$sum": 1},
                        "hits_during_crime": {
                            "$sum": {
                                "$cond": [
                                    {"$and": [
                                        {"$gte": ["$SDateTime", from_date]},
                                        {"$lte": ["$SDateTime", to_date]}
                                    ]}, 1, 0
                                ]
                            }
                        },
                        "hits_before_crime": {
                            "$sum": {"$cond": [{"$lt": ["$SDateTime", from_date]}, 1, 0]}
                        },
                        "hits_after_crime": {
                            "$sum": {"$cond": [{"$gt": ["$SDateTime", to_date]}, 1, 0]}
                        },
                        "first_call": {"$min": "$SDateTime"},
                        "last_call":  {"$max": "$SDateTime"},
                    }
                },
                {
                    "$match": {
                        "hits_during_crime": 0,
                        "total_hits":        {"$gt": 0}
                    }
                },
                result_project({
                    "Hits_Before_Crime": "$hits_before_crime",
                    "Hits_After_Crime":  "$hits_after_crime",
                    "Hits_During_Crime": "$hits_during_crime",
                })
            ]

        return []