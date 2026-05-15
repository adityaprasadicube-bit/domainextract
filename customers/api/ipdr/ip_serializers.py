import socket
import ipaddress
from bson import Binary
from rest_framework import serializers
from decimal import Decimal, InvalidOperation


class SafeDecimalField(serializers.DecimalField):
    def to_representation(self, value):
        try:
            return super().to_representation(value)
        except (InvalidOperation, TypeError, ValueError):
            return None


class BinaryFieldSerializer(serializers.Field):
    def to_representation(self, value):
        if not isinstance(value, (bytes, bytearray, Binary)):
            return None
        raw = bytes(value)
        try:
            if len(raw) == 4:
                return socket.inet_ntop(socket.AF_INET, raw)
            elif len(raw) == 16:
                return socket.inet_ntop(socket.AF_INET6, raw)
        except OSError:
            return None
        return None


class IPDRRecordSerializer(serializers.Serializer):
    id = serializers.CharField()
    MSISDN = serializers.CharField(required=False, allow_null=True)
    MSISDN_code = serializers.CharField(required=False, allow_null=True)
    Destination_ip = serializers.CharField(required=False)
    Destination_port = serializers.IntegerField(required=False, allow_null=True)
    SDateTime = serializers.DateTimeField(required=False, allow_null=True)
    EDateTime = serializers.DateTimeField(required=False, allow_null=True)
    Duration = serializers.IntegerField(required=False, allow_null=True)
    TowerID = serializers.CharField(required=False, allow_null=True)
    IMEI = serializers.CharField(required=False, allow_null=True)
    IMEI_TAC = serializers.CharField(required=False, allow_null=True)
    IMSI = serializers.CharField(required=False, allow_null=True)
    IMSI_CODE = serializers.CharField(required=False, allow_null=True)
    DataUpload = serializers.CharField(required=False, allow_null=True)
    DataDownload = serializers.CharField(required=False, allow_null=True)
    Source_ip = serializers.CharField(required=False)
    Source_port = serializers.IntegerField(required=False, allow_null=True)
    Translated_ip = serializers.CharField(required=False)
    Translated_port = serializers.IntegerField(required=False, allow_null=True)
    seq_id = serializers.CharField()


class IPDataBaseSerializer(serializers.Serializer):
    id = BinaryFieldSerializer()
    continent = serializers.CharField(required=False)
    continentCode = serializers.CharField(required=False)
    country = serializers.CharField(required=False)
    countryCode = serializers.CharField(required=False)
    region = serializers.CharField(required=False)
    regionName = serializers.CharField(required=False)
    city = serializers.CharField(required=False)
    district = serializers.CharField(required=False)
    zip = serializers.CharField(required=False)
    lat = SafeDecimalField(max_digits=9, decimal_places=6, required=False)
    lon = SafeDecimalField(max_digits=9, decimal_places=6, required=False)
    timezone = serializers.CharField(required=False)
    offset = serializers.CharField(required=False)
    currency = serializers.CharField(required=False)
    isp = serializers.CharField(required=False)
    org = serializers.CharField(required=False)
    as_ = serializers.CharField(source="as", required=False)
    asname = serializers.CharField(required=False)
    reverse = serializers.CharField(required=False)
    type = serializers.CharField(required=False)
    query = BinaryFieldSerializer()
    recdate = serializers.CharField(required=False)


class IPDRNexusSerializer(serializers.Serializer):
    id = serializers.CharField()
    CrimeID = serializers.CharField()
    Day = serializers.IntegerField()
    Duplicate = serializers.IntegerField()
    FromDate = serializers.DateTimeField()
    IPDR = serializers.CharField()
    Inserted = serializers.IntegerField()
    InsertedAt = serializers.DateTimeField()
    MaxDur = serializers.IntegerField()
    MinDur = serializers.IntegerField()
    Month = serializers.IntegerField()
    RecordType = serializers.CharField()
    Skipped = serializers.IntegerField()
    ToDate = serializers.DateTimeField()
    UserAccessID = serializers.CharField()
    Year = serializers.IntegerField()
    Name = serializers.CharField()


class PortInfoSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    Port = serializers.IntegerField()
    Description = serializers.CharField(required=False, allow_blank=True)
    Category = serializers.CharField(required=False, allow_blank=True)
    Type = serializers.CharField(required=False, allow_blank=True)


class IPDRFilterSerializer(serializers.Serializer):
    seq_id = serializers.CharField()
    filter = serializers.BooleanField(required=False)
    from_date = serializers.DateTimeField(required=False)
    to_date = serializers.DateTimeField(required=False)
    min_duration = serializers.IntegerField(required=False)
    max_duration = serializers.IntegerField(required=False)
