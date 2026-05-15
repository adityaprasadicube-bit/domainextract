from mongoengine import Document, StringField, DateTimeField, IntField, DecimalField, BinaryField, BooleanField

class ildNexus(Document):
    meta = {
        'collection': 'ILDNexus',
        'db_alias': 'ild_db',
        'strict': False
    }
    _id = StringField(primary_key=True)
    ILDNo = StringField()
    CrimeID = StringField()
    FileName = StringField()
    Duplicate = IntField(default=0)
    FromDate = DateTimeField()
    ToDate = DateTimeField()
    Inserted = IntField(default=0)
    RecordType = StringField()
    InsertedAt = DateTimeField()

    Day = IntField()
    Month = IntField()
    Year = IntField()

from mongoengine import Document, StringField, DateTimeField, IntField, ListField


class ildRecord(Document):
    meta = {
        'collection': 'ILDRecords',
        'db_alias': 'ild_db',
        'strict': False
    }

    _id = StringField(primary_key=True)

    STime = StringField()
    SDate = DateTimeField()

    A_Party = StringField()
    B_Party = StringField()

    Duration = IntField(default=0)

    ORIG_SWITCH_ID = IntField()
    ORG_TRUNC_GROUP = StringField()
    TERM_TRUNC_GROUP = StringField()

    SMSC_No = StringField(db_field="SMSC No")
    SW_MSC_ID = StringField(db_field="SW & MSC ID")

    FileServiceType = StringField()          # ✅ added
    CIRCLE = StringField()           # ✅ added

    Call_Type = StringField()
    CALL_STATUS = StringField()

    First_CGI = StringField()
    Last_CGI =StringField()

    First_Lat = StringField()                # ✅ added
    First_Long = StringField()               # ✅ added

    IMEI = StringField()
    IMSI = StringField()

    IMEI_TAC = StringField()
    IMSI_CODE = StringField()

    Con_Type = StringField()
    CARRIER = StringField()


    SDateTime = DateTimeField()
    EDateTime = DateTimeField()

    a_country_code = StringField()
    b_country_code = StringField()

    b_mobile_code = StringField()
    a_mobile_code = StringField()

    seq_id = ListField(StringField(), required=True)   # ✅ fixed