from mongoengine import EmbeddedDocument, DateTimeField, IntField, StringField, BinaryField

class IPRequisitionResult(EmbeddedDocument):
    Destination_ip = BinaryField()
    Destination_port = IntField()
    SDateTime = DateTimeField()
    EDateTime = DateTimeField()
    Duration = IntField()
    DataUpload = StringField()
    DataDownload = StringField()
    ISP = StringField()
    AppHostname = StringField()
    PortInfo = StringField()
    PortCategory = StringField()
    PortType = StringField()
    Domains = StringField()
    Location = StringField()
    Country = StringField()
