from mongoengine import Document, StringField, DateTimeField, IntField, DecimalField, ListField
class TowerDumpNexus(Document):
    meta ={'collection':'TowerDumpNexus','db_alias':'tower_dump','strict':False}
    id = StringField(primary_key=True)  # <--- Important
    CrimeID = StringField()
    Tower_id = StringField()
    UserAccessID = StringField()
    Day = IntField()
    Duplicate = IntField()
    FromDate = DateTimeField()
    Inserted = IntField()
    InsertedAt = DateTimeField()
    MaxDur = IntField()
    MinDur = IntField()
    Month = IntField()
    RecordType = StringField()
    Skipped = IntField()
    Tac_Or_Mobile_Code = StringField()
    ToDate = DateTimeField()
    Year = IntField()
    ImsiCode = StringField()


class TowerDumpDetailRecord(Document):
    meta = {'collection': 'TowerDumpRecords','strict': False ,'db_alias':'tower_dump', 'indexes':['seq_id']}  # Optional, to explicitly set the collection name

    id = StringField(primary_key=True)  # Mongo _id field is a string
    A_Party = StringField()
    a_mobile_code = StringField()
    B_Party = StringField()
    b_mobile_code = StringField()
    SDateTime = DateTimeField()
    EDateTime = DateTimeField()
    Duration = IntField()
    SDate = DateTimeField()
    STime = StringField()
    FileCallType = StringField()
    Call_Type = StringField()
    LRN = StringField()
    First_CGI = StringField()
    Last_CGI = StringField()
    IMEI = StringField()
    IMSI = StringField()
    IMSI_CODE = StringField()
    Con_Type = StringField()
    First_Lat = DecimalField(precision=5)
    First_Long = DecimalField(precision=5)
    Last_Lat = DecimalField(precision=5)
    Last_Long = DecimalField(precision=5)
    FileServiceType = StringField()
    IMEI_TAC = StringField()
    seq_id = StringField(required=True)