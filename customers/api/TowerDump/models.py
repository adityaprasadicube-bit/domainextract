from django.db import models
import mongoengine

# Create your models here.
from mongoengine import connect,StringField,IntField,DecimalField,DateTimeField,Document
class TowerDumpNexus(Document):
    meta ={'collection':'TowerDumpNexus','db_alias':'tower_dump','strict':False}
    _id = StringField(primary_key=True)  # <--- Important
    CrimeID = StringField()
    Day = IntField()
    Duplicate=IntField()
    FromDate=DateTimeField()
    ImsiCode=StringField()
    Inserted = IntField()
    InsertedAt = DateTimeField()
    MaxDur = IntField()
    MinDur=IntField()
    Month=IntField()
    RecordType=StringField()
    Skipped=IntField()
    ToDate=DateTimeField()
    Tower_id=StringField()
    UserAccessID=StringField()
    Year=IntField()

class TowerDumpDetailRecord(Document):
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
