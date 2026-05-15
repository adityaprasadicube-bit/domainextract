from mongoengine import StringField
from rest_framework import serializers
from .ild_model import  ildNexus,ildRecord


class ildNexusSerializer(serializers.Serializer):
    _id = serializers.CharField()

    ILDNo = serializers.CharField(required=False, allow_blank=True)
    CrimeID = serializers.CharField(required=False, allow_blank=True)
    FileName = serializers.CharField(required=False, allow_blank=True)

    Duplicate = serializers.IntegerField(default=0, required=False)

    FromDate = serializers.DateTimeField(required=False)
    ToDate = serializers.DateTimeField(required=False)

    Inserted = serializers.IntegerField(default=0, required=False)

    RecordType = serializers.CharField(required=False, allow_blank=True)
    InsertedAt = serializers.DateTimeField(required=False)


    Day = serializers.IntegerField(required=False)
    Month = serializers.IntegerField(required=False)
    Year = serializers.IntegerField(required=False)

    def create(self, validated_data):
        return ildNexus(**validated_data).save()

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance


class ildRecordSerializer(serializers.Serializer):
    _id = serializers.CharField()

    ILD = serializers.CharField(source='A_Party',required=False, allow_blank=True)
    B_Party = serializers.CharField(required=False, allow_blank=True)
    Date = serializers.DateTimeField(source='SDate',required=False)
    Time = serializers.CharField(source='STime',required=False, allow_blank=True)

    Duration = serializers.IntegerField(default=0, required=False)
    Call_Type = serializers.CharField(required=False, allow_blank=True)

    IMEI = serializers.CharField(required=False, allow_blank=True)
    IMSI = serializers.CharField(required=False, allow_blank=True)

    Roaming_Circle = serializers.CharField(source='CIRCLE',required=False, allow_blank=True)
    Outgoing_Switch = serializers.IntegerField(source='ORIG_SWITCH_ID',required=False)


    Outgoing_TRUNK = serializers.CharField(source='ORG_TRUNC_GROUP',required=False, allow_blank=True)
    Incoming_TRUNK = serializers.CharField(source='TERM_TRUNC_GROUP',required=False, allow_blank=True)
    CALL_STATUS = serializers.CharField(required=False, allow_blank=True)

    # MongoEngine db_field handles "SMSC No" and "SW & MSC ID"
    SMSC_No = serializers.CharField(required=False, allow_blank=True)
    SW_MSC_ID = serializers.CharField(required=False, allow_blank=True)

    FileServiceType = serializers.CharField(required=False, allow_blank=True)


    First_CGI = serializers.CharField(required=False, allow_blank=True)
    Last_CGI =serializers.CharField(required=False, allow_blank=True)

    First_Lat = serializers.CharField(required=False, allow_blank=True)
    First_Long = serializers.CharField(required=False, allow_blank=True)

    IMEI_TAC = serializers.CharField(required=False, allow_blank=True)
    IMSI_CODE = serializers.CharField(required=False, allow_blank=True)

    Con_Type = serializers.CharField(required=False, allow_blank=True)
    CARRIER = serializers.CharField(required=False, allow_blank=True)

    SDateTime = serializers.DateTimeField(required=False)
    EDateTime = serializers.DateTimeField(required=False)

    a_country_code = serializers.CharField(required=False, allow_blank=True)
    b_country_code = serializers.CharField(required=False, allow_blank=True)

    b_mobile_code = serializers.CharField(required=False, allow_blank=True)
    a_mobile_code = serializers.CharField(required=False, allow_blank=True)

    seq_id = serializers.ListField(
        child=serializers.CharField(),
        required=False
    )

    def create(self, validated_data):
        return ildRecord(**validated_data).save()

    def update(self, instance, validated_data):
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()
        return instance