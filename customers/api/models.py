from operator import index

from mongoengine import Document, StringField, DateTimeField, IntField, DecimalField, ListField

from mongoengine import Document, StringField
from mongoengine import Document, StringField

class SystemMeta(Document):
    meta = {'collection': 'system_meta', 'db_alias': 'logininfo'}

    key = StringField(required=True, unique=True)
    value = StringField(required=True)


class User(Document):
    meta = {'collection': 'UserInfo', 'db_alias': 'logininfo', 'strict': False}
    mobile = StringField(required=True, unique=True)
    password = StringField(required=True)
class Nexus(Document):
    meta ={'collection':'DataNexus','db_alias':'cdr_db','strict':False}
    id = StringField(primary_key=True)  # <--- Important
    CDRNo_Or_ImeiNo = StringField()
    CrimeID = StringField()
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
    Name = StringField()

class CallDetailRecord(Document):
    meta = {'collection': 'CallDetailRecords','strict': False ,'db_alias':'cdr_db', 'indexes':['seq_id']}  # Optional, to explicitly set the collection name

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

class CellTower(Document):
    meta = {'collection': 'cellid_info', 'strict': False, 'db_alias': 'cell_id'}

    id = StringField(primary_key=True)   # maps MongoDB _id field
    LATITUDE = StringField()
    LONGITUDE = StringField()
    AZIMUTH = StringField()
    ADDRESS = StringField()
    MAIN_CITY = StringField()
    SUB_CITY = StringField()
    TYPE = StringField()
    DATE_TIME = DateTimeField()
    LAC = StringField()
    CELLID = StringField()
    CIRCLE = StringField()
    OPERATOR = StringField()
    MCC = StringField()
    MNC = StringField()
    MCCMNC = StringField()



class ImeiDetails(Document):
    meta = {'collection': 'ImeiMapping', 'strict': False, 'db_alias': 'source_db','indexes':["devicetype",'brand']}
    id = IntField(primary_key=True)
    brand = StringField()
    manufacturer = StringField()
    datealloted = DateTimeField()
    os = StringField()
    devicetype = StringField()
    simslots = StringField()

class MobileOperator(Document):
    meta = {'collection': 'MobileCodes', 'strict': False, 'db_alias': 'source_db'}
    id = IntField(primary_key=True)
    Circle = StringField(required=True)
    Operator = StringField(required=True)

class MccMnc(Document):
    meta = {'collection': 'MccMnc', 'strict': False, 'db_alias': 'source_db','indexes':["mcc", "mnc", "mccmnc", "mccmnc_temp", "circle", "operator"]}
    mcc= StringField(required=True)
    mnc = StringField(required=True)
    mccmnc = StringField(required=True)
    mccmnc_temp = StringField(required=True)
    circle = StringField(required=True)
    operator = StringField(required=True)

class LRNCode(Document):
    meta = {'collection': 'LRNCodes', 'strict': False, 'db_alias': 'source_db'}
    id = IntField(primary_key=True)
    circle = StringField(required=True)
    operator = StringField(required=True)

class SMSHeader(Document):
    meta = {'collection': 'SMSHeaders', 'strict': False, 'db_alias': 'source_db'}
    id = StringField(primary_key=True)
    address = StringField(required=True)
    type = StringField(required=True)

class CrimeInformation(Document):
    meta = {'collection': 'CrimeRegistry', 'strict': False, 'db_alias': 'cdr_db'}  # Optional, to explicitly
    id = StringField(primary_key=True)
    Crime = StringField(required=True)
    AreaLocation = StringField(required=True)

class UserAccess(Document):
    meta = {'collection': 'UserAccessMapping', 'strict': False, 'db_alias': 'cdr_db'}  # Optional, to explicitly
    id = StringField(primary_key=True)
    UserID = StringField(required=True)
#
# class ImsiInformation(Document):
#     meta = {'collection': 'ImsiMapper', 'strict': False, 'db_alias': 'cdr_db'}  # Optional, to explicitly
#     IMSI = StringField(required=True)
#     IMSI_CODE = StringField(required=True)
#     seq_id = StringField(required=True)

