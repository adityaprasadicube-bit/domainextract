from bson import Binary
from rest_framework import serializers
from decimal import Decimal, InvalidOperation
from .towerdump_models.towerdump_model import TowerDumpNexus


class SafeDecimalField(serializers.DecimalField):
    def to_representation(self, value):
        try:
            return super().to_representation(value)
        except (InvalidOperation, TypeError, ValueError):
            return None  # or 0 or float(value) if needed

class TowerDumpNexusSerializer(serializers.Serializer):
    id = serializers.CharField()
    Tower_id = serializers.CharField()
    CrimeID= serializers.CharField()
    UserAccessID = serializers.CharField()
    Day = serializers.IntegerField()
    Duplicate = serializers.IntegerField()
    FromDate = serializers.DateTimeField()
    Inserted = serializers.IntegerField()
    InsertedAt = serializers.DateTimeField()
    MaxDur = serializers.IntegerField()
    MinDur = serializers.IntegerField()
    Month = serializers.IntegerField()
    RecordType = serializers.CharField()
    Skipped = serializers.IntegerField()
    Tac_Or_Mobile_Code = serializers.CharField()
    ToDate = serializers.DateTimeField()
    Year = serializers.IntegerField()
    ImsiCode = serializers.CharField()

    def create(self, validated_data):
        # from .towerdump_models import TowerDumpNexus
        return TowerDumpNexus(**validated_data).save()

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance

class TowerDumpDetailRecordSerializer(serializers.Serializer):
    id = serializers.CharField()
    A_Party = serializers.CharField()
    a_mobile_code = serializers.CharField()
    B_Party = serializers.CharField()
    b_mobile_code = serializers.CharField()
    SDateTime = serializers.DateTimeField()
    EDateTime = serializers.DateTimeField()
    Duration = serializers.IntegerField()
    SDate = serializers.DateTimeField()
    STime = serializers.CharField()
    FileCallType = serializers.CharField()
    Call_Type = serializers.CharField()
    LRN = serializers.CharField()
    First_CGI = serializers.CharField()
    Last_CGI = serializers.CharField()
    IMEI = serializers.CharField()
    IMSI = serializers.CharField()
    IMSI_CODE = serializers.CharField()
    Con_Type = serializers.CharField()
    First_Lat = SafeDecimalField(max_digits=9, decimal_places=5, required=False)
    First_Long = SafeDecimalField(max_digits=9, decimal_places=5, required=False)
    Last_Lat = SafeDecimalField(max_digits=9, decimal_places=5, required=False)
    Last_Long = SafeDecimalField(max_digits=9, decimal_places=5, required=False)
    FileServiceType = serializers.CharField()
    IMEI_TAC = serializers.CharField()
    seq_id = serializers.CharField()


class TowerDumpFilterSerializer(serializers.Serializer):
    seq_id = serializers.CharField()
    filter = serializers.BooleanField(required=False)
    from_date = serializers.DateTimeField(required=False)
    to_date = serializers.DateTimeField(required=False)
    min_duration = serializers.IntegerField(required=False)
    max_duration = serializers.IntegerField(required=False)