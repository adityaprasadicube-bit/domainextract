from mongoengine import get_db
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.core.cache import cache
import hashlib
import time


class SearchAPI(APIView):
    # ✅ Database connection mapping
    COLLECTION_MAP = {
        "CDR": "CallDetailRecords",
        "TowerDump": "TowerDumpNexus",
        "WhatsApp": "WhatsAppNexus",
        "IPDR": "IPdrNexus",
    }

    MODULE_MAP = {
        "CDR": ("cdr_db", "CallDetailRecords"),
        "TowerDump": ("tower_dump", "TowerDumpNexus"),
        "WhatsApp": ("whatsapp_db", "WhatsAppNexus"),
        "IPDR": ("ipdr_db", "IPdrNexus"),
    }

    def post(self, request):
        start_time = time.time()
        print("\n" + "=" * 80)
        print("🔍 SearchAPI POST Request Started")

        # Get request data
        state = request.data.get('state')
        main_city = request.data.get('main_city')
        filter_value = request.data.get('filtervalue')
        page = int(request.data.get('page', 1))
        page_size = int(request.data.get('page_size', 100))

        print(f"\n📝 Request Parameters:")
        print(f"   State: {state}")
        print(f"   Main City: {main_city}")
        print(f"   Filter Value: {filter_value}")
        print(f"   Page: {page}")
        print(f"   Page Size: {page_size}")

        # ✅ Validation
        if not state:
            print("❌ Validation Error: state is required")
            return Response(
                {"error": "state is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not filter_value:
            print("❌ Validation Error: filtervalue is required")
            return Response(
                {"error": "filtervalue is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if filter_value not in self.MODULE_MAP:
            print(f"❌ Validation Error: Invalid filtervalue '{filter_value}'")
            print(f"   Valid options: {list(self.MODULE_MAP.keys())}")
            return Response(
                {"error": "Invalid filtervalue"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ✅ Get db and collection names from MODULE_MAP
        db_alias, collection_name = self.MODULE_MAP[filter_value]

        print(f"\n✅ Validation Passed")
        print(f"   Database Alias: {db_alias}")
        print(f"   Target Collection: {collection_name}")

        # ✅ Cache key for cellids
        cache_key = self._generate_cache_key(state, main_city)
        print(f"\n🔑 Cache Key: {cache_key}")

        cellids = cache.get(cache_key)

        if cellids is not None:
            print(f"✅ Cache HIT! Found {len(cellids)} cellids in cache")
        else:
            print("❌ Cache MISS - Querying database for cellids...")
            cache_query_start = time.time()

            try:
                # ✅ Get cellids from cell_id database
                source_db = get_db(alias="cell_id")
                source_collection = source_db["cellid_info"]

                print(f"\n📦 Connected to source database")
                print(f"   Alias: cell_id")
                print(f"   Database Name: {source_db.name}")
                print(f"   Collection: cellid_info")

                query = {"CIRCLE": state}
                if main_city:
                    query["MAIN_CITY"] = main_city

                print(f"\n🔎 Executing cellid query: {query}")

                # Only fetch _id field for performance
                cellids = [
                    str(doc["_id"])
                    for doc in source_collection.find(query, {"_id": 1})
                ]

                cache_query_time = time.time() - cache_query_start
                print(f"✅ Cellid query completed in {cache_query_time:.2f}s")
                print(f"   Found: {len(cellids)} cellids")

                if not cellids:
                    print("\n❌ No cellids found matching criteria")
                    total_time = time.time() - start_time
                    print(f"⏱️  Total Time: {total_time:.2f}s")
                    print("=" * 80 + "\n")
                    return Response(
                        {"message": "No record found"},
                        status=status.HTTP_404_NOT_FOUND
                    )

                # Cache cellids for 1 hour
                cache.set(cache_key, cellids, 3600)
                print(f"💾 Cached {len(cellids)} cellids (TTL: 1 hour)")

            except Exception as e:
                print(f"\n❌ ERROR fetching cellids: {str(e)}")
                print(f"   Exception Type: {type(e).__name__}")
                import traceback
                traceback.print_exc()
                return Response(
                    {"error": f"Database error: {str(e)}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR
                )

        # ✅ Get target database and collection
        print(f"\n📦 Connecting to target database...")
        try:
            print(f"   Using alias: {db_alias}")
            target_db = get_db(alias=db_alias)
            target_collection = target_db[collection_name]

            print(f"✅ Connected successfully!")
            print(f"   Database Name: {target_db.name}")
            print(f"   Collection: {collection_name}")

        except Exception as e:
            print(f"❌ ERROR connecting to database: {str(e)}")
            print(f"   Exception Type: {type(e).__name__}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Database connection error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # ✅ Count total records
        print(f"\n📊 Counting records...")
        print(f"   Query: First_CGI in {len(cellids)} cellids")
        count_start = time.time()

        try:
            total_count = target_collection.count_documents(
                {"First_CGI": {"$in": cellids}}
            )
            count_time = time.time() - count_start
            print(f"✅ Count completed in {count_time:.2f}s")
            print(f"   Total Records: {total_count}")
        except Exception as e:
            print(f"❌ ERROR counting documents: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Error counting records: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        if total_count == 0:
            print("\n❌ No records found in target collection")
            total_time = time.time() - start_time
            print(f"⏱️  Total Time: {total_time:.2f}s")
            print("=" * 80 + "\n")
            return Response(
                {"message": "No record found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # ✅ Paginated query
        skip = (page - 1) * page_size
        total_pages = (total_count + page_size - 1) // page_size

        print(f"\n📄 Pagination Details:")
        print(f"   Page: {page}/{total_pages}")
        print(f"   Skip: {skip}")
        print(f"   Limit: {page_size}")

        # ✅ REMOVED PROJECTION - Fetch ALL fields
        print(f"   Projection: ALL FIELDS (no projection)")

        print(f"\n🔎 Executing main query...")
        query_start = time.time()

        try:
            # ✅ No projection parameter - get all fields
            cursor = target_collection.find(
                {"First_CGI": {"$in": cellids}}
            ).skip(skip).limit(page_size)

            # Try to use index hint if it exists
            try:
                cursor = cursor.hint("First_CGI_1")
                print(f"   ✅ Using index hint: First_CGI_1")
            except Exception as hint_error:
                print(f"   ⚠️  Index hint not available: {str(hint_error)}")

            cdr_records = list(cursor)
            query_time = time.time() - query_start
            print(f"✅ Query completed in {query_time:.2f}s")
            print(f"   Records Fetched: {len(cdr_records)}")

        except Exception as e:
            print(f"❌ ERROR executing query: {str(e)}")
            import traceback
            traceback.print_exc()
            return Response(
                {"error": f"Query execution error: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Convert ObjectId to string
        print(f"\n🔄 Converting ObjectIds to strings...")
        conversion_start = time.time()
        for record in cdr_records:
            record["_id"] = str(record["_id"])
        conversion_time = time.time() - conversion_start
        print(f"✅ Conversion completed in {conversion_time:.3f}s")

        total_time = time.time() - start_time

        print(f"\n✅ Request Completed Successfully!")
        print(f"\n⏱️  Performance Summary:")
        print(f"   Total Time: {total_time:.2f}s")
        if 'cache_query_time' in locals():
            print(f"   - Cellid Query: {cache_query_time:.2f}s")
        print(f"   - Count Query: {count_time:.2f}s")
        print(f"   - Data Query: {query_time:.2f}s")
        print(f"   - Conversion: {conversion_time:.3f}s")
        print("=" * 80 + "\n")

        return Response(
            {
                "count": total_count,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
                "data": cdr_records,
                "debug": {
                    "total_time_seconds": round(total_time, 2),
                    "cellids_count": len(cellids),
                    "cache_hit": cellids is not None,
                }
            },
            status=status.HTTP_200_OK
        )

    def _generate_cache_key(self, state, main_city):
        """Generate a unique cache key based on query parameters"""
        key_str = f"cellids:{state}:{main_city or 'all'}"
        return hashlib.md5(key_str.encode()).hexdigest()