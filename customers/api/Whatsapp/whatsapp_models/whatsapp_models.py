from mongoengine import Document, StringField, DateTimeField, IntField,Document, StringField, ListField, DictField

class WhatsAppNexus(Document):
    meta = {
        'collection': 'WhatsAppNexus',
        'db_alias': 'whatsapp_db',
        'strict': False
    }
    _id = StringField(primary_key=True)
    CrimeName = StringField()
    CrimeID=StringField()
    FileName = StringField()
    Matched = IntField(default=0)
    FromDate = DateTimeField()
    ToDate = DateTimeField()
    Inserted = IntField(default=0)
    RecordType = StringField()
    CreatedAt = DateTimeField()
    TargetNo=StringField()

    Day = IntField()
    Month = IntField()
    Year = IntField()

class WhatsAppDetailsRecord(Document):
    meta = {
        'collection': 'WhatsAppRecords',
        'db_alias': 'whatsapp_db',
        'strict': False,
        'indexes': [
            'seq_id',
            'ID',
            'DateTimeUTC',
            'DateTimeIST'
        ]
    }

    id = StringField(primary_key=True)
    Group = StringField()
    Target = StringField()
    Call_Creator=  StringField(db_field="Call Creator")
    Participant = StringField()
    Participant_Code= StringField()
    Participants = StringField()
    Participants_Code = StringField()
    DateTimeUTC = DateTimeField()
    DateTimeIST = DateTimeField()
    ID = StringField()
    Target_Device = StringField(db_field="Target Device")
    Participant_Device = StringField(db_field="Participant Device")
    Type = StringField()
    Call_Type = StringField()
    Style = StringField()
    Size = IntField()
    Target_IP = StringField(db_field="Target IP")
    Target_Port = IntField(db_field="Target Port")
    Participant_IP = StringField(db_field="Participant IP")
    Participant_Port = IntField(db_field="Participant Port")
    Status = StringField()
    Group_ID = StringField(db_field="Group ID")
    HashCode = StringField()
    seq_id = StringField(required=True)




class WhatsAppInfoNexus(Document):
    meta = {
        'collection': 'WhatsAppInfoNexus',
        'db_alias': 'whatsapp_db',
        'strict': False,
        'indexes': [
            'TargetNo',
            'CrimeName'
        ]
    }

    id = StringField(primary_key=True, db_field="_id")

    CrimeName = StringField()
    FileName = StringField()
    RecordType = StringField()

    TargetNo = StringField()
    Target_code = StringField()

    Emails = ListField(StringField())

    FromDate = DateTimeField()
    ToDate = DateTimeField()

    ConnectionsCount = IntField()
    GroupsCount = IntField()
    SymmetricContactsCount = IntField()
    AsymmetricContactsCount = IntField()

    Day = IntField()
    Month = IntField()
    Year = IntField()

    CreatedAt = DateTimeField()


class WhatsAppConnection(Document):
    meta = {
        'collection': 'WhatsAppConnections',   # make sure this matches your real collection name
        'db_alias': 'whatsapp_db',
        'strict': False,
        'indexes': [
            'TargetNo',
            'seq_id'
        ]
    }
    id = StringField(primary_key=True, db_field="_id")
    Device_Id = StringField(db_field="Device Id")
    Service_start = StringField(db_field="Service start")
    Device_Type = StringField(db_field="Device Type")
    App_Version = StringField(db_field="App Version")
    Device_OS_Build_Number = StringField(db_field="Device OS Build Number")
    Connection_State = StringField(db_field="Connection State")
    Last_seen = StringField(db_field="Last seen")
    Last_IP = StringField(db_field="Last IP")
    TargetNo = StringField()
    seq_id = StringField(required=True)   # ✅ STRING (not ListField)



class WhatsAppContacts(Document):
    meta = {
        'collection': 'WhatsAppContacts',  # use your real collection name
        'db_alias': 'whatsapp_db',
        'strict': False,
        'indexes': [
            'TargetNo',
            'seq_id'
        ]
    }

    id = StringField(primary_key=True, db_field="_id")

    TargetNo = StringField()
    CrimeName = StringField()

    symmetric_contacts = ListField(DictField())
    symmetric_contact_codes= ListField(DictField())

    asymmetric_contacts = ListField(DictField())
    asymmetric_contact_codes=ListField(DictField())

    seq_id = StringField(required=True)


class WhatsAppGroups(Document):
    meta = {
        'collection': 'WhatsAppGroups',
        'db_alias': 'whatsapp_db',
        'strict': False,
        'indexes': [
            'seq_id',
            'TargetNo',
            'ID'
        ]
    }

    id = StringField(primary_key=True, db_field="_id")

    TargetNo = StringField()
    ID = StringField()
    Creation = StringField()
    Size = StringField()
    Subject = StringField()
    Description = StringField()

    seq_id = StringField(required=True)
