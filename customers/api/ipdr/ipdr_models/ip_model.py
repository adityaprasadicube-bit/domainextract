from mongoengine import Document, StringField, DateTimeField, IntField, DecimalField, BinaryField, BooleanField

from mongoengine import Document, StringField, DateTimeField, IntField, BooleanField


class IPDRRecord(Document):
    meta = {
        'collection': 'IPDetailRecords',
        'strict': False,
        'db_alias': 'ipdr_db',
        'indexes': [
            'seq_id',
            'enriched_data_cached',
            'enriched_usage',
            'enriched_country',
            'enriched_location',
            'enriched_port_category',
            {'fields': ['seq_id', 'enriched_data_cached']},
            {'fields': ['seq_id', 'enriched_usage']},
        ]
    }

    # Original fields
    id = StringField(primary_key=True)
    MSISDN = StringField()
    MSISDN_code = StringField()
    Destination_ip = StringField()
    Destination_port = IntField()
    SDateTime = DateTimeField()
    EDateTime = DateTimeField()
    Duration = IntField()
    TowerID = StringField()
    IMEI = StringField()
    IMEI_TAC = StringField()
    IMSI = StringField()
    IMSI_CODE = StringField()
    DataUpload = StringField()
    DataDownload = StringField()
    Source_ip = StringField()
    Source_port = IntField()
    Translated_ip = StringField()
    Translated_port = IntField()
    seq_id = StringField(required=True)

    # NEW: Cached enriched fields (from external APIs)
    enriched_usage = StringField()
    enriched_isp_org = StringField()
    enriched_domains = StringField()
    enriched_location = StringField()
    enriched_country = StringField()
    enriched_port_category = StringField()
    enriched_port_info = StringField()
    enriched_data_cached = BooleanField(default=False)
    enriched_at = DateTimeField()


class IPDataBase(Document):
    meta = {'collection': 'ip_info', 'strict': False, 'db_alias': 'ip_info', 'indexes': ['query']}

    id = BinaryField(required=True, primary_key=True)
    continent = StringField()
    continentCode = StringField()
    country = StringField()
    countryCode = StringField()
    region = StringField()
    regionName = StringField()
    city = StringField()
    district = StringField()
    zip = StringField()
    lat = DecimalField(precision=6)
    lon = DecimalField(precision=6)
    timezone = StringField()
    offset = StringField()
    currency = StringField()
    isp = StringField()
    org = StringField()
    as_ = StringField(db_field="as")
    asname = StringField()
    reverse = StringField()
    type = StringField()
    query = BinaryField(required=True)
    recdate = StringField()


class IPDRNexus(Document):
    meta = {'collection': 'IPdrNexus', 'db_alias': 'ipdr_db', 'strict': False}

    id = StringField(primary_key=True)
    CrimeID = StringField()
    Day = IntField()
    Duplicate = IntField()
    FromDate = DateTimeField()
    IPDR = StringField()
    Inserted = IntField()
    InsertedAt = DateTimeField()
    MaxDur = IntField()
    MinDur = IntField()
    Month = IntField()
    RecordType = StringField()
    Skipped = IntField()
    ToDate = DateTimeField()
    UserAccessID = StringField()
    Year = IntField()
    Name = StringField()


class PortInfo(Document):
    meta = {'collection': 'port_info', 'strict': False, 'db_alias': 'ip_info'}

    id = IntField(primary_key=True)
    Port = IntField(required=True)
    Description = StringField()
    Category = StringField()
    Type = StringField()